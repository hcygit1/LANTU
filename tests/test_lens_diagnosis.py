from lantu.tools.lens import EventGraph, NormalizedEvent, diagnose_graph


def node(sequence: int, source_type: str, payload: dict) -> NormalizedEvent:
    return NormalizedEvent("session_a", sequence, "2026-01-01T00:00:00Z", "tool", source_type, payload)


def test_diagnosis_reports_failure_and_incomplete_call() -> None:
    graph = EventGraph(
        nodes=(
            node(1, "tool.started", {"tool_call_id": "tool_1"}),
            node(2, "model.request.started", {"model_call_id": "model_1"}),
            node(3, "tool.failed", {"tool_call_id": "tool_1"}),
        ),
        edges=(),
    )
    findings = diagnose_graph(graph)
    assert [(item.code, item.evidence_sequences) for item in findings] == [
        ("tool_failed", (3,)),
        ("model_incomplete", (2,)),
    ]
