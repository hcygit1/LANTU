from __future__ import annotations

from types import SimpleNamespace
from pathlib import Path

import pytest

from lantu.tools.base import ToolResult
from lantu.tools.code_search import CodeSearch, CodeSearchParams


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


@pytest.mark.asyncio
async def test_exact_uses_zvec_grep_rg_first(tmp_path: Path) -> None:
    tool = CodeSearch(str(tmp_path))
    manager = FakeMCP(mcp_result("src/app.py:2:needle"))
    tool.set_mcp_manager(manager)

    result = await tool.execute(CodeSearchParams(query="needle", mode="exact"))

    assert result.output == "src/app.py:2:needle"
    assert result.meta == {"backend": "zvec_grep_rg", "degraded": False}
    assert manager.calls[0][1] == "zvec_grep_rg"


@pytest.mark.asyncio
async def test_exact_falls_back_to_python_grep_when_mcp_and_rg_fail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tool = CodeSearch(str(tmp_path))
    tool.set_mcp_manager(FakeMCP(error=RuntimeError("offline")))
    monkeypatch.setattr("lantu.tools.code_search.shutil.which", lambda _name: None)
    tool._grep.execute = lambda _params: _fallback_result()  # type: ignore[method-assign]

    result = await tool.execute(CodeSearchParams(query="needle", mode="exact"))

    assert result.output == "fallback"
    assert result.meta == {"backend": "python_grep", "degraded": True}


async def _fallback_result() -> ToolResult:
    return ToolResult(output="fallback")


@pytest.mark.asyncio
async def test_semantic_uses_zvec_search(tmp_path: Path) -> None:
    tool = CodeSearch(str(tmp_path))
    manager = FakeMCP(mcp_result("src/auth.py:10-20"))
    tool.set_mcp_manager(manager)

    result = await tool.execute(
        CodeSearchParams(query="where are credentials validated?", mode="semantic")
    )

    assert result.meta == {"backend": "zvec_grep_search", "degraded": False}
    assert manager.calls[0][1] == "zvec_grep_search"


@pytest.mark.asyncio
async def test_semantic_falls_back_to_python_grep_when_zvec_and_rg_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tool = CodeSearch(str(tmp_path))
    tool.set_mcp_manager(FakeMCP(error=RuntimeError("offline")))
    monkeypatch.setattr("lantu.tools.code_search.shutil.which", lambda _name: None)
    tool._grep.execute = lambda _params: _fallback_result()  # type: ignore[method-assign]

    result = await tool.execute(
        CodeSearchParams(query="where are credentials validated?", mode="semantic")
    )

    assert result.output == "fallback"
    assert result.meta["backend"] == "python_grep"
    assert result.meta["semantic_fallback"] is True
