from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from lantu.memory.journal import JournalEvent


@dataclass(frozen=True)
class WindowSegment:
    window_id: str
    parent_window_id: str | None
    start_sequence: int
    end_sequence: int
    is_active: bool
    events: tuple[JournalEvent, ...]


def segment_windows(events: Iterable[JournalEvent]) -> list[WindowSegment]:
    """Project one Session Journal into ordered Conversation windows."""
    event_list = list(events)
    if not event_list:
        return []

    created = next((event for event in event_list if event.type == "session.created"), None)
    initial_id = (
        str(created.payload.get("window_id"))
        if created is not None and created.payload.get("window_id")
        else "window_legacy"
    )
    groups: list[tuple[str, str | None, list[JournalEvent]]] = [
        (initial_id, None, [])
    ]

    for event in event_list:
        if event.type == "context.window.rolled_over":
            window_id = str(event.payload.get("window_id") or f"window_{event.sequence}")
            parent_id = str(event.payload.get("parent_window_id") or groups[-1][0])
            groups.append((window_id, parent_id, [event]))
        else:
            groups[-1][2].append(event)

    segments: list[WindowSegment] = []
    for index, (window_id, parent_id, window_events) in enumerate(groups):
        if not window_events:
            continue
        segments.append(
            WindowSegment(
                window_id=window_id,
                parent_window_id=parent_id,
                start_sequence=window_events[0].sequence,
                end_sequence=window_events[-1].sequence,
                is_active=index == len(groups) - 1,
                events=tuple(window_events),
            )
        )
    return segments
