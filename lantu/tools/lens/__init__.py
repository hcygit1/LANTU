"""Read-only analysis primitives for LANTU sessions."""

from lantu.tools.lens.normalized import NormalizedEvent, normalize_event, normalize_events
from lantu.tools.lens.reader import LensReader
from lantu.tools.lens.graph import EventGraph, build_event_graph
from lantu.tools.lens.diagnosis import Finding, diagnose_graph
from lantu.tools.lens.report import DiagnosisReport, build_report
from lantu.tools.lens.action_graph import ActionGraph, ActionNode, build_action_graph
from lantu.tools.lens.annotations import AnnotationStore, LensAnnotation
from lantu.tools.lens.compare import (
    SessionComparison,
    SessionSnapshot,
    compare_snapshots,
    snapshot_session,
)
from lantu.tools.lens.dataset import export_dataset, redact
from lantu.tools.lens.replay import (
    ReplayPlan,
    ReplayResult,
    build_replay_plan,
    execute_capture_replay,
)
from lantu.tools.lens.capture import (
    CaptureRecord,
    CaptureStore,
    EvidenceLink,
    correlate_evidence,
)
from lantu.tools.lens.web import create_lens_app, run_lens_web
from lantu.tools.lens.tasks import TaskSegment, segment_tasks

__all__ = [
    "LensReader",
    "NormalizedEvent",
    "normalize_event",
    "normalize_events",
    "TaskSegment",
    "segment_tasks",
    "EventGraph",
    "build_event_graph",
    "Finding",
    "diagnose_graph",
    "DiagnosisReport",
    "build_report",
    "ActionGraph",
    "ActionNode",
    "build_action_graph",
    "AnnotationStore",
    "LensAnnotation",
    "SessionComparison",
    "SessionSnapshot",
    "compare_snapshots",
    "snapshot_session",
    "export_dataset",
    "redact",
    "ReplayPlan",
    "build_replay_plan",
    "ReplayResult",
    "execute_capture_replay",
    "CaptureRecord",
    "CaptureStore",
    "EvidenceLink",
    "correlate_evidence",
    "create_lens_app",
    "run_lens_web",
]
