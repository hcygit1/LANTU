from pathlib import Path

from lantu.memory.journal import SessionJournal
from lantu.tools.lens import LensReader


def test_lens_reads_journal_events_without_writing(tmp_path: Path) -> None:
    sessions_dir = tmp_path / ".lantu" / "sessions"
    journal = SessionJournal(sessions_dir, "session_a")
    journal.append("session.created", {"project_root": str(tmp_path)})
    journal.append("runtime.started", {"mode": "new"}, runtime_id="runtime_a")
    journal.close()

    reader = LensReader(tmp_path)
    assert reader.session_ids() == ["session_a"]
    events = reader.read("session_a")
    assert [event.type for event in events] == ["session.created", "runtime.started"]
    assert not (sessions_dir / "session_a.meta").exists()


def test_lens_lists_events_across_sessions(tmp_path: Path) -> None:
    sessions_dir = tmp_path / ".lantu" / "sessions"
    for session_id in ("session_b", "session_a"):
        journal = SessionJournal(sessions_dir, session_id)
        journal.append("session.created", {})
        journal.close()

    assert [sid for sid, _ in LensReader(tmp_path).list_events()] == [
        "session_a",
        "session_b",
    ]


def test_lens_search_matches_event_type_and_payload(tmp_path: Path) -> None:
    sessions_dir = tmp_path / ".lantu" / "sessions"
    journal = SessionJournal(sessions_dir, "session_a")
    journal.append("session.created", {})
    journal.append("tool.failed", {"tool_name": "ReadFile", "error": "missing"})
    journal.close()

    reader = LensReader(tmp_path)
    assert [event.type for _, event in reader.search("readfile")] == ["tool.failed"]
    assert [event.type for _, event in reader.search("FAILED")] == ["tool.failed"]
