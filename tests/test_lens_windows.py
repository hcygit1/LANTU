from lantu.memory.journal import JournalEvent
from lantu.tools.lens.windows import segment_windows


def event(sequence: int, event_type: str, payload: dict | None = None) -> JournalEvent:
    return JournalEvent(
        schema_version=1,
        event_id=f"event_{sequence}",
        session_id="session_a",
        runtime_id="runtime_a",
        turn_id="turn_a",
        sequence=sequence,
        timestamp="2026-01-01T00:00:00+00:00",
        type=event_type,
        payload=payload or {},
    )


def test_segment_windows_groups_rollover_as_new_window_start() -> None:
    windows = segment_windows(
        [
            event(1, "session.created", {"window_id": "window_1"}),
            event(2, "message.created"),
            event(
                3,
                "context.window.rolled_over",
                {"window_id": "window_2", "parent_window_id": "window_1"},
            ),
            event(4, "message.created", {"window_id": "window_2"}),
        ]
    )

    assert [window.window_id for window in windows] == ["window_1", "window_2"]
    assert [[item.sequence for item in window.events] for window in windows] == [
        [1, 2],
        [3, 4],
    ]
    assert windows[0].is_active is False
    assert windows[1].is_active is True
    assert windows[1].parent_window_id == "window_1"


def test_segment_windows_supports_legacy_session() -> None:
    windows = segment_windows([event(1, "session.created"), event(2, "turn.started")])

    assert len(windows) == 1
    assert windows[0].window_id == "window_legacy"
    assert windows[0].is_active is True
