from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from lantu.tools.base import Tool, ToolResult
from lantu.tools.grep import Grep, Params as GrepParams, _mcp_text


class SemanticSearchParams(BaseModel):
    query: str = Field(description="Natural-language question describing what you are looking for")
    path: str = Field(default=".", description="Directory or file to search")
    include: str = Field(default="", description="Optional filename glob")
    limit: int = Field(default=10, ge=1, le=50)


class SemanticSearch(Tool):
    """Index-backed semantic retrieval over the project, via zg (zvec_grep_search)."""

    name = "SemanticSearch"
    description = """\
Search this project's index by meaning. Use it when you are looking for a concept rather than a literal string: architecture, call chains, dependencies, lifecycle, data or control flow, design rationale, or cross-file comparisons — anything where you do not know the exact wording or location, or the answer spans files.

Pass a natural-language question, not a regex. For literal strings, symbols, filenames, or regex lookup use Grep instead.

The project already has a search index at its root, so this works immediately; do not try to build one. It covers the indexed project only — anything outside it (system headers, installed packages, other checkouts) is not indexed; reach those with Bash.

Results are bounded; verify with ReadFile before editing."""
    params_model = SemanticSearchParams
    category = "read"
    is_concurrency_safe = True

    def __init__(self, work_dir: str | None = None, grep: Grep | None = None) -> None:
        self.work_dir = work_dir
        self._grep = grep or Grep(work_dir)
        self._mcp_manager: Any = None
        self.mcp_server = "zvec_grep"

    def set_mcp_manager(self, manager: Any) -> None:
        self._mcp_manager = manager

    async def execute(self, params: BaseModel) -> ToolResult:
        assert isinstance(params, SemanticSearchParams)
        if not params.query.strip():
            return ToolResult(output="Error: query cannot be empty", is_error=True)

        if self._mcp_manager is None:
            fallback = await self._fallback(params)
            fallback.meta = {**(fallback.meta or {}), "semantic_fallback": True}
            return fallback
        arguments: dict[str, Any] = {
            "root": str(Path(self.work_dir or ".").resolve()),
            "query": params.query,
            "limit": params.limit,
        }
        if params.include:
            arguments["globs"] = [params.include]
        try:
            result = await self._mcp_manager.call_tool(
                self.mcp_server, "zvec_grep_search", arguments
            )
            if getattr(result, "isError", False):
                raise RuntimeError(_mcp_text(result))
            return ToolResult(
                output=_mcp_text(result),
                meta={"backend": "zvec_grep_search", "degraded": False},
            )
        except Exception as exc:
            fallback = await self._fallback(params)
            fallback.meta = {**(fallback.meta or {}), "semantic_error": str(exc)}
            return fallback

    async def _fallback(self, params: SemanticSearchParams) -> ToolResult:
        """zg unavailable: fall through Grep's exact chain (managed rg → host rg → Python)."""
        self._grep.work_dir = self.work_dir
        fallback = await self._grep.execute(
            GrepParams(pattern=params.query, path=params.path, include=params.include)
        )
        fallback.meta = {
            **(fallback.meta or {}),
            "backend": "grep_exact_fallback",
            "degraded": True,
        }
        return fallback
