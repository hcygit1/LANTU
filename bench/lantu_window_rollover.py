"""Online acceptance check for same-Session Conversation window rollover."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from lantu.agent import (
    CompactNotification,
    ErrorEvent,
    LoopComplete,
    PermissionRequest,
    PermissionResponse,
    StreamText,
    ToolResultEvent,
    ToolUseEvent,
)
from lantu.config import load_config
from lantu.context import CompactionPolicy, ensure_session_dir
from lantu.context.manager import make_persisted_reference, persist_tool_result
from lantu.conversation import ConversationManager, Message, ToolResultBlock
from lantu.memory.journal import SessionJournal
from lantu.memory.session import SessionManager
from lantu.permissions import PermissionMode
from lantu.runtime import build_interactive_runtime


MARKER = "RECOVERY_CODE=LANTU_WINDOW_20260909_Q9M4"
MARKER_VALUE = "LANTU_WINDOW_20260909_Q9M4"
SUMMARY_MARKER = "本次会话延续自之前的对话，因上下文空间不足进行了压缩。"


async def validate() -> dict[str, Any]:
    validation_root = Path(tempfile.mkdtemp(prefix="lantu-window-rollover-"))
    config = load_config()
    provider = config.providers[0]
    provider.context_window = 100_000
    provider.reasoning_effort = "low"
    provider.max_output_tokens = 2_048

    runtime = await build_interactive_runtime(
        config,
        provider,
        PermissionMode("bypassPermissions"),
        None,
        validation_root,
    )
    session_id = runtime.session.session_id
    initial_window_id = runtime.session.active_window_id
    artifact = persist_tool_result(
        "window_acceptance_artifact",
        "historical tool output\n" + MARKER + "\n",
        ensure_session_dir(str(validation_root)),
    )
    reference = make_persisted_reference(artifact)
    conversation = ConversationManager(
        history=[
            Message(role="user", content=SUMMARY_MARKER + "\n" + "prior state " * 2_000),
            Message(
                role="user",
                content="",
                tool_results=[ToolResultBlock("window_acceptance_artifact", reference)],
            ),
            *[
                Message(role="user", content=f"recent context {index} " + "r" * 5_000)
                for index in range(6)
            ],
            Message(
                role="user",
                content=(
                    "Find the exact RECOVERY_CODE in the historical tool result. "
                    "Use CodeSearch in exact mode on the artifact path from the window handoff "
                    "and report only the code."
                ),
            ),
        ]
    )
    runtime.conversation = conversation
    runtime.agent.file_ledger = runtime.session.file_ledger
    runtime.agent.context_window = 100_000
    conversation.record_usage_anchor(input_tokens=99_000)
    before_tokens = conversation.current_tokens()
    runtime.session.start_turn("user")
    for message in conversation.history:
        runtime.session.commit_message(message)
    seen_messages = {id(message) for message in conversation.history}

    actions: list[str] = []
    tool_calls: list[str] = []
    tool_outputs: list[str] = []
    errors: list[str] = []
    answer: list[str] = []
    after_rollover_tokens: int | None = None
    completed = False
    try:
        async for event in runtime.agent.run(conversation):
            if isinstance(event, CompactNotification):
                actions.append(event.action)
                if event.action == CompactionPolicy.WINDOW_ROLLOVER and event.boundary:
                    runtime.session.context_window_rolled_over(
                        event.boundary.summary,
                        event.boundary.keep,
                        event.boundary.artifact_refs,
                    )
                    after_rollover_tokens = conversation.current_tokens()
                    seen_messages = {id(message) for message in conversation.history}
            elif isinstance(event, ToolUseEvent):
                tool_calls.append(event.tool_name)
            elif isinstance(event, ToolResultEvent):
                tool_outputs.append(event.output)
            elif isinstance(event, StreamText):
                answer.append(event.text)
            elif isinstance(event, ErrorEvent):
                errors.append(event.message)
            elif isinstance(event, PermissionRequest) and not event.future.done():
                event.future.set_result(PermissionResponse.ALLOW)
            elif isinstance(event, LoopComplete):
                for message in conversation.history:
                    if id(message) not in seen_messages:
                        runtime.session.commit_message(message)
                        seen_messages.add(id(message))
                runtime.session.complete_turn(event.total_turns)
                completed = True
                break
    finally:
        if not completed and runtime.session.turn_id is not None:
            runtime.session.interrupt_turn("validation_error")
        await runtime.close()

    journal_path = validation_root / ".lantu" / "sessions" / f"{session_id}.jsonl"
    events = SessionJournal.read_file(journal_path)
    rollover = next(
        (event for event in events if event.type == "context.window.rolled_over"),
        None,
    )
    manager = SessionManager(str(validation_root))
    resumed = manager.resume(session_id)
    if resumed is None:
        raise RuntimeError("session could not be resumed")
    resumed_window_id = resumed.session.active_window_id
    resumed_messages = list(resumed.messages)
    resumed.session.close()

    usage_events = [event.payload for event in events if event.type == "usage.recorded"]
    prompt_tokens = sum(
        int(item.get("input_tokens", 0)) + int(item.get("cache_read_tokens", 0))
        for item in usage_events
    )
    cached_tokens = sum(int(item.get("cache_read_tokens", 0)) for item in usage_events)
    result = {
        "model": provider.model,
        "session_id": session_id,
        "same_session_after_resume": resumed.session.session_id == session_id,
        "initial_window_id": initial_window_id,
        "resumed_window_id": resumed_window_id,
        "window_changed": initial_window_id != resumed_window_id,
        "actions": actions,
        "before_tokens": before_tokens,
        "after_rollover_tokens": after_rollover_tokens,
        "token_reduction": (
            before_tokens - after_rollover_tokens
            if after_rollover_tokens is not None
            else None
        ),
        "tool_calls": tool_calls,
        "marker_recovered": MARKER_VALUE in "".join(answer),
        "artifact_exists_after_resume": artifact.is_file(),
        "artifact_indexed": bool(
            rollover and str(artifact) in rollover.payload.get("artifact_refs", [])
        ),
        "rollover_event_sequence": rollover.sequence if rollover else None,
        "resumed_message_count": len(resumed_messages),
        "old_tool_result_absent_from_projection": all(
            result.tool_use_id != "window_acceptance_artifact"
            for message in resumed_messages
            for result in message.tool_results
        ),
        "answer": "".join(answer),
        "tool_output_contains_marker": any(MARKER in output for output in tool_outputs),
        "model_requests": len(usage_events),
        "prompt_tokens": prompt_tokens,
        "cached_tokens": cached_tokens,
        "cache_hit_rate": cached_tokens / prompt_tokens if prompt_tokens else 0.0,
        "errors": errors,
    }
    result["passed"] = all(
        [
            CompactionPolicy.WINDOW_ROLLOVER in actions,
            result["same_session_after_resume"],
            result["window_changed"],
            result["marker_recovered"],
            result["artifact_exists_after_resume"],
            result["artifact_indexed"],
            result["old_tool_result_absent_from_projection"],
            result["tool_output_contains_marker"],
            not errors,
        ]
    )
    if result["passed"]:
        shutil.rmtree(validation_root, ignore_errors=True)
    else:
        result["validation_root"] = str(validation_root)
    return result


def main() -> None:
    if not os.environ.get("OPENAI_API_KEY"):
        raise SystemExit("OPENAI_API_KEY is not set")
    print(json.dumps(asyncio.run(validate()), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
