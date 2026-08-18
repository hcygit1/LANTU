from __future__ import annotations

import json
import random
import string
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

from lantu.conversation import (
    ConversationManager,
    Message,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
)
from lantu.memory.journal import JournalEvent, SessionJournal
from lantu.memory.file_ledger import FileLedger


SESSIONS_DIR = ".lantu/sessions"
TITLE_MAX_LENGTH = 50
SESSION_SUMMARY_PROMPT = (
    "你是一个对话摘要助手。请根据下面的对话内容，用一句话总结这个会话的主要内容。"
    "只输出摘要文本，不要加任何前缀或标点符号外的修饰。不要调用任何工具。"
)


def _lantu_version() -> str:
    try:
        return version("lantu")
    except PackageNotFoundError:
        return "0.2.0"


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _parse_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


@dataclass
class SessionMeta:
    id: str
    title: str = ""
    summary: str = ""
    message_count: int = 0
    total_tokens: int = 0
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    last_active: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def save(self, path: Path) -> None:
        data = {
            "id": self.id,
            "title": self.title,
            "summary": self.summary,
            "message_count": self.message_count,
            "total_tokens": self.total_tokens,
            "created_at": self.created_at.isoformat(),
            "last_active": self.last_active.isoformat(),
        }
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> SessionMeta | None:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return cls(
                id=data["id"],
                title=data.get("title", ""),
                summary=data.get("summary", ""),
                message_count=data.get("message_count", 0),
                total_tokens=data.get("total_tokens", 0),
                created_at=datetime.fromisoformat(data["created_at"]),
                last_active=datetime.fromisoformat(data["last_active"]),
            )
        except (OSError, json.JSONDecodeError, KeyError, ValueError):
            return None


@dataclass(frozen=True)
class ExecutionEvent:
    event_type: str
    payload: dict[str, Any]


@dataclass
class ResumeResult:
    session: Session
    messages: list[Message]
    last_active: datetime


class Session:
    """Session lifecycle and Conversation projection over a SessionJournal."""

    def __init__(
        self,
        journal: SessionJournal,
        meta: SessionMeta,
        sessions_dir: Path,
        *,
        pending_runtime_id: str | None = None,
        loaded_tool_states: list[dict[str, str]] | None = None,
        file_ledger: FileLedger | None = None,
        schema_epoch: dict[str, Any] | None = None,
    ) -> None:
        self.session_id = journal.session_id
        self.journal = journal
        self.meta = meta
        self._sessions_dir = sessions_dir
        self.runtime_id: str | None = None
        self.turn_id: str | None = None
        self._pending_runtime_id = pending_runtime_id
        self.loaded_tool_states = loaded_tool_states or []
        self.file_ledger = file_ledger or FileLedger()
        self.schema_epoch = dict(schema_epoch) if schema_epoch else None
        self._message_ids: dict[int, str] = {}
        self._closed = False

    @property
    def closed(self) -> bool:
        return self.journal.closed

    def start_runtime(self, mode: str) -> str:
        if self.runtime_id is not None:
            raise RuntimeError("runtime is already active")
        if mode not in {"new", "resume"}:
            raise ValueError(f"invalid runtime mode: {mode}")
        self.runtime_id = self._pending_runtime_id or _new_id("runtime")
        self._pending_runtime_id = None
        self.journal.append(
            "runtime.started",
            {
                "mode": mode,
                "work_dir": str(self._sessions_dir.parent.parent),
                "lantu_version": _lantu_version(),
            },
            runtime_id=self.runtime_id,
        )
        return self.runtime_id

    def start_turn(self, trigger: str) -> str:
        if self.runtime_id is None:
            raise RuntimeError("cannot start a turn without an active runtime")
        if self.turn_id is not None:
            raise RuntimeError("turn is already active")
        if trigger not in {"user", "notification", "recovery"}:
            raise ValueError(f"invalid turn trigger: {trigger}")
        self.turn_id = _new_id("turn")
        self.journal.append(
            "turn.started",
            {"trigger": trigger},
            runtime_id=self.runtime_id,
            turn_id=self.turn_id,
        )
        return self.turn_id

    def commit_message(self, message: Message) -> JournalEvent:
        if self.runtime_id is None or self.turn_id is None:
            raise RuntimeError("messages require an active runtime and turn")
        message_id = _new_id("msg")
        payload = _message_payload(message, message_id)
        event = self.journal.append(
            "message.created",
            payload,
            runtime_id=self.runtime_id,
            turn_id=self.turn_id,
        )
        self._message_ids[id(message)] = message_id
        self.meta.message_count += 1
        self.meta.last_active = _parse_timestamp(event.timestamp)
        if not self.meta.title and message.role == "user" and message.content:
            self.meta.title = message.content[:TITLE_MAX_LENGTH]
        self._save_meta_cache()
        return event

    def append(self, message: Message) -> JournalEvent:
        return self.commit_message(message)

    def record(self, event: ExecutionEvent) -> JournalEvent:
        if self.runtime_id is None:
            raise RuntimeError("execution events require an active runtime")
        journal_event = self.journal.append(
            event.event_type,
            event.payload,
            runtime_id=self.runtime_id,
            turn_id=self.turn_id,
        )
        if event.event_type == "tool.schema.epoch.changed":
            self.schema_epoch = dict(event.payload)
        return journal_event

    def context_compacted(self, summary: str, keep: list[Message]) -> JournalEvent:
        if self.runtime_id is None or self.turn_id is None:
            raise RuntimeError("context compaction requires an active turn")
        kept_message_ids = [
            self._message_ids[id(message)]
            for message in keep
            if id(message) in self._message_ids
        ]
        event = self.journal.append(
            "context.compacted",
            {"summary": summary, "kept_message_ids": kept_message_ids},
            runtime_id=self.runtime_id,
            turn_id=self.turn_id,
        )
        self.meta.summary = summary
        self.meta.last_active = _parse_timestamp(event.timestamp)
        self._save_meta_cache()
        return event

    def complete_turn(self, iteration_count: int) -> None:
        if self.runtime_id is None or self.turn_id is None:
            raise RuntimeError("no active turn")
        self.journal.append(
            "turn.completed",
            {"iteration_count": iteration_count},
            runtime_id=self.runtime_id,
            turn_id=self.turn_id,
        )
        self.journal.checkpoint()
        self.turn_id = None

    def interrupt_turn(self, reason: str, last_iteration: int = 0) -> None:
        if self.runtime_id is None or self.turn_id is None:
            return
        self.journal.append(
            "turn.interrupted",
            {"reason": reason, "last_iteration": last_iteration},
            runtime_id=self.runtime_id,
            turn_id=self.turn_id,
        )
        self.journal.checkpoint()
        self.turn_id = None

    def stop_runtime(self, reason: str) -> None:
        if self.runtime_id is None:
            return
        if self.turn_id is not None:
            self.interrupt_turn("runtime_stopped")
        self.journal.append(
            "runtime.stopped",
            {"reason": reason},
            runtime_id=self.runtime_id,
        )
        self.journal.checkpoint()
        self.runtime_id = None

    def update_total_tokens(self, total: int) -> None:
        self.meta.total_tokens = total
        self.meta.last_active = datetime.now(timezone.utc)
        self._save_meta_cache()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self.runtime_id is not None:
            self.stop_runtime("user_exit")
        self.journal.close()

    def _save_meta_cache(self) -> None:
        try:
            self.meta.save(self._sessions_dir / f"{self.session_id}.meta")
        except OSError:
            pass

    def _register_projected_messages(self, projected: list[tuple[str, Message]]) -> None:
        for message_id, message in projected:
            self._message_ids[id(message)] = message_id


class SessionManager:
    def __init__(self, work_dir: str) -> None:
        self._work_dir = Path(work_dir).resolve()
        self._sessions_dir = self._work_dir / SESSIONS_DIR
        self._sessions_dir.mkdir(parents=True, exist_ok=True)

    def create(self) -> Session:
        session_id = _generate_session_id()
        journal = SessionJournal(self._sessions_dir, session_id)
        event = journal.append(
            "session.created",
            {"project_root": str(self._work_dir), "lantu_version": _lantu_version()},
        )
        created_at = _parse_timestamp(event.timestamp)
        meta = SessionMeta(id=session_id, created_at=created_at, last_active=created_at)
        session = Session(journal, meta, self._sessions_dir, file_ledger=FileLedger())
        session._save_meta_cache()
        return session

    def list(self) -> list[SessionMeta]:
        metas: list[SessionMeta] = []
        for jsonl_path in self._sessions_dir.glob("*.jsonl"):
            meta_path = jsonl_path.with_suffix(".meta")
            meta = SessionMeta.load(meta_path)
            if meta is None:
                meta = _meta_from_events(SessionJournal.read_file(jsonl_path))
                if meta is not None:
                    try:
                        meta.save(meta_path)
                    except OSError:
                        pass
            if meta is not None:
                metas.append(meta)
        metas.sort(key=lambda item: item.last_active, reverse=True)
        return metas

    def resume(self, session_id: str) -> ResumeResult | None:
        jsonl_path = self._sessions_dir / f"{session_id}.jsonl"
        if not jsonl_path.exists():
            return None
        pending_runtime_id = _new_id("runtime")
        journal = SessionJournal(self._sessions_dir, session_id)
        try:
            events = journal.read()
            _append_interruption_events(journal, events, pending_runtime_id)
            events = journal.read()
            projected = _project_messages(events)
            meta = _meta_from_events(events)
            if meta is None:
                journal.close()
                return None
            session = Session(
                journal,
                meta,
                self._sessions_dir,
                pending_runtime_id=pending_runtime_id,
                loaded_tool_states=_tool_schema_states(events),
                file_ledger=_file_ledger_from_events(events),
                schema_epoch=_schema_epoch_from_events(events),
            )
            session._register_projected_messages(projected)
            session._save_meta_cache()
            return ResumeResult(
                session=session,
                messages=[message for _, message in projected],
                last_active=meta.last_active,
            )
        except Exception:
            journal.close()
            raise

    def delete(self, session_id: str) -> bool:
        deleted = False
        for suffix in (".jsonl", ".meta", ".jsonl.lock"):
            path = self._sessions_dir / f"{session_id}{suffix}"
            if path.exists():
                path.unlink()
                deleted = True
        return deleted


def _generate_session_id() -> str:
    now = datetime.now()
    suffix = "".join(random.choices(string.ascii_lowercase + string.digits, k=4))
    return f"session_{now.strftime('%Y%m%d_%H%M%S')}_{suffix}"


def _message_payload(message: Message, message_id: str) -> dict[str, Any]:
    payload = {
        "message_id": message_id,
        "role": message.role,
        "content": message.content,
        "tool_uses": [
            {
                "tool_call_id": item.tool_use_id,
                "tool_name": item.tool_name,
                "arguments": item.arguments,
            }
            for item in message.tool_uses
        ],
        "tool_results": [
            {
                "tool_call_id": item.tool_use_id,
                "content": item.content,
                "is_error": item.is_error,
            }
            for item in message.tool_results
        ],
        "thinking_blocks": [
            {"thinking": item.thinking, "signature": item.signature}
            for item in message.thinking_blocks
        ],
    }
    if message.reminder_key is not None:
        payload["reminder_key"] = message.reminder_key
        payload["reminder_hash"] = message.reminder_hash
    return payload


def _message_from_payload(payload: dict[str, Any]) -> Message:
    return Message(
        role=str(payload.get("role", "user")),
        content=str(payload.get("content", "")),
        tool_uses=[
            ToolUseBlock(
                tool_use_id=str(item.get("tool_call_id", "")),
                tool_name=str(item.get("tool_name", "")),
                arguments=item.get("arguments", {}),
            )
            for item in payload.get("tool_uses", [])
            if isinstance(item, dict)
        ],
        tool_results=[
            ToolResultBlock(
                tool_use_id=str(item.get("tool_call_id", "")),
                content=str(item.get("content", "")),
                is_error=bool(item.get("is_error", False)),
            )
            for item in payload.get("tool_results", [])
            if isinstance(item, dict)
        ],
        thinking_blocks=[
            ThinkingBlock(
                thinking=str(item.get("thinking", "")),
                signature=str(item.get("signature", "")),
            )
            for item in payload.get("thinking_blocks", [])
            if isinstance(item, dict)
        ],
        reminder_key=(
            str(payload["reminder_key"])
            if payload.get("reminder_key") is not None
            else None
        ),
        reminder_hash=(
            str(payload["reminder_hash"])
            if payload.get("reminder_hash") is not None
            else None
        ),
    )


def _project_messages(events: list[JournalEvent]) -> list[tuple[str, Message]]:
    projected: list[tuple[str, Message]] = []
    messages: dict[str, Message] = {}
    for event in events:
        if event.type == "message.created":
            message_id = str(event.payload.get("message_id", ""))
            if not message_id:
                continue
            message = _message_from_payload(event.payload)
            messages[message_id] = message
            projected.append((message_id, message))
        elif event.type == "context.compacted":
            summary = str(event.payload.get("summary", ""))
            compacted: list[tuple[str, Message]] = []
            if summary:
                compacted.append(
                    (
                        f"summary_{event.event_id}",
                        Message(
                            role="user",
                            content=(
                                "本次会话延续自之前的对话，因上下文空间不足进行了压缩。"
                                "以下是早期对话的摘要：\n\n" + summary
                            ),
                        ),
                    )
                )
            for message_id in event.payload.get("kept_message_ids", []):
                message = messages.get(str(message_id))
                if message is not None:
                    compacted.append((str(message_id), message))
            projected = compacted
    return projected


def _meta_from_events(events: list[JournalEvent]) -> SessionMeta | None:
    created = next((event for event in events if event.type == "session.created"), None)
    if created is None:
        return None
    created_at = _parse_timestamp(created.timestamp)
    meta = SessionMeta(
        id=created.session_id,
        created_at=created_at,
        last_active=created_at,
    )
    for event in events:
        meta.last_active = _parse_timestamp(event.timestamp)
        if event.type == "message.created":
            meta.message_count += 1
            if (
                not meta.title
                and event.payload.get("role") == "user"
                and event.payload.get("content")
            ):
                meta.title = str(event.payload["content"])[:TITLE_MAX_LENGTH]
        elif event.type == "context.compacted":
            meta.summary = str(event.payload.get("summary", ""))
        elif event.type == "usage.recorded":
            meta.total_tokens += sum(
                int(event.payload.get(name, 0) or 0)
                for name in (
                    "input_tokens",
                    "output_tokens",
                    "cache_read_tokens",
                    "cache_creation_tokens",
                )
            )
    return meta


def _tool_schema_states(events: list[JournalEvent]) -> list[dict[str, str]]:
    """Replay deferred-tool Schema activation events in Journal order."""
    states: list[dict[str, str]] = []
    seen: set[str] = set()
    for event in events:
        if event.type != "tool.schema.loaded":
            continue
        for raw_state in event.payload.get("tools", []):
            if not isinstance(raw_state, dict):
                continue
            name = raw_state.get("name")
            schema_hash = raw_state.get("schema_hash")
            if not isinstance(name, str) or not isinstance(schema_hash, str):
                continue
            if name in seen:
                continue
            states.append({"name": name, "schema_hash": schema_hash})
            seen.add(name)
    return states


def _file_ledger_from_events(events: list[JournalEvent]) -> FileLedger:
    """Replay the latest file observation/update for each path."""
    ledger = FileLedger()
    for event in events:
        if event.type not in {"file.observed", "file.updated"}:
            continue
        ledger.apply_payload(event.payload)
    return ledger


def _schema_epoch_from_events(events: list[JournalEvent]) -> dict[str, Any] | None:
    """Return the latest tool-schema Epoch payload for Session resume."""
    latest: dict[str, Any] | None = None
    for event in events:
        if event.type == "tool.schema.epoch.changed":
            latest = dict(event.payload)
    return latest


def _append_interruption_events(
    journal: SessionJournal,
    events: list[JournalEvent],
    detected_by_runtime_id: str,
) -> None:
    active_runtimes: dict[str, JournalEvent] = {}
    active_turns: dict[str, JournalEvent] = {}
    active_tools: dict[str, JournalEvent] = {}
    active_models: dict[str, JournalEvent] = {}
    for event in events:
        if event.type == "runtime.started" and event.runtime_id:
            active_runtimes[event.runtime_id] = event
        elif event.type in {"runtime.stopped", "runtime.interrupted"} and event.runtime_id:
            active_runtimes.pop(event.runtime_id, None)
        elif event.type == "turn.started" and event.turn_id:
            active_turns[event.turn_id] = event
        elif event.type in {"turn.completed", "turn.interrupted"} and event.turn_id:
            active_turns.pop(event.turn_id, None)
        elif event.type == "tool.started":
            call_id = str(event.payload.get("tool_call_id", ""))
            if call_id:
                active_tools[call_id] = event
        elif event.type in {"tool.completed", "tool.failed", "tool.interrupted"}:
            call_id = str(event.payload.get("tool_call_id", ""))
            active_tools.pop(call_id, None)
        elif event.type == "model.request.started":
            call_id = str(event.payload.get("model_call_id", ""))
            if call_id:
                active_models[call_id] = event
        elif event.type in {
            "model.request.completed",
            "model.request.failed",
            "model.request.interrupted",
        }:
            call_id = str(event.payload.get("model_call_id", ""))
            active_models.pop(call_id, None)

    for runtime_id in active_runtimes:
        journal.append(
            "runtime.interrupted",
            {
                "reason": "missing_runtime_stopped",
                "detected_by_runtime_id": detected_by_runtime_id,
            },
            runtime_id=runtime_id,
        )
    for turn_id, started in active_turns.items():
        journal.append(
            "turn.interrupted",
            {"reason": "runtime_interrupted", "last_iteration": 0},
            runtime_id=started.runtime_id,
            turn_id=turn_id,
        )
    for call_id, started in active_tools.items():
        journal.append(
            "tool.interrupted",
            {
                "tool_call_id": call_id,
                "tool_name": started.payload.get("tool_name", ""),
                "reason": "runtime_interrupted",
                "result_known": False,
            },
            runtime_id=started.runtime_id,
            turn_id=started.turn_id,
        )
        journal.append(
            "message.created",
            {
                "message_id": _new_id("msg"),
                "role": "user",
                "content": "",
                "tool_uses": [],
                "tool_results": [
                    {
                        "tool_call_id": call_id,
                        "content": "Tool execution was interrupted; the final result is unknown.",
                        "is_error": True,
                    }
                ],
                "thinking_blocks": [],
            },
            runtime_id=started.runtime_id,
            turn_id=started.turn_id,
        )
    for call_id, started in active_models.items():
        journal.append(
            "model.request.interrupted",
            {
                "model_call_id": call_id,
                "reason": "runtime_interrupted",
                "result_known": False,
            },
            runtime_id=started.runtime_id,
            turn_id=started.turn_id,
        )


async def generate_session_summary(
    client: Any, conversation: ConversationManager, protocol: str
) -> str:
    from lantu.tools.base import StreamEnd, TextDelta

    recent = conversation.history[-10:]
    if not recent:
        return ""
    summary_conv = ConversationManager()
    summary_conv.history = [Message(role="user", content=SESSION_SUMMARY_PROMPT)]
    summary_conv.history.extend(recent)
    summary_conv.history.append(
        Message(role="user", content="请用一句话总结上面的对话内容。不要调用工具。")
    )
    collected = ""
    try:
        async for event in client.stream(summary_conv, system=SESSION_SUMMARY_PROMPT):
            if isinstance(event, TextDelta):
                collected += event.text
            elif isinstance(event, StreamEnd):
                pass
    except Exception:
        return ""
    return collected.strip()
