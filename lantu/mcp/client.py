from __future__ import annotations

import logging
import os
import signal
from contextlib import AsyncExitStack
from typing import Any

import httpx
from mcp import ClientSession, types
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.client.streamable_http import streamable_http_client

from lantu.config import MCPServerConfig, build_child_env, resolve_env_vars

logger = logging.getLogger(__name__)


class MCPClient:
    def __init__(self, config: MCPServerConfig) -> None:
        self.config = config
        self.name = config.name
        self._session: ClientSession | None = None
        self._stack: AsyncExitStack | None = None
        self._alive = False
        # 存储 MCP 服务器的 InitializeResult，用于提取 instructions 等元信息
        self._init_result: types.InitializeResult | None = None


    @property
    def is_alive(self) -> bool:
        return self._alive

    @property
    def instructions(self) -> str:
        """返回 MCP 服务器的 instructions（来自 InitializeResult）。"""
        if self._init_result is not None and self._init_result.instructions:
            return self._init_result.instructions
        return ""


    async def connect(self) -> None:
        if self._alive:
            return

        self._stack = AsyncExitStack()
        await self._stack.__aenter__()

        try:
            if self.config.is_stdio:
                read, write = await self._connect_stdio()
            else:
                read, write = await self._connect_http()

            session = await self._stack.enter_async_context(
                ClientSession(read, write)
            )
            # 保存 InitializeResult，后续可从中提取 instructions
            self._init_result = await session.initialize()
            self._session = session
            self._alive = True
            logger.info("MCP server '%s' connected", self.name)
        except BaseException:
            try:
                await self._cleanup_stack()
            except BaseException:
                logger.debug(
                    "Error cleaning failed connection for '%s'",
                    self.name,
                    exc_info=True,
                )
            raise


    async def _connect_stdio(self) -> tuple[Any, Any]:
        assert self._stack is not None
        assert self.config.command is not None

        params = StdioServerParameters(
            command=self.config.command,
            args=self.config.args,
            env=build_child_env(self.config.env),
        )
        devnull = open(os.devnull, "w")
        self._stack.callback(devnull.close)
        read, write = await self._stack.enter_async_context(
            stdio_client(params, errlog=devnull)
        )
        return read, write

    async def _connect_http(self) -> tuple[Any, Any]:
        assert self._stack is not None
        assert self.config.url is not None

        resolved_headers = {
            k: resolve_env_vars(v) for k, v in self.config.headers.items()
        }
        http_client = httpx.AsyncClient(
            headers=resolved_headers,
            follow_redirects=True,
        )
        await self._stack.enter_async_context(http_client)

        result = await self._stack.enter_async_context(
            streamable_http_client(self.config.url, http_client=http_client)
        )
        read, write = result[0], result[1]
        return read, write


    async def list_tools(self) -> list[types.Tool]:
        assert self._session is not None
        result = await self._session.list_tools()
        return list(result.tools)


    async def call_tool(
        self, name: str, arguments: dict[str, Any]
    ) -> types.CallToolResult:
        assert self._session is not None
        return await self._session.call_tool(name, arguments)

    async def close(self) -> None:
        self._alive = False
        self._session = None
        await self._cleanup_stack()

    def _kill_descendant_processes(self) -> None:
        """关栈前 SIGKILL 全部后代进程（MCP 子进程树）。SDK 的 stdio 退出会等
        子进程死亡；zg 永不自行退出，否则 shutdown 永久挂起。"""
        try:
            pid = os.getpid()
            children: dict[int, list[int]] = {}
            for entry in os.listdir("/proc"):
                if not entry.isdigit():
                    continue
                try:
                    with open(f"/proc/{entry}/stat") as f:
                        data = f.read()
                    l = data.rindex(")")
                    fields = data[l + 2:].split()
                    ppid = int(fields[1])
                    children.setdefault(ppid, []).append(int(entry))
                except (OSError, IndexError, ValueError):
                    continue
            to_kill: list[int] = []
            frontier = [pid]
            while frontier:
                nxt = []
                for p in frontier:
                    for c in children.get(p, []):
                        to_kill.append(c)
                        nxt.append(c)
                frontier = nxt
            for p in to_kill:
                try:
                    os.kill(p, signal.SIGKILL)
                except OSError:
                    pass
            if to_kill:
                logger.info(
                    "Killed %d descendant processes before MCP stack close",
                    len(to_kill),
                )
        except OSError:
            # macOS 无 /proc，自动降级为 no-op（仅容器内生效）
            logger.debug("Cannot scan /proc for descendant processes", exc_info=True)

    async def _cleanup_stack(self) -> None:
        if self._stack is not None:
            stack = self._stack
            self._stack = None
            self._kill_descendant_processes()
            try:
                # 必须在原任务内直接 await：anyio cancel scope 的宿主任务是
                # 进入它的外层任务，任何挪到别的 task 的做法（wait_for/
                # asyncio.wait）都会让 CancelledError 击中外层任务导致崩溃。
                # 子进程已被 SIGKILL，管道 EOF 后这里会立即正常返回。
                await stack.__aexit__(None, None, None)
            except RuntimeError as e:
                if "cancel scope" in str(e):
                    logger.debug("Cancel scope cleanup (expected during shutdown): %s", e)
                else:
                    raise
            except Exception:
                logger.debug("Error closing stack for '%s'", self.name, exc_info=True)
