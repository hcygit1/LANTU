from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from lantu.tools.base import ToolResult
from lantu.tools.code_search import SemanticSearch, SemanticSearchParams


class FakeMCP:
    def __init__(self, result: object | None = None, error: Exception | None = None) -> None:
        self.result = result
        self.error = error
        self.calls: list[tuple[str, str, dict]] = []

    async def call_tool(self, server: str, name: str, arguments: dict) -> object:
        self.calls.append((server, name, arguments))
        if self.error:
            raise self.error
        return self.result


def mcp_result(text: str, is_error: bool = False) -> object:
    return SimpleNamespace(
        isError=is_error,
        content=[SimpleNamespace(text=text)],
    )


async def _fallback_result() -> ToolResult:
    return ToolResult(output="fallback")


@pytest.mark.asyncio
async def test_semantic_uses_zvec_search(tmp_path: Path) -> None:
    tool = SemanticSearch(str(tmp_path))
    manager = FakeMCP(mcp_result("src/auth.py:10-20"))
    tool.set_mcp_manager(manager)

    result = await tool.execute(
        SemanticSearchParams(query="where are credentials validated?")
    )

    assert result.output == "src/auth.py:10-20"
    assert result.meta == {"backend": "zvec_grep_search", "degraded": False}
    assert manager.calls[0][1] == "zvec_grep_search"


@pytest.mark.asyncio
async def test_semantic_error_falls_back_to_grep_chain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tool = SemanticSearch(str(tmp_path))
    tool.set_mcp_manager(FakeMCP(error=RuntimeError("offline")))
    monkeypatch.setattr("lantu.tools.grep.shutil.which", lambda _name: None)
    tool._grep.execute = lambda _params: _fallback_result()  # type: ignore[method-assign]

    result = await tool.execute(
        SemanticSearchParams(query="where are credentials validated?")
    )

    assert result.output == "fallback"
    assert result.meta["backend"] == "grep_exact_fallback"
    assert result.meta["degraded"] is True
    assert result.meta["semantic_error"] == "offline"


@pytest.mark.asyncio
async def test_without_manager_falls_back_to_grep_chain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tool = SemanticSearch(str(tmp_path))
    monkeypatch.setattr("lantu.tools.grep.shutil.which", lambda _name: None)
    tool._grep.execute = lambda _params: _fallback_result()  # type: ignore[method-assign]

    result = await tool.execute(
        SemanticSearchParams(query="where are credentials validated?")
    )

    assert result.output == "fallback"
    assert result.meta["backend"] == "grep_exact_fallback"
    assert result.meta["degraded"] is True
    assert result.meta["semantic_fallback"] is True


@pytest.mark.asyncio
async def test_empty_query_is_rejected(tmp_path: Path) -> None:
    tool = SemanticSearch(str(tmp_path))

    result = await tool.execute(SemanticSearchParams(query="   "))

    assert result.is_error is True
    assert "empty" in result.output
