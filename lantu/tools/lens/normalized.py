from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Iterator

from lantu.memory.journal import JournalEvent


@dataclass(frozen=True)
class NormalizedEvent:
    session_id: str
    sequence: int
    timestamp: str
    kind: str
    source_type: str
    payload: dict[str, Any]


_KINDS = {
    "message.created": "message",
    "tool.started": "tool",
    "tool.completed": "tool",
    "tool.failed": "tool",
    "tool.interrupted": "tool",
    "model.request.started": "model",
    "model.request.completed": "model",
    "model.request.failed": "model",
    "model.request.interrupted": "model",
    "permission.decided": "permission",
    "error.occurred": "error",
    "session.created": "lifecycle",
    "runtime.started": "lifecycle",
    "runtime.stopped": "lifecycle",
    "runtime.interrupted": "lifecycle",
    "turn.started": "lifecycle",
    "turn.completed": "lifecycle",
    "turn.interrupted": "lifecycle",
    "context.compacted": "lifecycle",
    "usage.recorded": "usage",
}


def normalize_event(event: JournalEvent) -> NormalizedEvent:
    """Convert one Journal envelope without changing its payload."""
    return NormalizedEvent(
        session_id=event.session_id,
        sequence=event.sequence,
        timestamp=event.timestamp,
        kind=_KINDS.get(event.type, "unknown"),
        source_type=event.type,
        payload=dict(event.payload),
    )


def normalize_events(events: Iterable[JournalEvent]) -> Iterator[NormalizedEvent]:
    for event in events:
        yield normalize_event(event)
