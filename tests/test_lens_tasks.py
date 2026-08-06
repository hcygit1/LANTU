from lantu.tools.lens import NormalizedEvent, segment_tasks


def event(sequence: int, source_type: str) -> NormalizedEvent:
    return NormalizedEvent(
        session_id="session_a",
        sequence=sequence,
        timestamp=f"2026-01-01T00:00:0{sequence}+00:00",
        kind="lifecycle",
        source_type=source_type,
        payload={},
    )


def test_segment_tasks_groups_events_by_turn() -> None:
    tasks = segment_tasks(
        [
            event(1, "session.created"),
            event(2, "turn.started"),
            event(3, "message.created"),
            event(4, "turn.completed"),
            event(5, "turn.started"),
            event(6, "turn.interrupted"),
        ]
    )
    assert len(tasks) == 2
    assert tasks[0].task_id == "task_1"
    assert tasks[0].start_sequence == 1
    assert tasks[0].end_sequence == 4
    assert tasks[1].events[-1].source_type == "turn.interrupted"
