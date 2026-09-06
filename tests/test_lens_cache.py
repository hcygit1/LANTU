from __future__ import annotations

from datetime import datetime, timezone

from lantu.conversation import ConversationManager
from lantu.memory.journal import JournalEvent
from lantu.tools.lens.cache import build_cache_report
from lantu.tools.lens.fingerprint import build_assembly_fingerprint


def event(sequence: int, event_type: str, payload: dict) -> JournalEvent:
    return JournalEvent(
        schema_version=1,
        event_id=f"event_{sequence}",
        session_id="session_a",
        runtime_id="runtime_a",
        turn_id="turn_a",
        sequence=sequence,
        timestamp=datetime.now(timezone.utc).isoformat(),
        type=event_type,
        payload=payload,
    )


def test_cache_report_recognizes_append_only_history() -> None:
    first = ConversationManager()
    first.add_user_message("hello")
    second = ConversationManager()
    second.add_user_message("hello")
    second.add_assistant_message("answer")
    fp1 = build_assembly_fingerprint("system", [], first.get_messages()).to_payload()
    fp2 = build_assembly_fingerprint("system", [], second.get_messages()).to_payload()

    events = [
        event(1, "model.request.started", {
            "model_call_id": "call_1", "call_kind": "main", "provider": "openai-compat",
            "model": "glm", "agent_id": "agent", "assembly": fp1,
        }),
        event(2, "usage.recorded", {
            "model_call_id": "call_1", "input_tokens": 100, "cache_read_tokens": 0,
        }),
        event(3, "model.request.started", {
            "model_call_id": "call_2", "call_kind": "main", "provider": "openai-compat",
            "model": "glm", "agent_id": "agent", "assembly": fp2,
        }),
        event(4, "usage.recorded", {
            "model_call_id": "call_2", "input_tokens": 20, "cache_read_tokens": 80,
        }),
    ]

    report = build_cache_report("session_a", events)

    assert [call.change for call in report.calls] == ["cold_start", "append_only"]
    assert report.calls[1].common_message_count == 1
    assert report.calls[1].cache_hit_rate == 0.8


def test_cache_report_flags_provider_miss_candidate() -> None:
    conversation = ConversationManager()
    conversation.add_user_message("hello")
    fingerprint = build_assembly_fingerprint("system", [], conversation.get_messages()).to_payload()
    base = {
        "call_kind": "main", "provider": "openai-compat", "model": "glm",
        "agent_id": "agent", "assembly": fingerprint,
    }
    events = [
        event(1, "model.request.started", {**base, "model_call_id": "call_1"}),
        event(2, "usage.recorded", {
            "model_call_id": "call_1", "input_tokens": 20, "cache_read_tokens": 80,
        }),
        event(3, "model.request.started", {**base, "model_call_id": "call_2"}),
        event(4, "usage.recorded", {"model_call_id": "call_2", "input_tokens": 100}),
    ]

    report = build_cache_report("session_a", events)

    assert report.calls[1].change == "provider_miss_candidate"
