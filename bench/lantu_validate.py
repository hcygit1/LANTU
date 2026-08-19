"""Offline cache validation for Lantu's standard tool-loading mode.

The benchmark drives the real :class:`lantu.agent.Agent` and real read/search
tools through a fixed code-reading session. Only the model is scripted, so the
request payloads are deterministic and no API key or network is required.

Run from the repository root::

    uv run python bench/lantu_validate.py
    uv run python bench/lantu_validate.py --turns 6
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, AsyncIterator

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from lantu.agent import Agent, CompactNotification, ErrorEvent, LoopComplete
from lantu.client import LLMClient
from lantu.context.manager import COMPACTION_INSTRUCTION
from lantu.conversation import ConversationManager, Message
from lantu.memory.file_ledger import FileLedger
from lantu.tools import create_default_registry
from lantu.tools.base import StreamEnd, StreamEvent, TextDelta, ToolCallComplete

RESULTS_DIR = Path(__file__).resolve().parent / "results"
CHARS_PER_TOKEN = 3.5


@dataclass(frozen=True)
class BenchCall:
    tool_name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class BenchTurn:
    user: str
    calls: tuple[BenchCall, ...]
    answer: str


@dataclass
class RequestRecord:
    kind: str
    prompt_chars: int
    prompt_tokens: int
    reusable_prefix_chars: int = 0
    reusable_prefix_tokens: int = 0


@dataclass
class BenchmarkSession:
    session_id: str = "lantu-offline-benchmark"
    events: list[Any] | None = None
    file_ledger: FileLedger | None = None

    def __post_init__(self) -> None:
        self.events = []
        self.file_ledger = FileLedger()

    def record(self, event: Any) -> None:
        assert self.events is not None
        self.events.append(event)


class ScriptedClient(LLMClient):
    """Return fixed model actions while recording the actual request shape."""

    model = "lantu-offline-benchmark"

    def __init__(self, responses: list[list[StreamEvent]]) -> None:
        self.responses = list(responses)
        self.requests: list[RequestRecord] = []
        self._previous_payload = ""

    @staticmethod
    def _payload(
        conversation: ConversationManager,
        system: str,
        tools: list[dict[str, Any]] | None,
    ) -> str:
        messages = [
            {
                "role": message.role,
                "content": message.content,
                "tool_uses": [asdict(item) for item in message.tool_uses],
                "tool_results": [asdict(item) for item in message.tool_results],
                "thinking_blocks": [asdict(item) for item in message.thinking_blocks],
            }
            for message in conversation.get_messages()
        ]
        return json.dumps(
            {"system": system, "tools": tools or [], "messages": messages},
            ensure_ascii=False,
            separators=(",", ":"),
        )

    async def stream(
        self,
        conversation: ConversationManager,
        system: str = "",
        tools: list[dict[str, Any]] | None = None,
    ) -> AsyncIterator[StreamEvent]:
        payload = self._payload(conversation, system, tools)
        common = 0
        for left, right in zip(self._previous_payload, payload):
            if left != right:
                break
            common += 1
        self._previous_payload = payload
        kind = (
            "summary"
            if conversation.history
            and conversation.history[-1].content == COMPACTION_INSTRUCTION
            else "model"
        )
        self.requests.append(
            RequestRecord(
                kind=kind,
                prompt_chars=len(payload),
                prompt_tokens=max(1, int(len(payload) / CHARS_PER_TOKEN)),
                reusable_prefix_chars=common,
                reusable_prefix_tokens=int(common / CHARS_PER_TOKEN),
            )
        )

        if kind == "summary":
            yield TextDelta(
                "## Long-term goal\nPreserve the user's goal.\n\n"
                "## Constraints and confirmed decisions\nNone\n\n"
                "## Completed work\nNone\n\n"
                "## Outstanding work\nNone\n\n"
                "## Key files and code state\nPreserve the current Lantu architecture.\n\n"
                "## Historical problems and resolutions\nNone"
            )
            yield StreamEnd("end_turn", input_tokens=1, output_tokens=20)
            return

        if not self.responses:
            raise RuntimeError("benchmark response script exhausted")
        for event in self.responses.pop(0):
            yield event


def _read(path: str, offset: int = 0, limit: int = 220) -> BenchCall:
    return BenchCall(
        "ReadFile",
        {"file_path": path, "offset": offset, "limit": limit},
    )


SESSION: tuple[BenchTurn, ...] = (
    BenchTurn(
        "Where is the Lantu Agent entrypoint and main loop?",
        (BenchCall("Grep", {"pattern": "class Agent", "path": "lantu"}),),
        "The Agent class and its loop are implemented in lantu/agent.py.",
    ),
    BenchTurn(
        "Show the Agent constructor and the start of its run loop.",
        (_read("lantu/agent.py", 294, 220),),
        "Agent initialization prepares the registry, session, ledger, and stable prompt.",
    ),
    BenchTurn(
        "How are context messages stored and token estimates calculated?",
        (_read("lantu/conversation.py", 1, 220),),
        "ConversationManager stores append-only messages and estimates unanchored tokens.",
    ),
    BenchTurn(
        "Read the same Agent section again and explain what remains stable.",
        (_read("lantu/agent.py", 294, 220),),
        "The system prompt and standard tool schema remain stable across the loop.",
    ),
    BenchTurn(
        "How does the standard tool registry build schemas?",
        (_read("lantu/tools/__init__.py", 1, 260),),
        "ToolRegistry returns a fixed visible schema set in standard mode.",
    ),
    BenchTurn(
        "How does FileLedger record versions and visible line ranges?",
        (_read("lantu/memory/file_ledger.py", 1, 220),),
        "FileLedger tracks hashes, versions, observations, and visible ranges.",
    ),
    BenchTurn(
        "How are tool results shortened or persisted before entering history?",
        (_read("lantu/context/manager.py", 1, 220),),
        "Large results are persisted and represented by a bounded preview.",
    ),
    BenchTurn(
        "How does the session journal store and restore messages?",
        (_read("lantu/memory/session.py", 260, 220),),
        "The journal appends execution events and restores the latest session state.",
    ),
    BenchTurn(
        "Read the file-ledger integration around tool result preparation.",
        (_read("lantu/agent.py", 1260, 190),),
        "Agent deduplicates visible ranges and records file observations around results.",
    ),
    BenchTurn(
        "How is automatic context compaction triggered and rebuilt?",
        (_read("lantu/context/manager.py", 700, 220),),
        "Compaction summarizes old history, keeps recent messages, and rebuilds context.",
    ),
    BenchTurn(
        "Read the context manager compaction section again and compare it with the previous turn.",
        (_read("lantu/context/manager.py", 700, 220),),
        "The compaction request is separate; the rebuilt main prefix remains stable.",
    ),
    BenchTurn(
        "Where are model usage and schema epoch events recorded?",
        (_read("lantu/agent.py", 1160, 150),),
        "Agent records model usage and associates model requests with a schema epoch.",
    ),
)


def _responses(turns: tuple[BenchTurn, ...]) -> list[list[StreamEvent]]:
    responses: list[list[StreamEvent]] = []
    tool_id = 0
    for turn in turns:
        calls: list[ToolCallComplete] = []
        for call in turn.calls:
            tool_id += 1
            calls.append(ToolCallComplete(f"bench-{tool_id}", call.tool_name, call.arguments))
        responses.append(
            [
                *calls,
                StreamEnd("end_turn", input_tokens=1, output_tokens=8),
            ]
        )
        responses.append(
            [TextDelta(turn.answer), StreamEnd("end_turn", input_tokens=1, output_tokens=12)]
        )
    return responses


def _count_events(session: BenchmarkSession, event_type: str) -> int:
    return sum(
        1 for event in session.events or [] if getattr(event, "event_type", "") == event_type
    )


async def run_session(
    root: Path,
    turns: tuple[BenchTurn, ...],
    *,
    compact_after: int | None = None,
) -> dict[str, Any]:
    client = ScriptedClient(_responses(turns))
    session = BenchmarkSession()
    registry = create_default_registry(work_dir=str(root), loading_mode="standard")
    agent = Agent(
        client=client,
        registry=registry,
        protocol="openai-compat",
        work_dir=str(root),
        context_window=1_000_000,
        session=session,
    )
    conversation = ConversationManager()
    errors: list[str] = []
    compactions: list[dict[str, Any]] = []
    compaction_model_indices: list[int] = []

    for index, turn in enumerate(turns, start=1):
        conversation.add_user_message(turn.user)
        async for event in agent.run(conversation):
            if isinstance(event, ErrorEvent):
                errors.append(event.message)
            if isinstance(event, CompactNotification):
                if event.boundary is not None:
                    compactions.append({"turn": index, "before_tokens": event.before_tokens})
        if compact_after == index:
            model_request_count = sum(
                1 for request in client.requests if request.kind == "model"
            )
            compacted = await agent.manual_compact(conversation)
            if isinstance(compacted, CompactNotification) and compacted.boundary is not None:
                compactions.append({"turn": index, "before_tokens": compacted.before_tokens})
                compaction_model_indices.append(model_request_count)
            elif isinstance(compacted, ErrorEvent):
                errors.append(compacted.message)

    model_requests = [request for request in client.requests if request.kind == "model"]
    duplicate_pointers = sum(
        1
        for message in conversation.history
        for result in message.tool_results
        if "already visible in the conversation" in result.content
    )
    persisted_results = sum(
        1
        for message in conversation.history
        for result in message.tool_results
        if "<persisted-output>" in result.content
    )
    compaction_recovery = []
    for request_index in compaction_model_indices:
        first = model_requests[request_index] if request_index < len(model_requests) else None
        next_request = (
            model_requests[request_index + 1]
            if request_index + 1 < len(model_requests)
            else None
        )
        compaction_recovery.append(
            {
                "first_request_reusable_prefix_tokens": (
                    first.reusable_prefix_tokens if first else 0
                ),
                "next_request_reusable_prefix_tokens": (
                    next_request.reusable_prefix_tokens if next_request else 0
                ),
            }
        )
    reusable = sum(request.reusable_prefix_tokens for request in model_requests[1:])
    prompt = sum(request.prompt_tokens for request in model_requests)
    return {
        "turns": len(turns),
        "model_requests": len(model_requests),
        "prompt_tokens": prompt,
        "reusable_prefix_tokens": reusable,
        "reusable_prefix_fraction": round(reusable / max(1, prompt), 4),
        "duplicate_reads_avoided": duplicate_pointers,
        "persisted_tool_results": persisted_results,
        "compactions": compactions,
        "compaction_recovery": compaction_recovery,
        "ledger_file_entries": len(session.file_ledger.entries()),
        "usage_events": _count_events(session, "usage.recorded"),
        "errors": errors,
        "requests": [asdict(request) for request in client.requests],
    }


def run_benchmark(root: Path, turns: int) -> dict[str, Any]:
    selected = SESSION[:turns]
    if not selected:
        raise ValueError("turns must be positive")
    return {
        "meta": {
            "root": str(root),
            "mode": "standard",
            "repo_map": "disabled",
            "network": False,
            "token_estimator": "serialized characters / 3.5",
            "reusable_prefix_is_prediction": True,
        },
        "main": asyncio.run(run_session(root, selected)),
        "forced_compaction": asyncio.run(
            run_session(root, selected, compact_after=min(6, max(1, len(selected) - 1)))
        )
        if len(selected) >= 6
        else None,
    }


def _report(data: dict[str, Any]) -> str:
    lines = [
        "Lantu offline cache validation",
        f"mode: {data['meta']['mode']} | RepoMap: {data['meta']['repo_map']}",
    ]
    for name in ("main", "forced_compaction"):
        result = data.get(name)
        if result is None:
            continue
        lines.extend(
            [
                "",
                f"[{name}]",
                f"turns: {result['turns']}",
                f"model requests: {result['model_requests']}",
                f"estimated prompt tokens: {result['prompt_tokens']}",
                f"predicted reusable prefix tokens: {result['reusable_prefix_tokens']} "
                f"({result['reusable_prefix_fraction']:.1%})",
                f"duplicate reads avoided: {result['duplicate_reads_avoided']}",
                f"persisted tool results: {result['persisted_tool_results']}",
                f"compactions: {len(result['compactions'])}",
                f"compaction prefix recovery: {result['compaction_recovery']}",
                f"errors: {len(result['errors'])}",
            ]
        )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--turns", type=int, default=len(SESSION))
    args = parser.parse_args()
    root = args.root.resolve()
    previous = Path.cwd()
    os.chdir(root)
    try:
        data = run_benchmark(root, args.turns)
    finally:
        os.chdir(previous)

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    (RESULTS_DIR / "lantu_validation.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (RESULTS_DIR / "lantu_validation.txt").write_text(
        _report(data) + "\n", encoding="utf-8"
    )
    print(_report(data))


if __name__ == "__main__":
    main()
