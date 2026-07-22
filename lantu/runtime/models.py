from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from lantu.agent import Agent
from lantu.agents.loader import AgentLoader
from lantu.agents.task_manager import TaskManager
from lantu.agents.trace import TraceManager
from lantu.cache import FileCache
from lantu.client import LLMClient
from lantu.commands.registry import CommandRegistry
from lantu.config import AppConfig, ProviderConfig
from lantu.conversation import ConversationManager
from lantu.filehistory import FileHistory
from lantu.hooks import HookEngine
from lantu.mcp import MCPManager
from lantu.memory import MemoryManager, Session, SessionManager
from lantu.permissions import PermissionChecker
from lantu.skills.executor import SkillExecutor
from lantu.skills.loader import SkillLoader
from lantu.teams.manager import TeamManager
from lantu.tools import ToolRegistry
from lantu.worktree.manager import WorktreeManager


@dataclass
class InteractiveRuntime:
    config: AppConfig
    provider: ProviderConfig
    client: LLMClient
    agent: Agent
    conversation: ConversationManager
    registry: ToolRegistry
    command_registry: CommandRegistry
    memory_manager: MemoryManager
    session_manager: SessionManager
    session: Session
    skill_loader: SkillLoader
    skill_executor: SkillExecutor
    task_manager: TaskManager
    trace_manager: TraceManager
    worktree_manager: WorktreeManager
    team_manager: TeamManager
    hook_engine: HookEngine | None
    work_dir: Path
    permission_checker: PermissionChecker
    file_cache: FileCache
    file_history: FileHistory
    agent_loader: AgentLoader
    load_skill_tool: Any
    install_skill_tool: Any
    exit_plan_mode_tool: Any
    mcp_manager: MCPManager | None = None
    mcp_instructions: str = ""
    mcp_task: asyncio.Task[None] | None = None
    background_tasks: set[asyncio.Task[Any]] = field(default_factory=set)
    startup_messages: list[str] = field(default_factory=list)
    _closed: bool = False

    def refresh_skills_if_needed(self) -> None:
        from lantu.runtime.lifecycle import refresh_runtime_skills

        refresh_runtime_skills(self)

    async def wait_until_ready(self) -> None:
        if self.mcp_task is None:
            return
        try:
            await asyncio.shield(self.mcp_task)
        except asyncio.CancelledError:
            if not self._closed:
                raise
        except Exception as exc:
            self.startup_messages.append(f"MCP warning: {exc}")

    async def prefetch_relevant_memories(self, query: str) -> str:
        from lantu.runtime.lifecycle import prefetch_runtime_memories

        return await prefetch_runtime_memories(self, query)

    async def close(self) -> None:
        from lantu.runtime.lifecycle import close_interactive_runtime

        await close_interactive_runtime(self)
