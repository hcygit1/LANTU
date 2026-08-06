from lantu.tools.lens import NormalizedEvent, TaskSegment, build_event_graph


def node(sequence: int, source_type: str, payload: dict) -> NormalizedEvent:
    return NormalizedEvent("session_a", sequence, "2026-01-01T00:00:00Z", "tool", source_type, payload)


def test_graph_links_sequence_and_same_tool_call() -> None:
    task = TaskSegment(
        "task_1",
        "session_a",
        1,
        3,
        (
            node(1, "tool.started", {"tool_call_id": "call_1"}),
            node(2, "tool.completed", {"tool_call_id": "call_1"}),
            node(3, "turn.completed", {}),
        ),
    )
    graph = build_event_graph(task)
    assert (1, 2, "next") in graph.edges
    assert (1, 2, "same_operation") in graph.edges
    assert (2, 3, "next") in graph.edges
