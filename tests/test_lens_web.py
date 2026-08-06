from pathlib import Path

from fastapi.testclient import TestClient

from lantu.memory.journal import SessionJournal
from lantu.tools.lens.web import create_lens_app


def test_lens_web_reads_session_journal(tmp_path: Path) -> None:
    journal = SessionJournal(tmp_path / ".lantu" / "sessions", "session_a")
    journal.append("session.created", {})
    journal.close()
    client = TestClient(create_lens_app(tmp_path))

    assert client.get("/").status_code == 200
    assert client.get("/api/sessions").json() == [{"id": "session_a"}]
    detail = client.get("/api/session/session_a").json()
    assert detail["events"][0]["type"] == "session.created"


def test_lens_web_searches_all_sessions(tmp_path: Path) -> None:
    journal = SessionJournal(tmp_path / ".lantu" / "sessions", "session_a")
    journal.append("session.created", {"project_root": "needle"})
    journal.close()
    client = TestClient(create_lens_app(tmp_path))
    assert client.get("/api/search", params={"query": "needle"}).json()[0]["session_id"] == "session_a"
