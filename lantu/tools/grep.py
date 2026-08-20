from __future__ import annotations

import re
from pathlib import Path

from pydantic import BaseModel, Field

from lantu.tools.base import SKIP_DIRS, Tool, ToolResult


GREP_CONTEXT_CHARS = 200


def _format_match(line: str, match: re.Match[str]) -> str:
    """Return a bounded window around the first match in a long line."""
    if len(line) <= 2 * GREP_CONTEXT_CHARS + 1:
        return line
    start = max(0, match.start() - GREP_CONTEXT_CHARS)
    end = min(len(line), match.end() + GREP_CONTEXT_CHARS)
    prefix = "..." if start > 0 else ""
    suffix = "..." if end < len(line) else ""
    return f"{prefix}{line[start:end]}{suffix}"


class Params(BaseModel):
    pattern: str = Field(description="Regex pattern to search for")
    path: str = Field(default=".", description="Base directory to search from")
    include: str = Field(default="", description="Glob filter for filenames (e.g. '*.py')")


class Grep(Tool):
    name = "Grep"
    description = "Search file contents using a regex pattern, returning file:line:content matches."
    params_model = Params
    category = "read"
    is_concurrency_safe = True


    async def execute(self, params: Params) -> ToolResult:
        base = self.resolve_path(params.path)
        if not base.exists():
            return ToolResult(output=f"Error: path not found: {params.path}", is_error=True)

        try:
            regex = re.compile(params.pattern)
        except re.error as e:
            return ToolResult(output=f"Error: invalid regex: {e}", is_error=True)

        glob_pattern = params.include if params.include else "**/*"
        if not glob_pattern.startswith("**/"):
            glob_pattern = "**/" + glob_pattern

        results: list[str] = []
        for file_path in sorted(base.glob(glob_pattern)):
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
                    rel = file_path.relative_to(base).as_posix()
                    results.append(
                        f"{rel}:{line_num}:{_format_match(line, match)}"
                    )

        if not results:
            return ToolResult(output="No matches found.")
        return ToolResult(output="\n".join(results))
