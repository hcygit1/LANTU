from __future__ import annotations

from pathlib import Path

import pytest

from lantu.conversation import (
    ConversationManager,
    Message,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
)
from lantu.context import (
    CompactionPolicy,
    auto_compact,
    ensure_session_dir,
)
from lantu.context.manager import make_persisted_reference, persist_tool_result
from lantu.memory.journal import SessionJournal
from lantu.memory.session import ExecutionEvent, SessionManager


class _UnusedSummaryClient:
    async def stream(self, *_args, **_kwargs):
        raise AssertionError("local tool-result compaction must not call the summary model")


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


def test_resume_replays_local_tool_result_compaction(tmp_path: Path) -> None:
    manager = SessionManager(str(tmp_path))
    session = manager.create()
    session.start_runtime("new")
    session.start_turn("user")
    session.commit_message(Message(role="user", content="inspect the output"))
    session.commit_message(
        Message(
            role="assistant",
            content="running tool",
            tool_uses=[ToolUseBlock("tool_1", "Bash", {})],
        )
    )
    session.commit_message(
        Message(
            role="user",
            content="",
            tool_results=[ToolResultBlock("tool_1", "full output")],
        )
    )
    reference = "<persisted-output>\n完整内容已保存到：\n/path/tool_1.txt"
    session.tool_results_compacted(
        [{"tool_use_id": "tool_1", "content": reference}]
    )
    session.complete_turn(iteration_count=1)
    session.stop_runtime("user_exit")
    session.close()

    resumed = manager.resume(session.session_id)
    assert resumed is not None
    assert resumed.messages[-1].tool_results[0].content == reference
    resumed.session.close()


def test_resume_rebuilds_latest_window_and_keeps_artifact_index(tmp_path: Path) -> None:
    manager = SessionManager(str(tmp_path))
    session = manager.create()
    session.start_runtime("new")
    session.start_turn("user")
    original = Message(
        role="user",
        content="",
        tool_results=[
            ToolResultBlock(
                "tool_1",
                "<persisted-output>\n完整内容已保存到：\n"
                + str(tmp_path / ".lantu" / "session" / "tool-results" / "tool_1.txt"),
            )
        ],
    )
    session.commit_message(original)
    session.context_window_rolled_over(
        "继续处理未完成任务",
        [],
        ["/artifact/tool_1.txt"],
    )
    session.commit_message(Message(role="user", content="继续"))
    session.complete_turn(iteration_count=1)
    session.stop_runtime("user_exit")
    session.close()

    events = SessionJournal.read_file(
        tmp_path / ".lantu" / "sessions" / f"{session.session_id}.jsonl"
    )
    rollover = next(event for event in events if event.type == "context.window.rolled_over")
    assert rollover.payload["parent_window_id"]
    assert "/artifact/tool_1.txt" in rollover.payload["artifact_refs"]
    assert any(path.endswith("tool_1.txt") for path in rollover.payload["artifact_refs"])

    resumed = manager.resume(session.session_id)
    assert resumed is not None
    assert len(resumed.messages) == 2
    assert "继续处理未完成任务" in resumed.messages[0].content
    assert "/artifact/tool_1.txt" in resumed.messages[0].content
    assert any(path.endswith("tool_1.txt") for path in resumed.messages[0].content.splitlines())
    assert resumed.messages[1].content == "继续"
    resumed.session.close()


@pytest.mark.asyncio
async def test_large_tool_result_remains_readable_after_compaction_and_resume(
    tmp_path: Path,
) -> None:
    manager = SessionManager(str(tmp_path))
    session = manager.create()
    session.start_runtime("new")
    session.start_turn("user")

    raw_output = "BEGIN\n" + ("result line\n" * 10_000) + "END\n"
    old_result = Message(
        role="user",
        content="",
        tool_results=[ToolResultBlock("tool_large", raw_output)],
    )
    recent = [
        Message(role="user", content=f"recent-{index}: " + "r" * 3_000)
        for index in range(25)
    ]
    messages = [old_result, *recent]
    for message in messages:
        session.commit_message(message)

    conversation = ConversationManager(history=list(messages))
    compacted = await auto_compact(
        conversation,
        _UnusedSummaryClient(),
        context_window=50_000,
        session_dir=ensure_session_dir(str(tmp_path)),
    )

    assert compacted is not None
    assert compacted.action == CompactionPolicy.COMPACT_STALE_TOOLS
    assert conversation.history[1:] == recent
    reference = conversation.history[0].tool_results[0].content
    artifact = tmp_path / ".lantu" / "session" / "tool-results" / "tool_large.txt"
    assert artifact.read_text(encoding="utf-8") == raw_output
    assert str(artifact) in reference

    session.tool_results_compacted(compacted.tool_result_replacements)
    session.complete_turn(iteration_count=1)
    session.stop_runtime("user_exit")
    session.close()

    resumed = manager.resume(session.session_id)
    assert resumed is not None
    assert resumed.messages[0].tool_results[0].content == reference
    assert artifact.read_text(encoding="utf-8") == raw_output
    resumed.session.close()


def test_resume_rebuilds_reminder_deduplication_state(tmp_path: Path) -> None:
    manager = SessionManager(str(tmp_path))
    session = manager.create()
    session.start_runtime("new")
    session.start_turn("user")
    original = ConversationManager()
    original.add_system_reminder(
        "tools: A",
        reminder_key="deferred_tools",
    )
    session.commit_message(original.history[-1])
    session.complete_turn(iteration_count=1)
    session.stop_runtime("user_exit")
    session.close()

    resumed = manager.resume(session.session_id)
    assert resumed is not None
    restored = ConversationManager(history=list(resumed.messages))

    assert restored.history[0].reminder_key == "deferred_tools"
    assert restored.history[0].reminder_hash
    assert not restored.add_system_reminder(
        "tools: A",
        reminder_key="deferred_tools",
    )
    assert restored.add_system_reminder(
        "tools: A, B",
        reminder_key="deferred_tools",
    )
    resumed.session.close()


def test_delete_session_removes_its_tool_result_artifacts(tmp_path: Path) -> None:
    manager = SessionManager(str(tmp_path))
    session = manager.create()
    session_id = session.session_id
    session.start_runtime("new")
    session.start_turn("user")
    artifact_dir = ensure_session_dir(str(tmp_path))
    artifact = persist_tool_result("tool_1", "old output", artifact_dir)
    session.commit_message(
        Message(
            role="user",
            content="",
            tool_results=[
                ToolResultBlock("tool_1", make_persisted_reference(artifact))
            ],
        )
    )
    session.complete_turn(iteration_count=1)
    session.stop_runtime("user_exit")
    session.close()

    assert manager.delete(session_id)
    assert not artifact.exists()


def test_delete_session_keeps_another_sessions_artifacts(tmp_path: Path) -> None:
    manager = SessionManager(str(tmp_path))
    first = manager.create()
    second = manager.create()
    artifact_dir = ensure_session_dir(str(tmp_path))

    first.start_runtime("new")
    first.start_turn("user")
    first_artifact = persist_tool_result("tool_first", "first", artifact_dir)
    first.commit_message(
        Message(
            role="user",
            content="",
            tool_results=[
                ToolResultBlock("tool_first", make_persisted_reference(first_artifact))
            ],
        )
    )
    first.complete_turn(iteration_count=1)
    first.stop_runtime("user_exit")
    first.close()

    second.start_runtime("new")
    second.start_turn("user")
    second_artifact = persist_tool_result("tool_second", "second", artifact_dir)
    second.commit_message(
        Message(
            role="user",
            content="",
            tool_results=[
                ToolResultBlock("tool_second", make_persisted_reference(second_artifact))
            ],
        )
    )
    second.complete_turn(iteration_count=1)
    second.stop_runtime("user_exit")
    second.close()

    assert manager.delete(first.session_id)
    assert not first_artifact.exists()
    assert second_artifact.exists()


def test_resume_rebuilds_loaded_tool_schema_state(tmp_path: Path) -> None:
    """Loaded deferred tools are recovered from Journal events in order."""
    manager = SessionManager(str(tmp_path))
    session = manager.create()
    session.start_runtime("new")
    session.record(
        ExecutionEvent(
            "tool.schema.loaded",
            {
                "tools": [
                    {"name": "DeferredBeta", "schema_hash": "b" * 64},
                    {"name": "DeferredAlpha", "schema_hash": "a" * 64},
                    {"name": "DeferredBeta", "schema_hash": "ignored"},
                ]
            },
        )
    )
    session.stop_runtime("user_exit")
    session.close()

    resumed = manager.resume(session.session_id)
    assert resumed is not None
    assert resumed.session.loaded_tool_states == [
        {"name": "DeferredBeta", "schema_hash": "b" * 64},
        {"name": "DeferredAlpha", "schema_hash": "a" * 64},
    ]
    resumed.session.close()


def test_resume_restores_latest_tool_schema_epoch(tmp_path: Path) -> None:
    manager = SessionManager(str(tmp_path))
    session = manager.create()
    session.start_runtime("new")
    payload = {
        "epoch_id": "schema-abc123",
        "fingerprint": "f" * 64,
        "protocol": "anthropic",
        "loading_mode": "progressive",
        "visible_tools": ["ReadFile", "ToolSearch"],
        "deferred_tools": ["Bash"],
        "previous_epoch_id": "schema-old",
        "reason": "tool_loaded",
    }
    session.record(ExecutionEvent("tool.schema.epoch.changed", payload))
    session.stop_runtime("user_exit")
    session.close()

    resumed = manager.resume(session.session_id)
    assert resumed is not None
    assert resumed.session.schema_epoch == payload
    resumed.session.close()


def test_resume_rebuilds_file_ledger_from_journal(tmp_path: Path) -> None:
    manager = SessionManager(str(tmp_path))
    session = manager.create()
    session.start_runtime("new")
    session.record(
        ExecutionEvent(
            "file.observed",
            {
                "path": str((tmp_path / "a.py").resolve()),
                "content_hash": "old",
                "size": 3,
                "line_count": 1,
                "operation": "read",
                "offset": 0,
                "limit": 2000,
                "mtime_ns": 1,
            },
        )
    )
    session.record(
        ExecutionEvent(
            "file.updated",
            {
                "path": str((tmp_path / "a.py").resolve()),
                "content_hash": "new",
                "size": 4,
                "line_count": 1,
                "operation": "edit",
                "offset": 0,
                "limit": None,
                "mtime_ns": 2,
            },
        )
    )
    session.stop_runtime("user_exit")
    session.close()

    resumed = manager.resume(session.session_id)
    assert resumed is not None
    entry = resumed.session.file_ledger.get(tmp_path / "a.py")
    assert entry is not None
    assert entry.content_hash == "new"
    assert entry.operation == "edit"
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
