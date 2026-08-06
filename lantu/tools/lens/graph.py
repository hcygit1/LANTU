from __future__ import annotations

from dataclasses import dataclass

from lantu.tools.lens.normalized import NormalizedEvent
from lantu.tools.lens.tasks import TaskSegment


@dataclass(frozen=True)
class EventGraph:
    nodes: tuple[NormalizedEvent, ...]
    edges: tuple[tuple[int, int, str], ...]


def build_event_graph(task: TaskSegment) -> EventGraph:
    """Build deterministic evidence links for one Task."""
    nodes = task.events
    edges: list[tuple[int, int, str]] = []
    by_key: dict[tuple[str, str], int] = {}

    for index, node in enumerate(nodes):
        if index:
            edges.append((nodes[index - 1].sequence, node.sequence, "next"))
        for key_name in ("tool_call_id", "model_call_id"):
            key = str(node.payload.get(key_name, ""))
            if key:
                previous = by_key.get((key_name, key))
                if previous is not None:
                    edges.append((nodes[previous].sequence, node.sequence, "same_operation"))
                else:
                    by_key[(key_name, key)] = index
    return EventGraph(nodes=nodes, edges=tuple(edges))
