from __future__ import annotations

from pathlib import Path
from typing import Iterator

from lantu.memory.journal import JournalEvent, SessionJournal
from lantu.tools.lens.normalized import NormalizedEvent, normalize_events
from lantu.tools.lens.tasks import TaskSegment, segment_tasks
from lantu.tools.lens.graph import EventGraph, build_event_graph
from lantu.tools.lens.diagnosis import Finding, diagnose_graph
from lantu.tools.lens.report import DiagnosisReport, build_report
from lantu.tools.lens.action_graph import ActionGraph, build_action_graph
from lantu.tools.lens.compare import SessionComparison, compare_snapshots, snapshot_session
from lantu.tools.lens.annotations import AnnotationStore
from lantu.tools.lens.dataset import export_dataset
from lantu.tools.lens.replay import ReplayPlan, ReplayResult, build_replay_plan, execute_capture_replay
from lantu.tools.lens.capture import CaptureStore, EvidenceLink, correlate_evidence


class LensReader:
    """Read LANTU Journal files without changing or projecting the source."""

    def __init__(self, work_dir: str | Path) -> None:
        self.work_dir = Path(work_dir).resolve()
        self.sessions_dir = self.work_dir / ".lantu" / "sessions"

    def session_ids(self) -> list[str]:
        return sorted(
            path.stem for path in self.sessions_dir.glob("*.jsonl") if path.is_file()
        )

    def read(self, session_id: str) -> list[JournalEvent]:
        path = self.sessions_dir / f"{session_id}.jsonl"
        if not path.is_file():
            raise FileNotFoundError(path)
        return SessionJournal.read_file(path)

    def iter_events(self, session_id: str) -> Iterator[JournalEvent]:
        yield from self.read(session_id)

    def normalized_events(self, session_id: str) -> Iterator[NormalizedEvent]:
        yield from normalize_events(self.iter_events(session_id))

    def tasks(self, session_id: str) -> list[TaskSegment]:
        return segment_tasks(self.normalized_events(session_id))

    def graphs(self, session_id: str) -> list[EventGraph]:
        return [build_event_graph(task) for task in self.tasks(session_id)]

    def diagnose(self, session_id: str) -> list[Finding]:
        findings: list[Finding] = []
        for graph in self.graphs(session_id):
            findings.extend(diagnose_graph(graph))
        return findings

    def report(self, session_id: str) -> DiagnosisReport:
        return build_report(session_id, self.graphs(session_id))

    def action_graphs(self, session_id: str) -> list[ActionGraph]:
        return [build_action_graph(task) for task in self.tasks(session_id)]

    def compare(self, left_session_id: str, right_session_id: str) -> SessionComparison:
        left = snapshot_session(
            left_session_id,
            self.normalized_events(left_session_id),
            self.action_graphs(left_session_id),
            self.diagnose(left_session_id),
        )
        right = snapshot_session(
            right_session_id,
            self.normalized_events(right_session_id),
            self.action_graphs(right_session_id),
            self.diagnose(right_session_id),
        )
        return compare_snapshots(left, right)

    def export_dataset(
        self, session_id: str, path: str | Path, *, redact_sensitive: bool = True
    ) -> int:
        return export_dataset(
            path,
            self.tasks(session_id),
            AnnotationStore(self.work_dir).read(session_id),
            redact_sensitive=redact_sensitive,
        )

    def replay_plan(self, session_id: str, task_id: str) -> ReplayPlan:
        task = next((item for item in self.tasks(session_id) if item.task_id == task_id), None)
        if task is None:
            raise KeyError(f"task not found: {task_id}")
        return build_replay_plan(task)

    def execute_replay(
        self, session_id: str, task_id: str, api_key: str
    ) -> ReplayResult:
        plan = self.replay_plan(session_id, task_id)
        call_ids = {
            str(item.get("model_call_id", "")) for item in plan.model_calls
        }
        capture = next(
            (
                item
                for item in CaptureStore(self.work_dir).read(session_id)
                if item.model_call_id in call_ids
            ),
            None,
        )
        if capture is None:
            raise ValueError("exact capture evidence is required for replay")
        return execute_capture_replay(capture, api_key)

    def evidence_links(self, session_id: str) -> list[EvidenceLink]:
        return correlate_evidence(
            self.read(session_id), CaptureStore(self.work_dir).read(session_id)
        )

    def list_events(self) -> Iterator[tuple[str, JournalEvent]]:
        for session_id in self.session_ids():
            for event in self.iter_events(session_id):
                yield session_id, event

    def search(
        self, query: str, session_id: str | None = None
    ) -> Iterator[tuple[str, JournalEvent]]:
        """Yield events whose envelope or payload contains ``query``."""
        needle = query.casefold()
        session_ids = [session_id] if session_id else self.session_ids()
        for current_id in session_ids:
            for event in self.iter_events(current_id):
                haystack = repr(event.payload).casefold()
                if needle in event.type.casefold() or needle in haystack:
                    yield current_id, event
