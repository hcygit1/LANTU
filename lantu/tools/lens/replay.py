from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from lantu.tools.lens.capture import CaptureRecord

from lantu.tools.lens.tasks import TaskSegment


@dataclass(frozen=True)
class ReplayPlan:
    session_id: str
    task_id: str
    messages: tuple[dict[str, Any], ...]
    model_calls: tuple[dict[str, Any], ...]
    network_enabled: bool = False
    tool_execution_enabled: bool = False
    journal_write_enabled: bool = False


@dataclass(frozen=True)
class ReplayResult:
    status_code: int
    headers: dict[str, str]
    body: str


def build_replay_plan(task: TaskSegment) -> ReplayPlan:
    messages: list[dict[str, Any]] = []
    model_calls: list[dict[str, Any]] = []
    for event in task.events:
        if event.source_type == "message.created":
            messages.append(dict(event.payload))
        elif event.source_type == "model.request.started":
            model_calls.append(dict(event.payload))
    return ReplayPlan(
        session_id=task.session_id,
        task_id=task.task_id,
        messages=tuple(messages),
        model_calls=tuple(model_calls),
    )


def execute_capture_replay(
    capture: CaptureRecord,
    api_key: str,
    *,
    transport: httpx.BaseTransport | None = None,
) -> ReplayResult:
    """Replay one exact HTTP capture without interpreting model tool calls."""
    method = str(capture.request.get("method", "POST"))
    url = str(capture.request.get("url", ""))
    if not url:
        raise ValueError("capture request URL is missing")
    headers = {
        str(name): str(value)
        for name, value in capture.request.get("headers", {}).items()
        if str(name).lower() not in {"host", "content-length", "authorization", "x-api-key"}
    }
    if capture.provider == "anthropic":
        headers["x-api-key"] = api_key
    else:
        headers["authorization"] = f"Bearer {api_key}"
    body = str(capture.request.get("body", ""))
    with httpx.Client(transport=transport, timeout=120, trust_env=False) as client:
        response = client.request(method, url, headers=headers, content=body)
    return ReplayResult(
        status_code=response.status_code,
        headers={name: value for name, value in response.headers.items()},
        body=response.text,
    )
