from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from lantu.memory.journal import JournalEvent


@dataclass(frozen=True)
class CaptureRecord:
    timestamp: str
    session_id: str
    model_call_id: str | None
    provider: str | None
    model: str | None
    request: dict[str, Any]
    response: dict[str, Any]


@dataclass(frozen=True)
class EvidenceLink:
    model_call_id: str
    journal_sequence: int
    confidence: str
    capture: CaptureRecord | None


class CaptureStore:
    def __init__(self, work_dir: str | Path) -> None:
        self.root = Path(work_dir).resolve() / ".lantu" / "lens" / "capture"

    def read(self, session_id: str) -> list[CaptureRecord]:
        path = self.root / f"{session_id}.jsonl"
        if not path.is_file():
            return []
        records: list[CaptureRecord] = []
        with path.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, 1):
                if not line.strip():
                    continue
                try:
                    data = json.loads(line)
                    records.append(CaptureRecord(**data))
                except (json.JSONDecodeError, TypeError) as exc:
                    raise ValueError(f"{path}: invalid capture at line {line_number}") from exc
        return records


def correlate_evidence(
    journal_events: Iterable[JournalEvent], captures: Iterable[CaptureRecord]
) -> list[EvidenceLink]:
    started = [event for event in journal_events if event.type == "model.request.started"]
    remaining = list(captures)
    links: list[EvidenceLink] = []
    for event in started:
        call_id = str(event.payload.get("model_call_id", ""))
        exact = next((item for item in remaining if item.model_call_id == call_id), None)
        if exact is not None:
            remaining.remove(exact)
            links.append(EvidenceLink(call_id, event.sequence, "exact", exact))
            continue
        fallback = _nearest_compatible(event, remaining)
        if fallback is not None:
            remaining.remove(fallback)
            links.append(EvidenceLink(call_id, event.sequence, "low", fallback))
        else:
            links.append(EvidenceLink(call_id, event.sequence, "missing", None))
    return links


def _nearest_compatible(
    event: JournalEvent, captures: list[CaptureRecord]
) -> CaptureRecord | None:
    provider = str(event.payload.get("provider", ""))
    model = str(event.payload.get("model", ""))
    event_time = datetime.fromisoformat(event.timestamp.replace("Z", "+00:00"))
    candidates: list[tuple[float, CaptureRecord]] = []
    for capture in captures:
        if capture.provider and provider and capture.provider != provider:
            continue
        if capture.model and model and capture.model != model:
            continue
        capture_time = datetime.fromisoformat(capture.timestamp.replace("Z", "+00:00"))
        distance = abs((capture_time - event_time).total_seconds())
        if distance <= 30:
            candidates.append((distance, capture))
    candidates.sort(key=lambda item: item[0])
    return candidates[0][1] if candidates else None
