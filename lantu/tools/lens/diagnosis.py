from __future__ import annotations

from dataclasses import dataclass

from lantu.tools.lens.graph import EventGraph


@dataclass(frozen=True)
class Finding:
    code: str
    severity: str
    message: str
    evidence_sequences: tuple[int, ...]


def diagnose_graph(graph: EventGraph) -> list[Finding]:
    findings: list[Finding] = []
    terminal_types = {
        "model.request.completed",
        "model.request.failed",
        "model.request.interrupted",
    }
    tool_terminal_types = {"tool.completed", "tool.failed", "tool.interrupted"}
    model_starts: dict[str, int] = {}
    tool_starts: dict[str, int] = {}

    for event in graph.nodes:
        if event.source_type == "model.request.started":
            call_id = str(event.payload.get("model_call_id", ""))
            if call_id:
                model_starts[call_id] = event.sequence
        elif event.source_type in terminal_types:
            call_id = str(event.payload.get("model_call_id", ""))
            model_starts.pop(call_id, None)
            if event.source_type.endswith("failed"):
                findings.append(
                    Finding("model_failed", "error", "Model request failed", (event.sequence,))
                )
            elif event.source_type.endswith("interrupted"):
                findings.append(
                    Finding("model_interrupted", "warning", "Model request was interrupted", (event.sequence,))
                )
        elif event.source_type == "tool.started":
            call_id = str(event.payload.get("tool_call_id", ""))
            if call_id:
                tool_starts[call_id] = event.sequence
        elif event.source_type in tool_terminal_types:
            call_id = str(event.payload.get("tool_call_id", ""))
            tool_starts.pop(call_id, None)
            if event.source_type.endswith("failed"):
                findings.append(
                    Finding("tool_failed", "error", "Tool execution failed", (event.sequence,))
                )
            elif event.source_type.endswith("interrupted"):
                findings.append(
                    Finding("tool_interrupted", "warning", "Tool execution was interrupted", (event.sequence,))
                )

    for call_id, sequence in model_starts.items():
        findings.append(
            Finding("model_incomplete", "warning", "Model request has no terminal event", (sequence,))
        )
    for call_id, sequence in tool_starts.items():
        findings.append(
            Finding("tool_incomplete", "warning", "Tool call has no terminal event", (sequence,))
        )
    return findings
