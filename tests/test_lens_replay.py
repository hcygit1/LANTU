import httpx

from lantu.tools.lens import (
    CaptureRecord,
    NormalizedEvent,
    TaskSegment,
    build_replay_plan,
    execute_capture_replay,
)


def test_replay_plan_isolated_and_read_only() -> None:
    task = TaskSegment(
        "task_1",
        "session_a",
        1,
        2,
        (
            NormalizedEvent(
                "session_a", 1, "now", "message", "message.created", {"content": "fix it"}
            ),
            NormalizedEvent(
                "session_a",
                2,
                "now",
                "model",
                "model.request.started",
                {"model_call_id": "model_1"},
            ),
        ),
    )
    plan = build_replay_plan(task)
    assert plan.messages[0]["content"] == "fix it"
    assert plan.network_enabled is False
    assert plan.tool_execution_enabled is False
    assert plan.journal_write_enabled is False


def test_execute_capture_replay_sends_http_without_tool_execution() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer test-key"
        return httpx.Response(200, text='{"tool_calls":[{"name":"Bash"}]}')

    capture = CaptureRecord(
        "now",
        "session_a",
        "call_1",
        "openai",
        "gpt",
        {"method": "POST", "url": "https://example.test/v1", "headers": {}, "body": "{}"},
        {},
    )
    result = execute_capture_replay(
        capture, "test-key", transport=httpx.MockTransport(handler)
    )
    assert result.status_code == 200
    assert "tool_calls" in result.body
