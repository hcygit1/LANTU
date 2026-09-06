from __future__ import annotations

from types import SimpleNamespace
from typing import AsyncIterator

import pytest

from lantu.client import LLMClient, _record_prepared_request
from lantu.conversation import ConversationManager
from lantu.tools.base import StreamEnd, StreamEvent, TextDelta
from lantu.tools.lens.request_trace import LensRequestRecorder


class FakeSession:
    session_id = "session_test"

    def __init__(self) -> None:
        self.events: list[object] = []

    def record(self, event: object) -> None:
        self.events.append(event)


class PreparedClient(LLMClient):
    model = "glm-5.2"

    async def stream(
        self,
        conversation: ConversationManager,
        system: str = "",
        tools: list[dict] | None = None,
    ) -> AsyncIterator[StreamEvent]:
        _record_prepared_request({
            "model": self.model,
            "messages": [{"role": "user", "content": "secret prompt"}],
            "extra_headers": {"X-LANTU-Model-Call-ID": "changes"},
        })
        yield TextDelta("ok")
        yield StreamEnd(
            "end_turn",
            input_tokens=20,
            output_tokens=5,
            cache_read=80,
            cache_creation=0,
        )


@pytest.mark.asyncio
async def test_request_recorder_links_fingerprints_and_usage() -> None:
    session = FakeSession()
    recorder = LensRequestRecorder(session, protocol="openai-compat", agent_id="agent_1")
    conversation = ConversationManager()
    conversation.add_user_message("secret prompt")

    events = [
        event
        async for event in recorder.stream(
            PreparedClient(),
            conversation,
            system="secret system",
            tools=[],
            call_kind="main",
            schema_epoch_id="schema_1",
        )
    ]

    assert len(events) == 2
    recorded = session.events
    assert [event.event_type for event in recorded] == [
        "model.request.started",
        "model.request.prepared",
        "model.request.completed",
        "usage.recorded",
    ]
    call_ids = {event.payload["model_call_id"] for event in recorded}
    assert len(call_ids) == 1
    assert recorded[0].payload["call_kind"] == "main"
    assert recorded[0].payload["schema_epoch_id"] == "schema_1"
    assert recorded[-1].payload["cache_read_tokens"] == 80
    assert "secret prompt" not in repr([event.payload for event in recorded])
    assert "secret system" not in repr([event.payload for event in recorded])

