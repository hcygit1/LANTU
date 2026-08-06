from pathlib import Path

from lantu.__main__ import build_parser, run_lens
from lantu.memory.journal import SessionJournal


def test_lens_parser_supports_list_and_events() -> None:
    args = build_parser().parse_args(["lens", "events", "session_a", "--json"])
    assert args.command == "lens"
    assert args.action == "events"
    assert args.session_id == "session_a"
    assert args.json_output is True


def test_lens_parser_supports_search() -> None:
    args = build_parser().parse_args(["lens", "search", "session_a", "ReadFile"])
    assert args.action == "search"
    assert args.session_id == "session_a"
    assert args.query == "ReadFile"


def test_lens_parser_supports_tasks_and_actions() -> None:
    tasks = build_parser().parse_args(["lens", "tasks", "session_a"])
    actions = build_parser().parse_args(["lens", "actions", "session_a", "--json"])
    assert tasks.action == "tasks"
    assert actions.action == "actions"
    assert actions.json_output is True


def test_lens_parser_supports_annotation() -> None:
    args = build_parser().parse_args(
        [
            "lens",
            "annotate",
            "session_a",
            "--kind",
            "diagnosis",
            "--target",
            "tool_1",
            "--value",
            '{"correct": false}',
        ]
    )
    assert args.kind == "diagnosis"
    assert args.target == "tool_1"


def test_lens_parser_supports_evidence() -> None:
    args = build_parser().parse_args(["lens", "evidence", "session_a", "--json"])
    assert args.action == "evidence"
    assert args.json_output is True


def test_run_lens_lists_events_as_json(tmp_path: Path, monkeypatch, capsys) -> None:
    sessions_dir = tmp_path / ".lantu" / "sessions"
    journal = SessionJournal(sessions_dir, "session_a")
    journal.append("session.created", {"project_root": str(tmp_path)})
    journal.close()
    monkeypatch.chdir(tmp_path)

    run_lens("events", "session_a", json_output=True)

    assert '"type": "session.created"' in capsys.readouterr().out


def test_run_lens_prints_diagnosis_with_evidence(tmp_path: Path, monkeypatch, capsys) -> None:
    sessions_dir = tmp_path / ".lantu" / "sessions"
    journal = SessionJournal(sessions_dir, "session_a")
    journal.append("session.created", {})
    journal.append("turn.started", {}, runtime_id="runtime_a", turn_id="turn_a")
    journal.append(
        "tool.failed",
        {"tool_call_id": "tool_1", "error": "missing file"},
        runtime_id="runtime_a",
        turn_id="turn_a",
    )
    journal.append("turn.completed", {}, runtime_id="runtime_a", turn_id="turn_a")
    journal.close()
    monkeypatch.chdir(tmp_path)

    run_lens("diagnose", "session_a")

    output = capsys.readouterr().out
    assert "tool_failed" in output
    assert "evidence #3" in output


def test_run_lens_prints_tasks_and_actions(tmp_path: Path, monkeypatch, capsys) -> None:
    sessions_dir = tmp_path / ".lantu" / "sessions"
    journal = SessionJournal(sessions_dir, "session_a")
    journal.append("session.created", {})
    journal.append("turn.started", {}, runtime_id="runtime_a", turn_id="turn_a")
    journal.append(
        "message.created",
        {"message_id": "msg_1", "role": "user", "content": "inspect"},
        runtime_id="runtime_a",
        turn_id="turn_a",
    )
    journal.append("turn.completed", {}, runtime_id="runtime_a", turn_id="turn_a")
    journal.close()
    monkeypatch.chdir(tmp_path)

    run_lens("tasks", "session_a")
    assert "task_1 sequences=1-4" in capsys.readouterr().out
    run_lens("actions", "session_a")
    assert "task_1 message completed" in capsys.readouterr().out


def test_run_lens_adds_annotation(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    run_lens(
        "annotate",
        "session_a",
        annotation_kind="diagnosis",
        annotation_target="tool_1",
        annotation_value='{"correct": false}',
    )
    assert '"kind": "diagnosis"' in capsys.readouterr().out
    assert (tmp_path / ".lantu" / "lens" / "annotations" / "session_a.jsonl").exists()
