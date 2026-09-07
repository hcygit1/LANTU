from pathlib import Path

import pytest

from lantu.tools.impl.memory_search import MemorySearchParams, MemorySearchTool


@pytest.mark.asyncio
async def test_memory_search_returns_full_matching_memory(tmp_path: Path) -> None:
    root = tmp_path / "memory"
    root.mkdir()
    (root / "db.md").write_text(
        "---\nname: db\ndescription: database rules\ntype: project\n---\n\nUse PostgreSQL SSL.\n",
        encoding="utf-8",
    )
    tool = MemorySearchTool(None, root)
    result = await tool.execute(MemorySearchParams(query="PostgreSQL"))
    assert result.is_error is False
    assert "Use PostgreSQL SSL." in result.output
    assert result.meta == {"count": 1}


@pytest.mark.asyncio
async def test_memory_search_does_not_return_unmatched_memory(tmp_path: Path) -> None:
    root = tmp_path / "memory"
    root.mkdir()
    (root / "db.md").write_text("database content", encoding="utf-8")
    tool = MemorySearchTool(None, root)
    result = await tool.execute(MemorySearchParams(query="unmatched"))
    assert "No memories matched" in result.output
