from lantu.memory.journal import JournalEvent
from lantu.tools.lens import CaptureRecord, correlate_evidence


def journal_event(call_id: str) -> JournalEvent:
    return JournalEvent(
        1,
        "event_1",
        "session_a",
        "runtime_a",
        "turn_a",
        4,
        "2026-01-01T00:00:00+00:00",
        "model.request.started",
        {"model_call_id": call_id, "provider": "openai", "model": "gpt"},
    )


def capture(call_id: str | None, timestamp: str = "2026-01-01T00:00:01+00:00") -> CaptureRecord:
    return CaptureRecord(timestamp, "session_a", call_id, "openai", "gpt", {}, {})


def test_capture_correlation_prefers_exact_id() -> None:
    links = correlate_evidence([journal_event("call_1")], [capture("call_1")])
    assert links[0].confidence == "exact"


def test_capture_correlation_marks_fallback_low_confidence() -> None:
    links = correlate_evidence([journal_event("call_1")], [capture(None)])
    assert links[0].confidence == "low"


def test_capture_correlation_marks_missing_evidence() -> None:
    links = correlate_evidence([journal_event("call_1")], [])
    assert links[0].confidence == "missing"
