from __future__ import annotations

import json
import os
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, IO

from filelock import FileLock, Timeout


SCHEMA_VERSION = 1
EVENT_TYPES = frozenset(
    {
        "session.created",
        "runtime.started",
        "runtime.stopped",
        "runtime.interrupted",
        "turn.started",
        "turn.completed",
        "turn.interrupted",
        "message.created",
        "context.compacted",
        "tool.started",
        "tool.completed",
        "tool.failed",
        "tool.interrupted",
        "permission.decided",
        "error.occurred",
        "model.request.started",
        "model.request.completed",
        "model.request.failed",
        "model.request.interrupted",
        "usage.recorded",
    }
)


class JournalWriteError(RuntimeError):
    pass


class JournalCorruptionError(RuntimeError):
    pass


@dataclass(frozen=True)
class JournalEvent:
    schema_version: int
    event_id: str
    session_id: str
    runtime_id: str | None
    turn_id: str | None
    sequence: int
    timestamp: str
    type: str
    payload: dict[str, Any]

    @classmethod
    def from_dict(cls, data: dict[str, Any], *, path: Path, line_number: int) -> JournalEvent:
        required = {
            "schema_version",
            "event_id",
            "session_id",
            "runtime_id",
            "turn_id",
            "sequence",
            "timestamp",
            "type",
            "payload",
        }
        missing = required.difference(data)
        if missing:
            names = ", ".join(sorted(missing))
            raise JournalCorruptionError(f"{path}: line {line_number} is missing fields: {names}")
        if data["schema_version"] != SCHEMA_VERSION:
            raise JournalCorruptionError(
                f"{path}: line {line_number} has unsupported schema_version {data['schema_version']}"
            )
        if not isinstance(data["payload"], dict):
            raise JournalCorruptionError(f"{path}: line {line_number} payload must be an object")
        try:
            return cls(**{key: data[key] for key in required})
        except TypeError as exc:
            raise JournalCorruptionError(f"{path}: line {line_number} has an invalid envelope") from exc


class SessionJournal:
    """Append-only Session event store with single-writer ownership."""

    def __init__(
        self,
        sessions_dir: str | Path,
        session_id: str,
        *,
        lock_timeout: float = 0,
    ) -> None:
        self.session_id = session_id
        self.path = Path(sessions_dir) / f"{session_id}.jsonl"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = FileLock(str(self.path) + ".lock")
        self._file: IO[str] | None = None
        self._closed = False

        try:
            self._lock.acquire(timeout=lock_timeout)
        except Timeout as exc:
            raise JournalWriteError(f"session journal lock is already held: {self.path}") from exc

        try:
            events = self.read_file(self.path)
            self._remove_truncated_tail()
            self._next_sequence = events[-1].sequence + 1 if events else 1
            self._open()
        except Exception:
            self._lock.release()
            raise

    def append(
        self,
        event_type: str,
        payload: dict[str, Any],
        runtime_id: str | None = None,
        turn_id: str | None = None,
    ) -> JournalEvent:
        if self._closed:
            raise JournalWriteError("session journal is closed")
        if event_type not in EVENT_TYPES:
            raise ValueError(f"unsupported journal event type: {event_type}")
        event = JournalEvent(
            schema_version=SCHEMA_VERSION,
            event_id=f"evt_{uuid.uuid4().hex}",
            session_id=self.session_id,
            runtime_id=runtime_id,
            turn_id=turn_id,
            sequence=self._next_sequence,
            timestamp=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            type=event_type,
            payload=payload,
        )
        line = json.dumps(asdict(event), ensure_ascii=False, separators=(",", ":")) + "\n"
        self._write_with_retry(line)
        self._next_sequence += 1
        return event

    def read(self) -> list[JournalEvent]:
        return self.read_file(self.path)

    @classmethod
    def read_file(cls, path: str | Path) -> list[JournalEvent]:
        journal_path = Path(path)
        if not journal_path.exists():
            return []
        raw = journal_path.read_bytes()
        if not raw:
            return []

        chunks = raw.splitlines(keepends=True)
        events: list[JournalEvent] = []
        expected_sequence = 1
        for index, chunk in enumerate(chunks, start=1):
            is_last = index == len(chunks)
            complete_line = chunk.endswith((b"\n", b"\r"))
            try:
                text = chunk.decode("utf-8", errors="strict").strip()
            except UnicodeDecodeError as exc:
                if is_last and not complete_line:
                    break
                raise JournalCorruptionError(
                    f"{journal_path}: invalid UTF-8 at line {index}"
                ) from exc
            if not text:
                continue
            try:
                data = json.loads(text)
            except json.JSONDecodeError as exc:
                if is_last and not complete_line:
                    break
                raise JournalCorruptionError(f"{journal_path}: invalid JSON at line {index}") from exc
            if not isinstance(data, dict):
                raise JournalCorruptionError(f"{journal_path}: line {index} must be a JSON object")
            event = JournalEvent.from_dict(data, path=journal_path, line_number=index)
            if event.sequence != expected_sequence:
                raise JournalCorruptionError(
                    f"{journal_path}: sequence gap at line {index}; "
                    f"expected {expected_sequence}, got {event.sequence}"
                )
            events.append(event)
            expected_sequence += 1
        return events

    def checkpoint(self) -> None:
        if self._closed or self._file is None:
            raise JournalWriteError("session journal is closed")
        try:
            self._file.flush()
            os.fsync(self._file.fileno())
        except OSError as exc:
            raise JournalWriteError(f"failed to checkpoint session journal: {self.path}") from exc

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            if self._file is not None:
                self._file.flush()
                self._file.close()
        finally:
            self._file = None
            self._lock.release()

    def __enter__(self) -> SessionJournal:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def _open(self) -> None:
        self._file = self.path.open("a", encoding="utf-8", newline="\n")

    def _write_with_retry(self, line: str) -> None:
        original_size = self.path.stat().st_size if self.path.exists() else 0
        last_error: OSError | None = None
        for attempt in range(2):
            try:
                if self._file is None:
                    self._open()
                assert self._file is not None
                self._file.write(line)
                self._file.flush()
                return
            except OSError as exc:
                last_error = exc
                if self._file is not None:
                    try:
                        self._file.close()
                    except OSError:
                        pass
                self._file = None
                try:
                    with self.path.open("r+b") as stream:
                        stream.truncate(original_size)
                except OSError as rollback_error:
                    raise JournalWriteError(
                        f"failed to restore session journal after write error: {self.path}"
                    ) from rollback_error
                if attempt == 0:
                    continue
        raise JournalWriteError(f"failed to append session journal: {self.path}") from last_error

    def _remove_truncated_tail(self) -> None:
        if not self.path.exists():
            return
        raw = self.path.read_bytes()
        if not raw or raw.endswith((b"\n", b"\r")):
            return
        tail_start = raw.rfind(b"\n") + 1
        tail = raw[tail_start:]
        try:
            json.loads(tail.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            with self.path.open("r+b") as stream:
                stream.truncate(tail_start)
