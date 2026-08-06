from lantu.tools.lens import EventGraph, NormalizedEvent, build_report


def test_report_resolves_finding_evidence() -> None:
    failed = NormalizedEvent(
        "session_a",
        4,
        "2026-01-01T00:00:00Z",
        "tool",
        "tool.failed",
        {"tool_call_id": "tool_1", "error": "missing file"},
    )
    report = build_report("session_a", [EventGraph((failed,), ())])
    assert report.status == "error"
    assert report.findings[0].code == "tool_failed"
    assert report.evidence[4].payload["error"] == "missing file"
    assert "evidence #4" in report.render_text()


def test_report_is_ok_without_findings() -> None:
    report = build_report("session_a", [])
    assert report.status == "ok"
    assert "No failures" in report.render_text()
