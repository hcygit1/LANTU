from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from lantu.tools.base import Tool, ToolResult

if TYPE_CHECKING:
    from lantu.cache import FileCache
    from lantu.tools.file_state_cache import FileStateCache


READ_MAX_LINE_LENGTH = 2_000
READ_TRUNCATED_LINE_MARKER = "(line truncated to 2000 chars; use Grep to search this line)"


def _truncate_line(line: str) -> str:
    if len(line) <= READ_MAX_LINE_LENGTH:
        return line
    return f"{line[:READ_MAX_LINE_LENGTH]}... {READ_TRUNCATED_LINE_MARKER}"


class Params(BaseModel):
    file_path: str = Field(description="Absolute or relative path to the file to read")
    offset: int = Field(default=0, description="Line offset to start reading from (0-based)")
    limit: int = Field(default=2000, description="Maximum number of lines to read")


class ReadFile(Tool):
    name = "ReadFile"
    description = "Read a file and return its contents with line numbers."
    params_model = Params
    category = "read"
    is_concurrency_safe = True


    def __init__(self, file_cache: FileCache | None = None, file_state_cache: FileStateCache | None = None) -> None:
        self._cache = file_cache
        self._state_cache = file_state_cache


    async def execute(self, params: Params) -> ToolResult:
        path = self.resolve_path(params.file_path)
        if not path.exists():
            return ToolResult(output=f"Error: file not found: {params.file_path}", is_error=True)
        if not path.is_file():
            return ToolResult(output=f"Error: not a file: {params.file_path}", is_error=True)

        resolved = str(path.resolve())

        try:
            mtime_ns = path.stat().st_mtime_ns
            text = self._cache.get(resolved, mtime_ns) if self._cache else None
            if text is None:
                text = path.read_text(encoding="utf-8")
                if self._cache:
                    self._cache.put(resolved, text, mtime_ns)
        except Exception as e:
            return ToolResult(output=f"Error reading file: {e}", is_error=True)

        if self._state_cache:
            try:
                mtime_ns = path.stat().st_mtime_ns
                self._state_cache.record(resolved, text, mtime_ns)
            except OSError:
                pass

        lines = text.splitlines()
        selected = lines[params.offset : params.offset + params.limit]
        numbered = [
            f"{i + params.offset + 1}\t{_truncate_line(line)}"
            for i, line in enumerate(selected)
        ]
        return ToolResult(output="\n".join(numbered))
