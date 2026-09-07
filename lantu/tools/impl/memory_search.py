from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from lantu.memory.recall import ENTRYPOINT_NAME, parse_frontmatter
from lantu.tools.base import Tool, ToolResult


class MemorySearchParams(BaseModel):
    query: str = Field(description="关键词、记忆文件名或主题")
    max_results: int = Field(default=5, ge=1, le=20)


class MemorySearchTool(Tool):
    name = "memory_search"
    description = (
        "Search project and user memory files and return the full content of "
        "the most relevant matches. Use this when a memory summary needs details."
    )
    params_model = MemorySearchParams
    category = "read"
    should_defer = False
    expose_in_standard = True

    def __init__(self, user_mem_dir: str | Path | None, project_mem_dir: str | Path | None) -> None:
        self._roots = [Path(p) for p in (user_mem_dir, project_mem_dir) if p]

    async def execute(self, params: BaseModel) -> ToolResult:
        assert isinstance(params, MemorySearchParams)
        query = params.query.casefold().strip()
        if not query:
            return ToolResult(output="Memory search query cannot be empty.")

        matches: list[tuple[int, Path, str, dict[str, str]]] = []
        terms = [term for term in query.split() if term]
        for root in self._roots:
            if not root.is_dir():
                continue
            try:
                files = root.rglob("*.md")
            except OSError:
                continue
            for path in files:
                if not path.is_file() or path.name == ENTRYPOINT_NAME:
                    continue
                try:
                    content = path.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
                frontmatter = parse_frontmatter(content)
                haystack = " ".join(
                    [path.name, frontmatter.get("name", ""), frontmatter.get("description", ""), content]
                ).casefold()
                score = sum(haystack.count(term) for term in terms)
                if score:
                    matches.append((score, path, content, frontmatter))

        matches.sort(key=lambda item: (-item[0], str(item[1])))
        if not matches:
            return ToolResult(output=f"No memories matched: {params.query}")

        parts: list[str] = []
        for _, path, content, _ in matches[: params.max_results]:
            parts.append(f"## Memory: {path.name}\n{content}")
        return ToolResult(output="\n\n---\n\n".join(parts), meta={"count": min(len(matches), params.max_results)})
