from lantu.tools.lens import NormalizedEvent, TaskSegment, build_action_graph


def event(sequence: int, source_type: str, payload: dict) -> NormalizedEvent:
    return NormalizedEvent("session_a", sequence, "2026-01-01T00:00:00Z", "tool", source_type, payload)


def test_action_graph_pairs_operations_and_marks_status() -> None:
    task = TaskSegment(
        "task_1",
        "session_a",
        1,
        4,
        (
            event(1, "message.created", {"message_id": "msg_1"}),
            event(2, "model.request.started", {"model_call_id": "model_1"}),
            event(3, "model.request.completed", {"model_call_id": "model_1"}),
            event(4, "tool.started", {"tool_call_id": "tool_1"}),
        ),
    )
    graph = build_action_graph(task)
    assert [(node.kind, node.status) for node in graph.nodes] == [
        ("message", "completed"),
        ("model", "completed"),
        ("tool", "incomplete"),
    ]
    assert graph.edges == (("msg_1", "model_1"), ("model_1", "tool_1"))
