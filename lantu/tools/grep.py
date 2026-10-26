from __future__ import annotations

import asyncio
import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from lantu.tools.base import SKIP_DIRS, Tool, ToolResult


GREP_CONTEXT_CHARS = 200

# Outputs that mean "the exact pass found nothing" and may justify a
# semantic retry over the project index.
NO_MATCH_OUTPUTS = {"", "No matches found.", "(no output)"}


def _format_match(line: str, match: re.Match[str]) -> str:
    """Return a bounded window around the first match in a long line."""
    if len(line) <= 2 * GREP_CONTEXT_CHARS + 1:
        return line
    start = max(0, match.start() - GREP_CONTEXT_CHARS)
    end = min(len(line), match.end() + GREP_CONTEXT_CHARS)
    prefix = "..." if start > 0 else ""
    suffix = "..." if end < len(line) else ""
    return f"{prefix}{line[start:end]}{suffix}"


def _mcp_text(result: Any) -> str:
    content = getattr(result, "content", None) or []
    parts = [getattr(item, "text", "") for item in content]
    return "\n".join(part for part in parts if part) or "(no output)"


class Params(BaseModel):
    pattern: str = Field(description="Regex pattern to search for")
    path: str = Field(default=".", description="Base directory to search from")
    include: str = Field(default="", description="Glob filter for filenames (e.g. '*.py')")


class Grep(Tool):
    """Regex content search backed by the managed rg channel.

    Routing: zg's managed rg (zvec_grep_rg) → host rg binary → built-in
    Python implementation. When the exact pass finds nothing and the zvec
    MCP server is reachable, the same pattern is retried once against the
    project's semantic index and the closest matches are appended.
    """

    name = "Grep"
    description = (
        "Search file contents using a regex pattern, returning file:line:content matches."
    )
    params_model = Params
    category = "read"
    is_concurrency_safe = True

    def __init__(self, work_dir: str | None = None) -> None:
        self.work_dir = work_dir
        self._mcp_manager: Any = None
        self.mcp_server = "zvec_grep"

    def set_mcp_manager(self, manager: Any) -> None:
        self._mcp_manager = manager

    async def execute(self, params: Params) -> ToolResult:
        result = await self._exact(params)
        if (
            not result.is_error
            and self._mcp_manager is not None
            and result.output.strip() in NO_MATCH_OUTPUTS
        ):
            retry = await self._semantic_retry(params)
            if retry is not None:
                return retry
        return result

    async def _exact(self, params: Params) -> ToolResult:
        if self._mcp_manager is not None:
            arguments = {
                "root": str(Path(self.work_dir or ".").resolve()),
                "command": self._rg_command(params),
            }
            try:
                mcp_result = await self._mcp_manager.call_tool(
                    self.mcp_server, "zvec_grep_rg", arguments
                )
                if not getattr(mcp_result, "isError", False):
                    return ToolResult(
                        output=_mcp_text(mcp_result),
                        meta={"backend": "zvec_grep_rg", "degraded": False},
                    )
            except Exception:
                pass

        local = await self._local_rg(params)
        if local is not None:
            return local
        return self._python_grep(params)

    async def _semantic_retry(self, params: Params) -> ToolResult | None:
        """One index-backed retry when the literal pattern matched nothing."""
        arguments: dict[str, Any] = {
            "root": str(Path(self.work_dir or ".").resolve()),
            "query": params.pattern,
            "limit": 10,
        }
        if params.include:
            arguments["globs"] = [params.include]
        try:
            mcp_result = await self._mcp_manager.call_tool(
                self.mcp_server, "zvec_grep_search", arguments
            )
        except Exception:
            return None
        if getattr(mcp_result, "isError", False):
            return None
        text = _mcp_text(mcp_result)
        if text.strip() in NO_MATCH_OUTPUTS:
            return None
        return ToolResult(
            output=(
                "No exact matches. Closest matches from the project's semantic index:\n"
                + text
            ),
            meta={
                "backend": "zvec_grep_search",
                "degraded": False,
                "semantic_retry": True,
            },
        )

    def _rg_command(self, params: Params) -> str:
        parts = ["rg", "-n"]
        if params.include:
            parts.extend(["-g", params.include])
        parts.extend([params.pattern, params.path])
        return " ".join(json.dumps(part) for part in parts)

    async def _local_rg(self, params: Params) -> ToolResult | None:
        executable = shutil.which("rg")
        if not executable:
            return None
        args = [executable, "-n", "--no-heading", "--color", "never"]
        if params.include:
            args.extend(["-g", params.include])
        args.extend([params.pattern, str(self.resolve_path(params.path))])

        def run() -> tuple[int, str, str]:
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

        code, stdout, _stderr = await asyncio.to_thread(run)
        if code == 0:
            return ToolResult(output=stdout.rstrip(), meta={"backend": "rg", "degraded": True})
        if code == 1:
            return ToolResult(output="No matches found.", meta={"backend": "rg", "degraded": True})
        return None

    def _python_grep(self, params: Params) -> ToolResult:
        base = self.resolve_path(params.path)
        if not base.exists():
            return ToolResult(output=f"Error: path not found: {params.path}", is_error=True)

        try:
            regex = re.compile(params.pattern)
        except re.error as e:
            return ToolResult(output=f"Error: invalid regex: {e}", is_error=True)

        if base.is_file():
            files = [base]
            output_root = base.parent
        else:
            glob_pattern = params.include if params.include else "**/*"
            if not glob_pattern.startswith("**/"):
                glob_pattern = "**/" + glob_pattern
            files = sorted(base.glob(glob_pattern))
            output_root = base

        results: list[str] = []
        for file_path in files:
            if not file_path.is_file():
                continue
            if any(part in SKIP_DIRS for part in file_path.parts):
                continue
            try:
                text = file_path.read_text(encoding="utf-8", errors="ignore")
            except (OSError, UnicodeDecodeError):
                continue
            for line_num, line in enumerate(text.splitlines(), 1):
                match = regex.search(line)
                if match:
                    rel = file_path.relative_to(output_root).as_posix()
                    results.append(
                        f"{rel}:{line_num}:{_format_match(line, match)}"
                    )

        if not results:
            return ToolResult(
                output="No matches found.",
                meta={"backend": "python_grep", "degraded": True},
            )
        return ToolResult(
            output="\n".join(results),
            meta={"backend": "python_grep", "degraded": True},
        )
