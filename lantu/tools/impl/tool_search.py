from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from lantu.tools.base import Tool, ToolResult

if __import__("typing").TYPE_CHECKING:
    from lantu.tools import ToolRegistry


class ToolSearchParams(BaseModel):
    query: str
    max_results: int = 5


class ToolSearchTool(Tool):
    name = "ToolSearch"
    description = (
        "Search for and load additional tools that are not immediately available. "
        "Use query 'select:<name>[,<name>...]' to load specific tools by name, "
        "or provide keywords to search by relevance."
    )
    params_model = ToolSearchParams
    category = "read"
    should_defer = False  # ToolSearch 自身永远不延迟加载
    expose_in_standard = False


    def __init__(
        self,
        registry: ToolRegistry,
        protocol: str = "anthropic",
    ) -> None:
        self._registry = registry
        self._protocol = protocol


    def get_schema(self) -> dict[str, Any]:
        schema = self.params_model.model_json_schema()
        schema.pop("title", None)
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": schema,
        }


    async def execute(self, params: BaseModel) -> ToolResult:
        assert isinstance(params, ToolSearchParams)
        query = params.query
        max_results = params.max_results

        if query.startswith("select:"):
            names = [n.strip() for n in query[7:].split(",") if n.strip()]
            candidates = self._registry.find_deferred_names(names)
        else:
            candidates = self._registry.search_deferred_names(query, max_results)

        loaded_names = [
            name for name in candidates if self._registry.mark_discovered(name)
        ]
        if not loaded_names:
            deferred_names = self._registry.get_deferred_tool_names()
            return ToolResult(
                output=(
                    f'No matching deferred tools for "{query}". '
                    f'Available: {", ".join(deferred_names)}'
                )
            )

        return ToolResult(
            output=f"Loaded tools: {', '.join(loaded_names)}",
            meta={"loaded_tools": self._registry.discovered_tool_states(loaded_names)},
        )
