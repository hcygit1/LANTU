from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
from typing import TYPE_CHECKING, Any

from lantu.tools.base import Tool

if TYPE_CHECKING:
    from lantu.cache import FileCache


@dataclass(frozen=True)
class SchemaEpoch:
    """Stable identity of the tool-schema view sent to one provider."""

    epoch_id: str
    fingerprint: str
    protocol: str
    loading_mode: str
    visible_tools: tuple[str, ...]
    deferred_tools: tuple[str, ...]

    def to_payload(self) -> dict[str, Any]:
        return {
            "epoch_id": self.epoch_id,
            "fingerprint": self.fingerprint,
            "protocol": self.protocol,
            "loading_mode": self.loading_mode,
            "visible_tools": list(self.visible_tools),
            "deferred_tools": list(self.deferred_tools),
        }


class ToolRegistry:
    def __init__(self, loading_mode: str = "progressive") -> None:
        if loading_mode not in {"standard", "progressive"}:
            raise ValueError(
                "loading_mode must be 'standard' or 'progressive'"
            )
        self._tools: dict[str, Tool] = {}
        self.loading_mode = loading_mode
        self._disabled: set[str] = set()
        self._discovered: set[str] = set()
        self._discovery_order: list[str] = []
        self._schema_snapshots: dict[str, dict[str, Any]] = {}

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool
        self._schema_snapshots.pop(tool.name, None)

    @property
    def is_progressive(self) -> bool:
        return self.loading_mode == "progressive"

    def set_loading_mode(self, loading_mode: str) -> None:
        """Change the session-local tool loading strategy before a turn starts."""
        if loading_mode not in {"standard", "progressive"}:
            raise ValueError(
                "loading_mode must be 'standard' or 'progressive'"
            )
        if loading_mode == self.loading_mode:
            return
        self.reset_discovered()
        self.loading_mode = loading_mode

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)


    def is_enabled(self, name: str) -> bool:
        return name in self._tools and name not in self._disabled

    def enable(self, name: str) -> None:
        self._disabled.discard(name)


    def disable(self, name: str) -> None:
        if name in self._tools:
            self._disabled.add(name)

    def enable_all(self) -> None:
        self._disabled.clear()


    def mark_discovered(self, name: str) -> bool:
        """Make one deferred tool visible to the model exactly once."""
        if not self.is_progressive:
            return False
        if name in self._discovered:
            return False
        tool = self._tools.get(name)
        if tool is None or not getattr(tool, "should_defer", False):
            return False
        if name in self._disabled:
            return False

        self._schema_snapshots[name] = deepcopy(tool.get_schema())
        self._discovered.add(name)
        self._discovery_order.append(name)
        return True

    def is_discovered(self, name: str) -> bool:
        return name in self._discovered

    def discovered_tool_states(
        self, names: list[str] | None = None
    ) -> list[dict[str, str]]:
        """Return loaded tool names and canonical Schema hashes in load order."""
        selected = set(names) if names is not None else None
        states: list[dict[str, str]] = []
        for name in self._discovery_order:
            if selected is not None and name not in selected:
                continue
            schema = self._schema_snapshots.get(name)
            if schema is None:
                continue
            states.append({"name": name, "schema_hash": _schema_hash(schema)})
        return states

    def forget_discovered(self, names: list[str]) -> None:
        """Roll back a set of newly activated deferred tools."""
        names_set = set(names)
        self._discovered.difference_update(names_set)
        self._discovery_order = [
            name for name in self._discovery_order if name not in names_set
        ]
        for name in names_set:
            self._schema_snapshots.pop(name, None)

    def reset_discovered(self) -> None:
        """Clear the session-local deferred-tool visibility state."""
        self.forget_discovered(list(self._discovery_order))

    def restore_discovered(self, states: list[dict[str, str]]) -> None:
        """Restore loaded tools from Journal state and verify their Schemas."""
        if not self.is_progressive:
            return
        restored: list[str] = []
        try:
            for state in states:
                name = state["name"]
                if not self.mark_discovered(name):
                    raise ValueError(f"cannot restore deferred tool '{name}'")
                restored.append(name)
                actual = self.discovered_tool_states([name])[0]["schema_hash"]
                if actual != state["schema_hash"]:
                    raise ValueError(
                        f"schema changed for deferred tool '{name}' "
                        f"(expected {state['schema_hash']}, got {actual})"
                    )
        except Exception:
            self.forget_discovered(restored)
            raise

    def is_model_visible(self, name: str) -> bool:
        """Return whether the model may call this tool in the current view."""
        tool = self._tools.get(name)
        if tool is None or name in self._disabled:
            return False
        if not self.is_progressive:
            return True
        return not getattr(tool, "should_defer", False) or name in self._discovered


    def get_deferred_tool_names(self) -> list[str]:
        if not self.is_progressive:
            return []
        return [
            name
            for name, tool in self._tools.items()
            if getattr(tool, "should_defer", False)
            and name not in self._discovered
            and name not in self._disabled
        ]

    def search_deferred_names(self, query: str, max_results: int) -> list[str]:
        if not self.is_progressive:
            return []
        query_lower = query.lower()
        scored: list[tuple[int, str, Tool]] = []
        for name, tool in self._tools.items():
            if not getattr(tool, "should_defer", False):
                continue
            if name in self._disabled or name in self._discovered:
                continue
            score = 0
            name_lower = name.lower()
            desc_lower = (tool.description or "").lower()
            if query_lower in name_lower:
                score += 10
            if query_lower in desc_lower:
                score += 5
            for word in query_lower.split():
                if word in name_lower:
                    score += 3
                if word in desc_lower:
                    score += 1
            if score > 0:
                scored.append((score, name, tool))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [name for _, name, _tool in scored[:max_results]]

    def find_deferred_names(self, names: list[str]) -> list[str]:
        if not self.is_progressive:
            return []
        results: list[str] = []
        for name in names:
            tool = self._tools.get(name)
            if tool is None:
                continue
            if (
                not getattr(tool, "should_defer", False)
                or name in self._disabled
                or name in self._discovered
            ):
                continue
            results.append(name)
        return results

    def list_tools(self) -> list[Tool]:
        return list(self._tools.values())


    def get_all_schemas(self, protocol: str = "anthropic") -> list[dict[str, Any]]:
        if self.is_progressive:
            # Keep the stable registered tools first, then append deferred tools in
            # the order in which the model discovered them.
            names = [
                name
                for name, tool in self._tools.items()
                if name not in self._disabled
                and not getattr(tool, "should_defer", False)
            ]
            names.extend(
                name
                for name in self._discovery_order
                if name in self._tools and name not in self._disabled
            )
        else:
            # Standard mode exposes one fixed tool list for the whole session.
            names = [
                name
                for name, tool in self._tools.items()
                if name not in self._disabled
                and getattr(tool, "expose_in_standard", True)
            ]

        schemas: list[dict[str, Any]] = []
        for name in names:
            tool = self._tools[name]
            base = self._schema_snapshots.get(name)
            if base is None:
                base = deepcopy(tool.get_schema())
                self._schema_snapshots[name] = base
            if protocol in ("openai", "openai-compat"):
                schemas.append({
                    "type": "function",
                    "name": base["name"],
                    "description": base["description"],
                    "parameters": deepcopy(base["input_schema"]),
                })
            else:
                schemas.append(deepcopy(base))
        return schemas

    def schema_size(self, protocol: str = "anthropic") -> int:
        """Return the serialized Schema size used for the large-tool notice."""
        return len(
            json.dumps(
                self.get_all_schemas(protocol),
                ensure_ascii=False,
                separators=(",", ":"),
            )
        )

    def schema_epoch(self, protocol: str = "anthropic") -> SchemaEpoch:
        """Return the stable identity of the current model-visible Schema set."""
        schemas = self.get_all_schemas(protocol)
        schema_states = [
            {
                "name": str(schema.get("name", "")),
                "schema_hash": _schema_hash(schema),
            }
            for schema in schemas
        ]
        deferred_tools = tuple(self.get_deferred_tool_names())
        canonical = {
            "protocol": protocol,
            "loading_mode": self.loading_mode,
            "schemas": schema_states,
            "deferred_tools": list(deferred_tools),
        }
        fingerprint = _schema_hash(canonical)
        return SchemaEpoch(
            epoch_id=f"schema-{fingerprint[:16]}",
            fingerprint=fingerprint,
            protocol=protocol,
            loading_mode=self.loading_mode,
            visible_tools=tuple(state["name"] for state in schema_states),
            deferred_tools=deferred_tools,
        )


def create_default_registry(
    file_cache: FileCache | None = None,
    file_history: Any = None,
    work_dir: str | None = None,
    loading_mode: str = "progressive",
) -> ToolRegistry:
    from lantu.tools.bash import Bash
    from lantu.tools.edit_file import EditFile
    from lantu.tools.file_state_cache import FileStateCache
    from lantu.tools.glob import Glob
    from lantu.tools.grep import Grep
    from lantu.tools.read_file import ReadFile
    from lantu.tools.write_file import WriteFile

    file_state_cache = FileStateCache()

    registry = ToolRegistry(loading_mode=loading_mode)
    registry.register(ReadFile(file_cache=file_cache, file_state_cache=file_state_cache))
    registry.register(WriteFile(file_cache=file_cache, file_history=file_history, file_state_cache=file_state_cache))
    registry.register(EditFile(file_cache=file_cache, file_history=file_history, file_state_cache=file_state_cache))
    registry.register(Bash())
    registry.register(Glob())
    registry.register(Grep())
    for tool in registry.list_tools():
        tool.work_dir = work_dir
    return registry


def _schema_hash(schema: dict[str, Any]) -> str:
    canonical = json.dumps(
        schema,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
