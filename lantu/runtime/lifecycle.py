from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable
from typing import Any

from lantu.client import create_client
from lantu.commands.handlers.skill_register import register_skill_commands
from lantu.conversation import ConversationManager, Message
from lantu.hooks import HookContext
from lantu.mcp import ConnectResult, MCPManager
from lantu.memory import find_relevant_memories, render_reminder

from lantu.runtime.models import InteractiveRuntime

log = logging.getLogger(__name__)


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
        conversation = ConversationManager()
        conversation.history = [Message(role="user", content=user_message)]
        collected = ""
        async for event in side_client.stream(conversation, system=system_prompt):
            if isinstance(event, TextDelta):
                collected += event.text
            elif isinstance(event, StreamEnd):
                continue
        return collected

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
            timeout=8.0,
        )
        return render_reminder(results)
    except asyncio.CancelledError:
        raise
    except (asyncio.TimeoutError, Exception):
        return ""


def _consume_task_result(task: asyncio.Task[Any]) -> None:
    try:
        task.exception()
    except asyncio.CancelledError:
        return
    except BaseException:
        log.exception("Background runtime task failed")


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
) -> None:
    tasks = [asyncio.create_task(awaitable) for awaitable in awaitables]
    if not tasks:
        return
    try:
        done, pending = await asyncio.wait(tasks, timeout=timeout)
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        for task in done:
            try:
                task.result()
            except BaseException:
                log.exception("Runtime cleanup operation failed")
    except BaseException:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def close_interactive_runtime(runtime: InteractiveRuntime) -> None:
    if runtime._closed:
        return
    runtime._closed = True
    current = asyncio.current_task()

    managed_tasks = set(runtime.background_tasks)
    if runtime.mcp_task is not None:
        managed_tasks.add(runtime.mcp_task)
    managed_tasks.discard(current)
    for task in managed_tasks:
        if not task.done():
            task.cancel()
    if managed_tasks:
        try:
            await asyncio.gather(*managed_tasks, return_exceptions=True)
        except BaseException:
            pass

    cleanup: list[Awaitable[Any]] = [
        runtime.agent._extract_memories(runtime.conversation)
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
    await _run_cleanup_tasks(cleanup, timeout=3.0)

    try:
        for team_name in runtime.team_manager.list_teams():
            try:
                team = runtime.team_manager.get_team(team_name)
                if team is not None:
                    for member in team.members:
                        team.set_member_active(member.name, False)
                runtime.team_manager.delete_team(team_name)
            except BaseException:
                log.exception("Failed to delete team %s", team_name)
    finally:
        try:
            runtime.session.close()
        except BaseException:
            log.exception("Failed to close interactive session")
