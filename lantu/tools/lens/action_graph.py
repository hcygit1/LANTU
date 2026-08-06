from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from lantu.tools.lens.tasks import TaskSegment


@dataclass(frozen=True)
class ActionNode:
    action_id: str
    kind: str
    status: str
    start_sequence: int
    end_sequence: int
    details: dict[str, Any]


@dataclass(frozen=True)
class ActionGraph:
    nodes: tuple[ActionNode, ...]
    edges: tuple[tuple[str, str], ...]


def build_action_graph(task: TaskSegment) -> ActionGraph:
    actions: list[ActionNode] = []
    pending: dict[tuple[str, str], int] = {}

    for event in task.events:
        operation = _operation(event.source_type, event.payload)
        if operation is not None:
            kind, operation_id, phase = operation
            key = (kind, operation_id)
            if phase == "started":
                pending[key] = len(actions)
                actions.append(
                    ActionNode(
                        action_id=operation_id,
                        kind=kind,
                        status="incomplete",
                        start_sequence=event.sequence,
                        end_sequence=event.sequence,
                        details=dict(event.payload),
                    )
                )
            else:
                index = pending.pop(key, None)
                if index is not None:
                    started = actions[index]
                    actions[index] = ActionNode(
                        action_id=started.action_id,
                        kind=started.kind,
                        status=phase,
                        start_sequence=started.start_sequence,
                        end_sequence=event.sequence,
                        details={**started.details, **event.payload},
                    )
            continue
        if event.source_type == "message.created":
            message_id = str(event.payload.get("message_id", f"message_{event.sequence}"))
            actions.append(
                ActionNode(
                    action_id=message_id,
                    kind="message",
                    status="completed",
                    start_sequence=event.sequence,
                    end_sequence=event.sequence,
                    details=dict(event.payload),
                )
            )

    ordered = tuple(sorted(actions, key=lambda item: item.start_sequence))
    edges = tuple(
        (ordered[index - 1].action_id, node.action_id)
        for index, node in enumerate(ordered)
        if index
    )
    return ActionGraph(nodes=ordered, edges=edges)


def _operation(
    source_type: str, payload: dict[str, Any]
) -> tuple[str, str, str] | None:
    if source_type.startswith("tool."):
        operation_id = str(payload.get("tool_call_id", ""))
        kind = "tool"
    elif source_type.startswith("model.request."):
        operation_id = str(payload.get("model_call_id", ""))
        kind = "model"
    else:
        return None
    if not operation_id:
        return None
    phase = source_type.rsplit(".", 1)[-1]
    status = {
        "started": "started",
        "completed": "completed",
        "failed": "failed",
        "interrupted": "interrupted",
    }.get(phase)
    if status is None:
        return None
    return kind, operation_id, status
