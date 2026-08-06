from __future__ import annotations

from pathlib import Path

from lantu.conversation import Message, ThinkingBlock, ToolResultBlock, ToolUseBlock
from lantu.memory.journal import SessionJournal
from lantu.memory.session import ExecutionEvent, SessionManager


def test_session_records_lifecycle_and_messages(tmp_path: Path) -> None:
    manager = SessionManager(str(tmp_path))
    session = manager.create()
    session.start_runtime("new")
    session.start_turn("user")
    session.commit_message(Message(role="user", content="inspect the project"))
    session.complete_turn(iteration_count=1)
    session.stop_runtime("user_exit")
    session.close()

    events = SessionJournal.read_file(
        tmp_path / ".lantu" / "sessions" / f"{session.session_id}.jsonl"
    )
    assert [event.type for event in events] == [
        "session.created",
        "runtime.started",
        "turn.started",
        "message.created",
        "turn.completed",
        "runtime.stopped",
    ]
    assert events[3].payload["content"] == "inspect the project"


def test_resume_rebuilds_complete_structured_messages(tmp_path: Path) -> None:
    manager = SessionManager(str(tmp_path))
    session = manager.create()
    session.start_runtime("new")
    session.start_turn("user")
    session.commit_message(Message(role="user", content="read a.py"))
    session.commit_message(
        Message(
            role="assistant",
            content="reading",
            tool_uses=[ToolUseBlock("tool_1", "ReadFile", {"path": "a.py"})],
            thinking_blocks=[ThinkingBlock("need source", "sig")],
        )
    )
    session.commit_message(
        Message(
            role="user",
            content="",
            tool_results=[ToolResultBlock("tool_1", "print('ok')")],
        )
    )
    session.complete_turn(iteration_count=2)
    session.stop_runtime("user_exit")
    session.close()

    resumed = manager.resume(session.session_id)
    assert resumed is not None
    assert [message.role for message in resumed.messages] == ["user", "assistant", "user"]
    assert resumed.messages[1].tool_uses[0].tool_use_id == "tool_1"
    assert resumed.messages[1].thinking_blocks[0].thinking == "need source"
    assert resumed.messages[2].tool_results[0].content == "print('ok')"
    resumed.session.close()


def test_resume_rebuilds_missing_meta_cache(tmp_path: Path) -> None:
    manager = SessionManager(str(tmp_path))
    session = manager.create()
    session.start_runtime("new")
    session.start_turn("user")
    session.commit_message(Message(role="user", content="restore this title"))
    session.complete_turn(iteration_count=1)
    session.stop_runtime("user_exit")
    session.close()
    meta_path = tmp_path / ".lantu" / "sessions" / f"{session.session_id}.meta"
    meta_path.unlink()

    resumed = manager.resume(session.session_id)
    assert resumed is not None
    assert resumed.session.meta.title == "restore this title"
    assert resumed.session.meta.message_count == 1
    assert meta_path.exists()
    resumed.session.close()


def test_resume_marks_incomplete_tool_unknown_and_restores_synthetic_result(
    tmp_path: Path,
) -> None:
    manager = SessionManager(str(tmp_path))
    session = manager.create()
    session.start_runtime("new")
    session.start_turn("user")
    session.commit_message(
        Message(
            role="assistant",
            content="reading",
            tool_uses=[ToolUseBlock("tool_1", "ReadFile", {"path": "a.py"})],
        )
    )
    session.record(
        ExecutionEvent(
            "tool.started",
            {"tool_call_id": "tool_1", "tool_name": "ReadFile", "arguments": {}},
        )
    )
    session.journal.close()

    resumed = manager.resume(session.session_id)
    assert resumed is not None
    events = resumed.session.journal.read()
    assert any(event.type == "runtime.interrupted" for event in events)
    assert any(event.type == "turn.interrupted" for event in events)
    assert any(event.type == "tool.interrupted" for event in events)
    assert resumed.messages[-1].tool_results[0].is_error is True
    assert "unknown" in resumed.messages[-1].tool_results[0].content
    resumed.session.close()
