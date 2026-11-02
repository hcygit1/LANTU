"""Internal lifecycle management for index-backed MCP search servers."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path
from dataclasses import dataclass
from typing import Any

from lantu.mcp.manager import MCPManager

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class IndexStatus:
    state: str
    error: str | None = None


class IndexManager:
    """Keep a ZG index ready without exposing index administration to the model."""

    def __init__(
        self,
        manager: MCPManager,
        server_name: str = "zvec_grep",
        timeout: float = 300.0,
        poll_interval: float = 0.5,
    ) -> None:
        self.manager = manager
        self.server_name = server_name
        self.timeout = timeout
        self.poll_interval = poll_interval
        self._lock = asyncio.Lock()

    async def ensure_ready(self, root: str) -> IndexStatus:
        """Check the index and build it once when the server reports it missing."""
        async with self._lock:
            status = await self.status(root)
            if status.state in {"ready", "fresh"}:
                return IndexStatus("ready")
            if status.state in {"unavailable", "unknown"}:
                return status

            try:
                if status.state == "building":
                    return await self._wait_ready(root)
                try:
                    await self._call("zvec_grep_index", root, {"wait": True})
                except Exception as exc:
                    # 请求超时后服务端可能仍在构建，查询状态而非再次提交。
                    log.warning("ZG index build request failed: %s", exc)
                return await self._wait_ready(root)
            except Exception as exc:
                log.warning("ZG index initialization failed: %s", exc)
                return IndexStatus("failed", str(exc))

    async def status(self, root: str) -> IndexStatus:
        try:
            result = await self._call("zvec_grep_index_status", root)
        except Exception as exc:
            # Older or restricted ZG toolsets may not expose index management.
            return IndexStatus("unavailable", str(exc))
        data = _structured(result)
        if data is not None:
            runtime = data.get("runtime")
            job_state = str(
                runtime.get("job_state", "") if isinstance(runtime, dict) else ""
            ).casefold()
            # 首次构建期间 indexed 仍可能为 false，不能再次提交构建。
            if job_state in {"running", "pending", "queued"}:
                return IndexStatus("building")
            if data.get("indexed") is False:
                return IndexStatus("stale")
            if data.get("indexed") is True:
                return IndexStatus("ready")
        log.warning("Unrecognised ZG index status for %s", root)
        return IndexStatus("unknown", "unrecognised ZG index status")

    async def monitor(self, root: str, interval: float = 60.0) -> None:
        """Periodically reconcile stale indexes; cancellation stops the monitor."""
        while True:
            await asyncio.sleep(interval)
            status = await self.status(root)
            if status.state in {"missing", "stale", "failed"}:
                await self.ensure_ready(root)

    async def _wait_ready(self, root: str) -> IndexStatus:
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            status = await self.status(root)
            if status.state == "ready":
                return status
            if status.state in {"unavailable", "unknown"}:
                return status
            await asyncio.sleep(self.poll_interval)
        return IndexStatus("failed", "timed out waiting for ZG index")

    async def _call(
        self, tool_name: str, root: str, extra: dict | None = None
    ) -> Any:
        """Use the advertised schema; never fall back to the server's cwd."""
        root_path = Path(root).expanduser()
        if not root_path.is_absolute():
            raise ValueError("ZG index root must be an absolute workspace path")
        client = await self.manager.get_client(self.server_name)
        if client is None:
            raise RuntimeError(f"MCP server '{self.server_name}' is not configured")
        tool = next((item for item in await client.list_tools()
                     if item.name == tool_name), None)
        if tool is None:
            raise RuntimeError(f"MCP tool unavailable: {tool_name}")
        properties = tool.inputSchema.get("properties", {})
        root_key = next((key for key in ("root", "path", "repo")
                         if key in properties), None)
        if root_key is None:
            raise RuntimeError(f"{tool_name} does not advertise a workspace parameter")
        payload = {root_key: str(root_path.resolve())}
        payload.update({key: value for key, value in (extra or {}).items()
                        if key in properties})
        result = await self.manager.call_tool(self.server_name, tool_name, payload)
        if getattr(result, "isError", False):
            raise RuntimeError(_result_text(result))
        return result


def _structured(result: Any) -> dict | None:
    data = getattr(result, "structuredContent", None)
    if isinstance(data, dict):
        return data
    try:
        parsed = json.loads(_result_text(result))
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _result_text(result: Any) -> str:
    structured = getattr(result, "structuredContent", None)
    if structured:
        try:
            return json.dumps(structured, ensure_ascii=False)
        except TypeError:
            pass
    content = getattr(result, "content", None) or []
    parts = [getattr(item, "text", "") for item in content]
    return "\n".join(part for part in parts if part) or str(result)
