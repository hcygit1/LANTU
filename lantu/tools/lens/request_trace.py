from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any, AsyncIterator

from lantu.client import LLMClient, model_call_context
from lantu.conversation import ConversationManager
from lantu.memory.session import ExecutionEvent
from lantu.tools.base import StreamEnd, StreamEvent
from lantu.tools.lens.fingerprint import (
    build_assembly_fingerprint,
    build_payload_fingerprint,
)


class LensRequestRecorder:
    """Record model-call facts without changing the streamed response."""

    def __init__(
        self,
        session: Any | None,
        *,
        protocol: str,
        agent_id: str | None = None,
    ) -> None:
        self.session = session
        self.protocol = protocol
        self.agent_id = agent_id

    def _record(self, event_type: str, payload: dict[str, Any]) -> None:
        if self.session is None:
            return
        self.session.record(ExecutionEvent(event_type, payload))

    async def stream(
        self,
        client: LLMClient,
        conversation: ConversationManager,
        *,
        system: str = "",
        tools: list[dict[str, Any]] | None = None,
        call_kind: str,
        schema_epoch_id: str | None = None,
    ) -> AsyncIterator[StreamEvent]:
        model_call_id = uuid.uuid4().hex
        model = str(getattr(client, "model", ""))
        common = {
            "model_call_id": model_call_id,
            "call_kind": call_kind,
            "provider": self.protocol,
            "model": model,
        }
        if self.agent_id:
            common["agent_id"] = self.agent_id
        if schema_epoch_id:
            common["schema_epoch_id"] = schema_epoch_id

        assembly = build_assembly_fingerprint(
            system,
            tools or [],
            conversation.get_messages(),
        )
        self._record(
            "model.request.started",
            {**common, "assembly": assembly.to_payload()},
        )

        prepared_recorded = False

        def record_prepared(kwargs: dict[str, Any]) -> None:
            nonlocal prepared_recorded
            if prepared_recorded:
                return
            prepared_recorded = True
            fingerprint = build_payload_fingerprint(kwargs)
            self._record(
                "model.request.prepared",
                {**common, "payload": fingerprint.to_payload()},
            )

        started = time.monotonic()
        usage: StreamEnd | None = None
        session_id = (
            str(getattr(self.session, "session_id", "")) or None
            if self.session is not None
            else None
        )
        try:
            with model_call_context(
                model_call_id,
                session_id,
                on_prepared=record_prepared,
            ):
                async for event in client.stream(
                    conversation,
                    system=system,
                    tools=tools,
                ):
                    if isinstance(event, StreamEnd):
                        usage = event
                    yield event
        except asyncio.CancelledError:
            self._record(
                "model.request.interrupted",
                {
                    **common,
                    "reason": "cancelled",
                    "result_known": False,
                    "elapsed_ms": int((time.monotonic() - started) * 1000),
                },
            )
            raise
        except Exception as exc:
            self._record(
                "model.request.failed",
                {
                    **common,
                    "error": {"type": type(exc).__name__, "message": str(exc)},
                    "elapsed_ms": int((time.monotonic() - started) * 1000),
                },
            )
            raise

        self._record(
            "model.request.completed",
            {
                **common,
                "elapsed_ms": int((time.monotonic() - started) * 1000),
            },
        )
        if usage is not None:
            self._record(
                "usage.recorded",
                {
                    **common,
                    "input_tokens": usage.input_tokens,
                    "output_tokens": usage.output_tokens,
                    "cache_read_tokens": usage.cache_read,
                    "cache_creation_tokens": usage.cache_creation,
                },
            )
