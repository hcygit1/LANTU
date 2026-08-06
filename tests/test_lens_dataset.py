import json
from pathlib import Path

from lantu.tools.lens import NormalizedEvent, TaskSegment, export_dataset


def test_dataset_export_redacts_secrets_by_default(tmp_path: Path) -> None:
    event = NormalizedEvent(
        "session_a",
        1,
        "2026-01-01T00:00:00Z",
        "message",
        "message.created",
        {"content": "use sk-abcdefghijklmnop", "api_key": "plain-secret"},
    )
    task = TaskSegment("task_1", "session_a", 1, 1, (event,))
    output = tmp_path / "dataset.jsonl"
    assert export_dataset(output, [task]) == 1
    record = json.loads(output.read_text(encoding="utf-8"))
    payload = record["events"][0]["payload"]
    assert payload["api_key"] == "[REDACTED]"
    assert payload["content"] == "use [REDACTED]"
