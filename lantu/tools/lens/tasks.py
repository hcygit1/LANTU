from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from lantu.tools.lens.normalized import NormalizedEvent


@dataclass(frozen=True)
class TaskSegment:
    task_id: str
    session_id: str
    start_sequence: int
    end_sequence: int
    events: tuple[NormalizedEvent, ...]


def segment_tasks(events: Iterable[NormalizedEvent]) -> list[TaskSegment]:
    """Group the first version of Lens Tasks by turn lifecycle."""
    tasks: list[TaskSegment] = []
    current: list[NormalizedEvent] = []
    task_number = 0

    def finish() -> None:
        nonlocal current, task_number
        if not current:
            return
        task_number += 1
        tasks.append(
            TaskSegment(
                task_id=f"task_{task_number}",
                session_id=current[0].session_id,
                start_sequence=current[0].sequence,
                end_sequence=current[-1].sequence,
                events=tuple(current),
            )
        )
        current = []

    for event in events:
        if (
            event.source_type == "turn.started"
            and current
            and any(item.source_type == "turn.started" for item in current)
        ):
            finish()
        current.append(event)
        if event.source_type in {"turn.completed", "turn.interrupted"}:
            finish()
    finish()
    return tasks
