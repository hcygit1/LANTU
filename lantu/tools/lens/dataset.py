from __future__ import annotations

import json
import re
from dataclasses import asdict
from pathlib import Path
from typing import Any, Iterable

from lantu.tools.lens.annotations import LensAnnotation
from lantu.tools.lens.tasks import TaskSegment


_SENSITIVE_KEYS = ("authorization", "api_key", "apikey", "token", "secret", "password")
_SECRET_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b"),
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._-]+"),
)


def redact(value: Any, key: str = "") -> Any:
    if any(name in key.casefold() for name in _SENSITIVE_KEYS):
        return "[REDACTED]"
    if isinstance(value, dict):
        return {item_key: redact(item, str(item_key)) for item_key, item in value.items()}
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, tuple):
        return [redact(item) for item in value]
    if isinstance(value, str):
        result = value
        for pattern in _SECRET_PATTERNS:
            result = pattern.sub("[REDACTED]", result)
        return result
    return value


def export_dataset(
    path: str | Path,
    tasks: Iterable[TaskSegment],
    annotations: Iterable[LensAnnotation] = (),
    *,
    redact_sensitive: bool = True,
) -> int:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    annotations_by_target: dict[str, list[dict[str, Any]]] = {}
    for annotation in annotations:
        annotations_by_target.setdefault(annotation.target_id, []).append(asdict(annotation))
    count = 0
    with destination.open("w", encoding="utf-8", newline="\n") as handle:
        for task in tasks:
            record: dict[str, Any] = {
                "schema_version": 1,
                "session_id": task.session_id,
                "task_id": task.task_id,
                "start_sequence": task.start_sequence,
                "end_sequence": task.end_sequence,
                "events": [asdict(event) for event in task.events],
                "annotations": annotations_by_target.get(task.task_id, []),
            }
            if redact_sensitive:
                record = redact(record)
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            count += 1
    return count
