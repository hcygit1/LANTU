from __future__ import annotations

import asyncio
import json
import os
import shutil
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from lantu.tools.base import Tool, ToolResult
from lantu.tools.grep import Grep, Params as GrepParams


class CodeSearchParams(BaseModel):
    query: str = Field(description="Natural-language query, identifier, or regex")
    mode: str = Field(default="auto", description="auto, semantic, or exact")
    path: str = Field(default=".", description="Directory or file to search")
    include: str = Field(default="", description="Optional filename glob")
    limit: int = Field(default=10, ge=1, le=50)


def _mcp_text(result: Any) -> str:
    content = getattr(result, "content", None) or []
    parts = [getattr(item, "text", "") for item in content]
    return "\n".join(part for part in parts if part) or "(no output)"


class CodeSearch(Tool):
    """Route repository searches through zg, local rg, then Python Grep."""

    name = "CodeSearch"
    description = (
        "Search the current repository. Use semantic mode for conceptual or "
        "cross-file discovery and exact mode for identifiers, regex, or all matches. "
        "Results are bounded; use ReadFile to verify current source."
    )
    params_model = CodeSearchParams
    category = "read"
    is_concurrency_safe = True

    def __init__(self, work_dir: str | None = None, grep: Grep | None = None) -> None:
        self.work_dir = work_dir
        self._grep = grep or Grep()
        self._mcp_manager: Any = None
        self.mcp_server = "zvec_grep"

    def set_mcp_manager(self, manager: Any) -> None:
        self._mcp_manager = manager

    async def execute(self, params: BaseModel) -> ToolResult:
        assert isinstance(params, CodeSearchParams)
        mode = params.mode.casefold()
        if mode not in {"auto", "semantic", "exact"}:
            return ToolResult(output="Error: mode must be auto, semantic, or exact", is_error=True)
        if not params.query.strip():
            return ToolResult(output="Error: query cannot be empty", is_error=True)

        if mode == "semantic" or (mode == "auto" and self._looks_semantic(params.query)):
            return await self._semantic(params)
        return await self._exact(params)

    @staticmethod
    def _looks_semantic(query: str) -> bool:
        # Natural-language questions do not have a dependable exhaustive-search anchor.
        return len(query.split()) >= 4 or any(char in query for char in "?？")

    async def _semantic(self, params: CodeSearchParams) -> ToolResult:
        if self._mcp_manager is None:
            fallback = await self._fallback_exact(params)
            fallback.meta["semantic_fallback"] = True
            return fallback
        arguments = {
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
            fallback = await self._fallback_exact(params)
            fallback.meta["semantic_error"] = str(exc)
            return fallback

    async def _fallback_exact(self, params: CodeSearchParams) -> ToolResult:
        """Use the host rg binary, then the built-in Grep implementation."""
        local = await self._local_rg(params)
        if local is not None:
            local.meta["degraded"] = True
            local.meta["semantic_fallback"] = True
            return local

        self._grep.work_dir = self.work_dir
        fallback = await self._grep.execute(
            GrepParams(pattern=params.query, path=params.path, include=params.include)
        )
        fallback.meta = {
            "backend": "python_grep",
            "degraded": True,
            "semantic_fallback": True,
        }
        return fallback

    async def _exact(self, params: CodeSearchParams) -> ToolResult:
        if self._mcp_manager is not None:
            arguments = {
                "root": str(Path(self.work_dir or ".").resolve()),
                "command": self._rg_command(params),
            }
            try:
                result = await self._mcp_manager.call_tool(
                    self.mcp_server, "zvec_grep_rg", arguments
                )
                if not getattr(result, "isError", False):
                    return ToolResult(
                        output=_mcp_text(result),
                        meta={"backend": "zvec_grep_rg", "degraded": False},
                    )
            except Exception:
                pass

        fallback = await self._fallback_exact(params)
        fallback.meta.pop("semantic_fallback", None)
        return fallback

    def _rg_command(self, params: CodeSearchParams) -> str:
        parts = ["rg", "-n", "--no-heading", "--color", "never"]
        if params.include:
            parts.extend(["-g", params.include])
        parts.extend([params.query, params.path])
        return " ".join(json.dumps(part) for part in parts)

    async def _local_rg(self, params: CodeSearchParams) -> ToolResult | None:
        executable = shutil.which("rg")
        if not executable:
            return None
        args = [executable, "-n", "--no-heading", "--color", "never"]
        if params.include:
            args.extend(["-g", params.include])
        args.extend([params.query, str(self.resolve_path(params.path))])

        def run() -> tuple[int, str, str]:
            import subprocess

            completed = subprocess.run(
                args,
                cwd=self.work_dir,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
            return completed.returncode, completed.stdout, completed.stderr

        code, stdout, stderr = await asyncio.to_thread(run)
        if code == 0:
            return ToolResult(output=stdout.rstrip(), meta={"backend": "rg", "degraded": True})
        if code == 1:
            return ToolResult(output="No matches found.", meta={"backend": "rg", "degraded": True})
        return None
