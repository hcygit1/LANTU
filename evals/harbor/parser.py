"""Small, dependency-free parsers for LANTU evaluation output."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


TASK_COMPLETION_BOUNDARY = """

Evaluation boundary:
- Complete only the requested task.
- Once the requested output file or change is correct, stop immediately.
- Do not add extra tests, create unrelated files, or continue exploring after completion.
- Before stopping, make sure the requested output has been saved in the exact path from the task.
""".strip()


def build_task_instruction(instruction: str) -> str:
    """Add a clear stopping boundary to benchmark instructions."""
    return f"{instruction.rstrip()}\n\n{TASK_COMPLETION_BOUNDARY}"


@dataclass(frozen=True)
class LantuMetrics:
    input_tokens: int = 0
    output_tokens: int = 0
    duration_ms: int = 0
    cache_tokens: int = 0


def iter_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    """Yield valid JSON objects and ignore banners or truncated lines."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            yield value


def parse_metrics(path: Path) -> LantuMetrics:
    """Read the final ``result`` event, falling back to usage events."""
    last_usage: dict[str, Any] = {}
    result: dict[str, Any] | None = None
    for event in iter_jsonl(path):
        if event.get("type") == "usage":
            nested_usage = event.get("usage")
            last_usage = (
                nested_usage
                if isinstance(nested_usage, dict)
                else event
            )
        elif event.get("type") == "result":
            result = event

    usage = result.get("usage", {}) if result else last_usage
    if not isinstance(usage, dict):
        usage = {}
    return LantuMetrics(
        input_tokens=_int(usage.get("input_tokens")),
        output_tokens=_int(usage.get("output_tokens")),
        duration_ms=_int(result.get("duration_ms")) if result else 0,
        cache_tokens=_int(usage.get("cache_tokens")),
    )


def _int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0
