from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Iterable

from lantu.tools.lens.diagnosis import Finding, diagnose_graph
from lantu.tools.lens.graph import EventGraph
from lantu.tools.lens.normalized import NormalizedEvent


@dataclass(frozen=True)
class DiagnosisReport:
    session_id: str
    findings: tuple[Finding, ...]
    evidence: dict[int, NormalizedEvent]

    @property
    def status(self) -> str:
        if any(item.severity == "error" for item in self.findings):
            return "error"
        if self.findings:
            return "warning"
        return "ok"

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "status": self.status,
            "findings": [asdict(item) for item in self.findings],
            "evidence": {
                str(sequence): asdict(event)
                for sequence, event in sorted(self.evidence.items())
            },
        }

    def render_text(self) -> str:
        lines = [f"Session: {self.session_id}", f"Status: {self.status}"]
        if not self.findings:
            lines.append("No failures or incomplete operations found.")
            return "\n".join(lines)
        for finding in self.findings:
            sequences = ", ".join(str(item) for item in finding.evidence_sequences)
            lines.append(f"[{finding.severity}] {finding.code}: {finding.message}")
            for sequence in finding.evidence_sequences:
                event = self.evidence.get(sequence)
                if event is not None:
                    lines.append(f"  evidence #{sequence}: {event.source_type} {event.payload}")
            if not finding.evidence_sequences:
                lines.append(f"  evidence: {sequences}")
        return "\n".join(lines)


def build_report(session_id: str, graphs: Iterable[EventGraph]) -> DiagnosisReport:
    findings: list[Finding] = []
    events: dict[int, NormalizedEvent] = {}
    for graph in graphs:
        findings.extend(diagnose_graph(graph))
        events.update((event.sequence, event) for event in graph.nodes)
    evidence_sequences = {
        sequence for finding in findings for sequence in finding.evidence_sequences
    }
    return DiagnosisReport(
        session_id=session_id,
        findings=tuple(findings),
        evidence={sequence: events[sequence] for sequence in evidence_sequences if sequence in events},
    )
