from lantu.tools.lens import SessionSnapshot, compare_snapshots


def test_compare_snapshots_returns_directional_deltas() -> None:
    left = SessionSnapshot("left", 3, {"tool.failed": 1}, {"failed": 1}, {"tool_failed": 1})
    right = SessionSnapshot("right", 5, {"tool.completed": 2}, {"completed": 2}, {})
    result = compare_snapshots(left, right)
    assert result.deltas["event_count"] == 2
    assert result.deltas["event.tool.failed"] == -1
    assert result.deltas["action.completed"] == 2
    assert result.deltas["finding.tool_failed"] == -1
