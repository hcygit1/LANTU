from __future__ import annotations

import json
from pathlib import Path

import pytest

from lantu.memory.journal import (
    JournalCorruptionError,
    JournalWriteError,
    SessionJournal,
)


def _event(sequence: int, event_type: str) -> dict[str, object]:
    return {
        "schema_version": 1,
        "event_id": f"evt_{sequence}",
        "session_id": "session_test",
        "runtime_id": "runtime_test",
        "turn_id": None,
        "sequence": sequence,
        "timestamp": "2026-08-05T00:00:00Z",
        "type": event_type,
        "payload": {},
    }


def test_append_assigns_ordered_envelopes(tmp_path: Path) -> None:
    journal = SessionJournal(tmp_path, "session_test")
    try:
        first = journal.append("session.created", {"project_root": "D:/repo"})
        second = journal.append("turn.started", {"trigger": "user"}, turn_id="turn_1")
    finally:
        journal.close()

    assert first.sequence == 1
    assert second.sequence == 2
    assert second.session_id == "session_test"
    assert second.turn_id == "turn_1"
    assert journal.read() == [first, second]


def test_reader_ignores_only_truncated_final_line(tmp_path: Path) -> None:
    journal = SessionJournal(tmp_path, "session_test")
    journal.append("session.created", {})
    journal.close()
    path = tmp_path / "session_test.jsonl"
    with path.open("ab") as stream:
        stream.write(b'{"schema_version": 1, "sequence": 2')

    assert len(SessionJournal.read_file(path)) == 1


def test_reopen_removes_truncated_tail_before_appending(tmp_path: Path) -> None:
    first = SessionJournal(tmp_path, "session_test")
    first.append("session.created", {})
    first.close()
    path = tmp_path / "session_test.jsonl"
    with path.open("ab") as stream:
        stream.write(b'{"schema_version":1,"sequence":2')

    second = SessionJournal(tmp_path, "session_test")
    try:
        event = second.append("runtime.started", {"mode": "resume"})
    finally:
        second.close()

    assert event.sequence == 2
    assert [item.type for item in SessionJournal.read_file(path)] == [
        "session.created",
        "runtime.started",
    ]


def test_reader_rejects_middle_corruption(tmp_path: Path) -> None:
    path = tmp_path / "session_test.jsonl"
    lines = [_event(1, "session.created"), _event(2, "turn.started")]
    path.write_text(json.dumps(lines[0]) + "\nnot-json\n" + json.dumps(lines[1]) + "\n", encoding="utf-8")

    with pytest.raises(JournalCorruptionError, match="line 2"):
        SessionJournal.read_file(path)


def test_reader_rejects_sequence_gap(tmp_path: Path) -> None:
    path = tmp_path / "session_test.jsonl"
    lines = [_event(1, "session.created"), _event(3, "turn.started")]
    path.write_text("\n".join(json.dumps(item) for item in lines) + "\n", encoding="utf-8")

    with pytest.raises(JournalCorruptionError, match="sequence"):
        SessionJournal.read_file(path)


def test_second_writer_is_rejected(tmp_path: Path) -> None:
    first = SessionJournal(tmp_path, "session_test")
    try:
        with pytest.raises(JournalWriteError, match="lock"):
            SessionJournal(tmp_path, "session_test", lock_timeout=0)
    finally:
        first.close()


def test_append_reopens_and_retries_once_after_write_failure(tmp_path: Path) -> None:
    journal = SessionJournal(tmp_path, "session_test")
    real_file = journal._file

    class FailOnceFile:
        def write(self, _line: str) -> int:
            raise OSError("disk hiccup")

        def close(self) -> None:
            assert real_file is not None
            real_file.close()

    journal._file = FailOnceFile()  # type: ignore[assignment]
    try:
        event = journal.append("session.created", {})
    finally:
        journal.close()

    assert event.sequence == 1
    assert [item.type for item in SessionJournal.read_file(tmp_path / "session_test.jsonl")] == [
        "session.created"
    ]
