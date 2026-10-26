from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from lantu.tools.grep import Grep, Params


class FakeMCP:
    """Routes calls by zvec tool name; each name gets a fixed result or error."""

    def __init__(
        self,
        results: dict[str, object] | None = None,
        errors: dict[str, Exception] | None = None,
    ) -> None:
        self.results = results or {}
        self.errors = errors or {}
        self.calls: list[tuple[str, str, dict]] = []

    async def call_tool(self, server: str, name: str, arguments: dict) -> object:
        self.calls.append((server, name, arguments))
        if name in self.errors:
            raise self.errors[name]
        if name in self.results:
            return self.results[name]
        raise AssertionError(f"unexpected MCP tool call: {name}")


def mcp_result(text: str, is_error: bool = False) -> object:
    return SimpleNamespace(
        isError=is_error,
        content=[SimpleNamespace(text=text)],
    )


@pytest.mark.asyncio
async def test_exact_uses_zvec_grep_rg_first(tmp_path: Path) -> None:
    tool = Grep(str(tmp_path))
    manager = FakeMCP(results={"zvec_grep_rg": mcp_result("src/app.py:2:needle")})
    tool.set_mcp_manager(manager)

    result = await tool.execute(Params(pattern="needle"))

    assert result.output == "src/app.py:2:needle"
    assert result.meta == {"backend": "zvec_grep_rg", "degraded": False}
    assert manager.calls[0][1] == "zvec_grep_rg"


@pytest.mark.asyncio
async def test_mcp_error_falls_back_to_python_grep(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "app.py").write_text("x = 1  # needle\n")
    tool = Grep(str(tmp_path))
    tool.set_mcp_manager(FakeMCP(errors={"zvec_grep_rg": RuntimeError("offline")}))
    monkeypatch.setattr("lantu.tools.grep.shutil.which", lambda _name: None)

    result = await tool.execute(Params(pattern="needle"))

    assert "app.py:1:" in result.output and "needle" in result.output
    assert result.meta == {"backend": "python_grep", "degraded": True}


@pytest.mark.asyncio
async def test_without_manager_uses_host_rg_or_python(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "app.py").write_text("x = 1  # needle\n")
    tool = Grep(str(tmp_path))
    monkeypatch.setattr("lantu.tools.grep.shutil.which", lambda _name: None)

    result = await tool.execute(Params(pattern="needle"))

    assert "app.py:1:" in result.output and "needle" in result.output
    assert result.meta == {"backend": "python_grep", "degraded": True}


@pytest.mark.asyncio
async def test_empty_result_retries_semantic_index(tmp_path: Path) -> None:
    tool = Grep(str(tmp_path))
    manager = FakeMCP(
        results={
            "zvec_grep_rg": mcp_result(""),
            "zvec_grep_search": mcp_result("src/auth.py:10:validate_credentials()"),
        }
    )
    tool.set_mcp_manager(manager)

    result = await tool.execute(Params(pattern="credential validation flow"))

    assert "src/auth.py:10" in result.output
    assert "semantic index" in result.output
    assert result.meta == {
        "backend": "zvec_grep_search",
        "degraded": False,
        "semantic_retry": True,
    }
    assert [call[1] for call in manager.calls] == ["zvec_grep_rg", "zvec_grep_search"]


@pytest.mark.asyncio
async def test_nonempty_result_skips_semantic_retry(tmp_path: Path) -> None:
    tool = Grep(str(tmp_path))
    manager = FakeMCP(results={"zvec_grep_rg": mcp_result("src/app.py:1:needle")})
    tool.set_mcp_manager(manager)

    result = await tool.execute(Params(pattern="needle"))

    assert result.output == "src/app.py:1:needle"
    assert len(manager.calls) == 1


@pytest.mark.asyncio
async def test_semantic_retry_failure_returns_exact_empty(tmp_path: Path) -> None:
    tool = Grep(str(tmp_path))
    manager = FakeMCP(
        results={"zvec_grep_rg": mcp_result("No matches found.")},
        errors={"zvec_grep_search": RuntimeError("index missing")},
    )
    tool.set_mcp_manager(manager)

    result = await tool.execute(Params(pattern="missing-thing"))

    assert result.output == "No matches found."
    assert result.meta == {"backend": "zvec_grep_rg", "degraded": False}


@pytest.mark.asyncio
async def test_invalid_regex_reports_error_without_retry(tmp_path: Path) -> None:
    tool = Grep(str(tmp_path))
    manager = FakeMCP(results={"zvec_grep_rg": mcp_result("(no output)", is_error=True)})
    tool.set_mcp_manager(manager)

    result = await tool.execute(Params(pattern="("))

    assert result.is_error is True
    assert "invalid regex" in result.output
