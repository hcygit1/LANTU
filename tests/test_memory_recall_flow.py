from pathlib import Path

import pytest

from lantu.memory.recall import find_relevant_memories, render_reminder


def _write_memory(root: Path, filename: str, description: str, body: str) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / filename).write_text(
        f"---\nname: {filename[:-3]}\ndescription: {description}\ntype: project\n---\n\n{body}\n",
        encoding="utf-8",
    )


@pytest.mark.asyncio
async def test_recall_prefilters_then_calls_selector(tmp_path: Path) -> None:
    memory_dir = tmp_path / "memory"
    _write_memory(memory_dir, "db.md", "PostgreSQL connection rules", "use ssl")
    _write_memory(memory_dir, "ui.md", "frontend color preferences", "use blue")
    calls: list[str] = []

    async def selector(_system: str, message: str) -> str:
        calls.append(message)
        assert "db.md" in message
        assert "ui.md" not in message
        return '{"selected_memories":["db.md"]}'

    result = await find_relevant_memories(
        "PostgreSQL", None, memory_dir, None, None, selector
    )
    assert len(calls) == 1
    assert result[0].filename == "db.md"


@pytest.mark.asyncio
async def test_recall_skips_selector_without_candidates(tmp_path: Path) -> None:
    memory_dir = tmp_path / "memory"
    _write_memory(memory_dir, "db.md", "PostgreSQL connection rules", "use ssl")
    called = False

    async def selector(_system: str, _message: str) -> str:
        nonlocal called
        called = True
        return '{"selected_memories":["db.md"]}'

    result = await find_relevant_memories(
        "unrelated topic", None, memory_dir, None, None, selector
    )
    assert result == []
    assert called is False


def test_render_reminder_contains_summary_not_body() -> None:
    from lantu.memory.recall import RelevantMemory

    result = render_reminder(
        [RelevantMemory("/tmp/db.md", 0, "db.md", "db", "PostgreSQL rules", "project")]
    )
    assert "PostgreSQL rules" in result
    assert "memory_search" in result
    assert "/tmp/db.md" not in result
