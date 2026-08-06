from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from filelock import FileLock


@dataclass(frozen=True)
class LensAnnotation:
    annotation_id: str
    session_id: str
    kind: str
    target_id: str
    value: dict[str, Any]
    created_at: str


class AnnotationStore:
    """Append-only Lens metadata that never changes the Session Journal."""

    def __init__(self, work_dir: str | Path) -> None:
        self.root = Path(work_dir).resolve() / ".lantu" / "lens" / "annotations"

    def add(
        self, session_id: str, kind: str, target_id: str, value: dict[str, Any]
    ) -> LensAnnotation:
        if kind not in {"task_boundary", "diagnosis", "dataset_label"}:
            raise ValueError(f"unsupported annotation kind: {kind}")
        annotation = LensAnnotation(
            annotation_id=f"annotation_{uuid.uuid4().hex}",
            session_id=session_id,
            kind=kind,
            target_id=target_id,
            value=dict(value),
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.root / f"{session_id}.jsonl"
        with FileLock(str(path) + ".lock"):
            with path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(json.dumps(asdict(annotation), ensure_ascii=False) + "\n")
                handle.flush()
        return annotation

    def read(self, session_id: str) -> list[LensAnnotation]:
        path = self.root / f"{session_id}.jsonl"
        if not path.is_file():
            return []
        annotations: list[LensAnnotation] = []
        with path.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, 1):
                if not line.strip():
                    continue
                try:
                    annotations.append(LensAnnotation(**json.loads(line)))
                except (json.JSONDecodeError, TypeError) as exc:
                    raise ValueError(f"{path}: invalid annotation at line {line_number}") from exc
        return annotations
