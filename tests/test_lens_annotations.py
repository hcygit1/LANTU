from pathlib import Path

from lantu.memory.journal import SessionJournal
from lantu.tools.lens import AnnotationStore


def test_annotations_are_stored_outside_journal(tmp_path: Path) -> None:
    sessions_dir = tmp_path / ".lantu" / "sessions"
    journal = SessionJournal(sessions_dir, "session_a")
    journal.append("session.created", {})
    journal.close()
    journal_path = sessions_dir / "session_a.jsonl"
    before = journal_path.read_bytes()

    store = AnnotationStore(tmp_path)
    created = store.add(
        "session_a", "diagnosis", "tool_1", {"correct": False, "label": "expected"}
    )

    assert store.read("session_a") == [created]
    assert journal_path.read_bytes() == before


def test_annotations_reject_unknown_kind(tmp_path: Path) -> None:
    store = AnnotationStore(tmp_path)
    try:
        store.add("session_a", "unknown", "target", {})
    except ValueError as exc:
        assert "unsupported annotation kind" in str(exc)
    else:
        raise AssertionError("unknown annotation kind was accepted")
