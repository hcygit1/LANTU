from lantu.memory.journal import JournalEvent
from lantu.tools.lens import normalize_event, normalize_events


def event(event_type: str, payload: dict) -> JournalEvent:
    return JournalEvent(
        schema_version=1,
        event_id="event_1",
        session_id="session_a",
        runtime_id="runtime_a",
        turn_id="turn_a",
        sequence=1,
        timestamp="2026-01-01T00:00:00+00:00",
        type=event_type,
        payload=payload,
    )


def test_normalize_preserves_source_and_payload() -> None:
    result = normalize_event(event("tool.failed", {"tool_name": "ReadFile"}))
    assert result.kind == "tool"
    assert result.source_type == "tool.failed"
    assert result.payload == {"tool_name": "ReadFile"}


def test_normalize_events_keeps_order() -> None:
    results = list(normalize_events([event("turn.started", {}), event("message.created", {})]))
    assert [item.kind for item in results] == ["lifecycle", "message"]
