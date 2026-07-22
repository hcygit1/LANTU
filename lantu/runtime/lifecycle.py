from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable
from pathlib import Path
from typing import Any

from lantu.client import create_client
from lantu.commands.handlers.skill_register import register_skill_commands
from lantu.conversation import ConversationManager, Message
from lantu.hooks import HookContext
from lantu.mcp import ConnectResult, MCPManager
from lantu.memory import find_relevant_memories, render_reminder
from lantu.permissions import PathSandbox

from lantu.runtime.models import InteractiveRuntime

log = logging.getLogger(__name__)

BACKGROUND_TASK_CANCEL_TIMEOUT = 0.25
RUNTIME_CLOSE_TIMEOUT = 3.0
MEMORY_PREFETCH_TIMEOUT = 8.0


def switch_runtime_work_dir(runtime: InteractiveRuntime, path: str) -> None:
    runtime.agent.work_dir = path
    runtime.permission_checker.sandbox = PathSandbox(path)
    for tool in runtime.registry.list_tools():
        tool.work_dir = path

    bash_tool = runtime.registry.get("Bash")
    if (
        runtime.config.sandbox.enabled
        and bash_tool is not None
        and getattr(bash_tool, "sandbox", None) is not None
    ):
        from lantu.sandbox import SandboxConfig

        bash_tool.sandbox_config = SandboxConfig(
            allow_write=[path, "/tmp"],
            deny_write=[
                str(Path(path) / ".lantu" / "config.yaml"),
                str(Path(path) / ".lantu" / "permissions.local.yaml"),
            ],
            network_enabled=runtime.config.sandbox.network_enabled,
        )


def _render_skill_catalog(catalog: list[tuple[str, str]]) -> str:
    if not catalog:
        return ""
    lines = ["You can use the following Skills:", ""]
    lines.extend(f"- {name}: {description}" for name, description in catalog)
    lines.extend(
        [
            "",
            "If the user's request matches a Skill, call LoadSkill to activate it.",
        ]
    )
    return "\n".join(lines)


def _render_agent_catalog(
    catalog: list[tuple[str, str]], enable_fork: bool
) -> str:
    if not catalog:
        return ""
    lines = [
        "## Available Sub-Agent Types",
        "",
        "Use the Agent tool with subagent_type parameter to delegate tasks:",
        "",
    ]
    lines.extend(f"- **{name}**: {description}" for name, description in catalog)
    if enable_fork:
        lines.extend(
            [
                "",
                "Leave subagent_type empty to fork the current conversation "
                "(inherits full dialog history).",
            ]
        )
    lines.extend(
        [
            "",
            "IMPORTANT: Sub-agents run in the background. "
            "After calling the Agent tool, you will get a task ID immediately. "
            "Do NOT wait, sleep, or poll for the result. "
            "Simply report the task ID to the user and end your turn. "
            "The system will automatically notify when the task completes.",
        ]
    )
    return "\n".join(lines)


def refresh_runtime_skills(runtime: InteractiveRuntime) -> None:
    if not runtime.skill_loader.needs_reload():
        return
    runtime.skill_loader.reload()
    register_skill_commands(
        runtime.command_registry, runtime.skill_loader, runtime.skill_executor
    )
    runtime.agent.set_skill_catalog(
        _render_skill_catalog(runtime.skill_loader.get_catalog())
    )
    agent_catalog = runtime.agent_loader.list_agents()
    runtime.agent.set_agent_catalog(
        _render_agent_catalog(agent_catalog, runtime.config.enable_fork),
        catalog_list=agent_catalog,
    )


async def prefetch_runtime_memories(
    runtime: InteractiveRuntime, query: str
) -> str:
    provider = runtime.provider

    async def selector(system_prompt: str, user_message: str) -> str:
        from lantu.tools.base import StreamEnd, TextDelta

        side_client = create_client(provider)
        try:
            conversation = ConversationManager()
            conversation.history = [Message(role="user", content=user_message)]
            collected = ""
            async for event in side_client.stream(conversation, system=system_prompt):
                if isinstance(event, TextDelta):
                    collected += event.text
                elif isinstance(event, StreamEnd):
                    continue
            return collected
        finally:
            try:
                await side_client.aclose()
            except BaseException:
                log.warning("Failed to close memory side client", exc_info=True)

    try:
        results = await asyncio.wait_for(
            find_relevant_memories(
                query=query,
                user_mem_dir=runtime.memory_manager.user_mem_dir,
                project_mem_dir=runtime.memory_manager.project_mem_dir,
                recent_tools=None,
                already_surfaced=None,
                selector=selector,
            ),
            timeout=MEMORY_PREFETCH_TIMEOUT,
        )
        return render_reminder(results)
    except asyncio.CancelledError:
        raise
    except (asyncio.TimeoutError, Exception):
        return ""


def _consume_task_result(task: asyncio.Task[Any]) -> None:
    try:
        exception = task.exception()
    except asyncio.CancelledError:
        return
    except BaseException:
        log.exception("Background runtime task failed")
        return
    if exception is not None:
        log.error(
            "Background runtime task failed",
            exc_info=(type(exception), exception, exception.__traceback__),
        )


def _cancel_task_once(task: asyncio.Task[Any]) -> None:
    if not task.done() and task.cancelling() == 0:
        task.cancel()


def track_background_task(
    runtime: InteractiveRuntime, awaitable: Awaitable[Any]
) -> asyncio.Task[Any]:
    task = asyncio.create_task(awaitable)
    runtime.background_tasks.add(task)
    task.add_done_callback(runtime.background_tasks.discard)
    task.add_done_callback(_consume_task_result)
    return task


def _build_mcp_instructions(
    runtime: InteractiveRuntime, connect_result: ConnectResult
) -> str:
    parts: list[str] = []
    for server in connect_result.servers:
        body = server.instructions
        if not body:
            tool_names = [
                tool.name
                for tool in runtime.registry.list_tools()
                if tool.name.startswith(f"mcp__{server.name}__")
            ]
            if tool_names:
                body = "Available tools: " + ", ".join(tool_names)
        parts.append(f"## {server.name}\n{body}".rstrip())
    if not parts:
        return ""
    return (
        "# MCP Server Instructions\n\n"
        "The following MCP servers have provided instructions "
        "for how to use their tools and resources:\n\n"
        + "\n\n".join(parts)
    )


async def initialize_runtime_mcp(runtime: InteractiveRuntime) -> None:
    manager = MCPManager()
    published = False
    try:
        manager.load_configs(runtime.config.mcp_servers)
        result = await manager.register_all_tools(runtime.registry)
        if runtime._closed:
            return
        runtime.mcp_manager = manager
        published = True
        runtime.mcp_instructions = _build_mcp_instructions(runtime, result)
        runtime.startup_messages.extend(f"MCP warning: {error}" for error in result.errors)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        message = f"MCP warning: {exc}"
        log.warning(message)
        if not runtime._closed:
            runtime.startup_messages.append(message)
    finally:
        if not published:
            try:
                await manager.shutdown()
            except BaseException:
                log.exception("Failed to shut down unpublished MCP manager")


async def _run_cleanup_tasks(
    awaitables: list[Awaitable[Any]], timeout: float
) -> BaseException | None:
    tasks = [asyncio.create_task(awaitable) for awaitable in awaitables]
    return await _wait_for_tasks_bounded(tasks, timeout)


async def _wait_for_tasks_bounded(
    tasks: list[asyncio.Task[Any]] | set[asyncio.Task[Any]], timeout: float
) -> BaseException | None:
    pending = set(tasks)
    if not pending:
        return None
    interrupted: BaseException | None = None
    try:
        done, pending = await asyncio.wait(pending, timeout=timeout)
    except BaseException as exc:
        interrupted = exc
        done = {task for task in pending if task.done()}
        pending = {task for task in pending if not task.done()}

    for task in done:
        _consume_task_result(task)
    for task in pending:
        _cancel_task_once(task)
        task.add_done_callback(_consume_task_result)
    return interrupted


def _remaining_time(deadline: float) -> float:
    return max(0.0, deadline - time.monotonic())


async def close_interactive_runtime(runtime: InteractiveRuntime) -> None:
    if runtime._closed:
        return
    runtime._closed = True
    deadline = time.monotonic() + RUNTIME_CLOSE_TIMEOUT
    cancellation: asyncio.CancelledError | None = None
    current = asyncio.current_task()

    managed_tasks = set(runtime.background_tasks)
    managed_tasks.update(runtime.task_manager.active_tasks())
    if runtime.mcp_task is not None:
        managed_tasks.add(runtime.mcp_task)
    managed_tasks.discard(current)
    for task in managed_tasks:
        _cancel_task_once(task)
        task.add_done_callback(runtime.background_tasks.discard)
    interrupted = await _wait_for_tasks_bounded(
        managed_tasks,
        timeout=min(BACKGROUND_TASK_CANCEL_TIMEOUT, _remaining_time(deadline)),
    )
    if isinstance(interrupted, asyncio.CancelledError):
        cancellation = interrupted

    private_clients = [
        client
        for client in runtime.task_manager.agent_clients()
        if client is not runtime.client
    ]
    cleanup: list[Awaitable[Any]] = [
        runtime.agent._extract_memories(runtime.conversation),
        runtime.client.aclose(),
        *(client.aclose() for client in private_clients),
    ]
    if runtime.hook_engine is not None:
        cleanup.append(
            runtime.hook_engine.run_hooks(
                "shutdown", HookContext(event_name="shutdown")
            )
        )
    manager = runtime.mcp_manager
    runtime.mcp_manager = None
    if manager is not None:
        cleanup.append(manager.shutdown())
    interrupted = await _run_cleanup_tasks(
        cleanup, timeout=_remaining_time(deadline)
    )
    if isinstance(interrupted, asyncio.CancelledError):
        cancellation = interrupted

    try:
        for team_name in runtime.team_manager.list_teams():
            try:
                team = runtime.team_manager.get_team(team_name)
                if team is not None:
                    for member in team.members:
                        team.set_member_active(member.name, False)
                await runtime.team_manager.delete_team_bounded(
                    team_name, deadline=deadline
                )
            except asyncio.CancelledError as exc:
                cancellation = exc
            except BaseException:
                log.exception("Failed to delete team %s", team_name)
    finally:
        try:
            runtime.session.close()
        except BaseException:
            log.exception("Failed to close interactive session")
    if cancellation is not None:
        raise cancellation
