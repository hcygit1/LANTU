from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any, Iterable

from lantu.tools.lens.action_graph import ActionGraph
from lantu.tools.lens.diagnosis import Finding
from lantu.tools.lens.normalized import NormalizedEvent


@dataclass(frozen=True)
class SessionSnapshot:
    session_id: str
    event_count: int
    event_types: dict[str, int]
    action_statuses: dict[str, int]
    finding_codes: dict[str, int]


@dataclass(frozen=True)
class SessionComparison:
    left: SessionSnapshot
    right: SessionSnapshot
    deltas: dict[str, int]


def snapshot_session(
    session_id: str,
    events: Iterable[NormalizedEvent],
    action_graphs: Iterable[ActionGraph],
    findings: Iterable[Finding],
) -> SessionSnapshot:
    event_list = list(events)
    return SessionSnapshot(
        session_id=session_id,
        event_count=len(event_list),
        event_types=dict(Counter(event.source_type for event in event_list)),
        action_statuses=dict(
            Counter(node.status for graph in action_graphs for node in graph.nodes)
        ),
        finding_codes=dict(Counter(item.code for item in findings)),
    )


def compare_snapshots(left: SessionSnapshot, right: SessionSnapshot) -> SessionComparison:
    deltas: dict[str, int] = {"event_count": right.event_count - left.event_count}
    for prefix, left_values, right_values in (
        ("event", left.event_types, right.event_types),
        ("action", left.action_statuses, right.action_statuses),
        ("finding", left.finding_codes, right.finding_codes),
    ):
        for key in sorted(set(left_values) | set(right_values)):
            deltas[f"{prefix}.{key}"] = right_values.get(key, 0) - left_values.get(key, 0)
    return SessionComparison(left=left, right=right, deltas=deltas)
