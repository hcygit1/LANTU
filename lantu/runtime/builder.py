from __future__ import annotations

import asyncio
from pathlib import Path

from lantu.agent import Agent
from lantu.agents.loader import AgentLoader
from lantu.agents.task_manager import TaskManager
from lantu.agents.trace import TraceManager
from lantu.cache import FileCache
from lantu.client import create_client, resolve_context_window
from lantu.commands.handlers import register_all_commands
from lantu.commands.handlers.skill_register import register_skill_commands
from lantu.commands.handlers.tasks import create_tasks_command
from lantu.commands.handlers.trace import create_trace_command
from lantu.commands.handlers.worktree import create_worktree_command
from lantu.commands.registry import CommandRegistry
from lantu.config import AppConfig, ProviderConfig
from lantu.conversation import ConversationManager
from lantu.filehistory import FileHistory
from lantu.hooks import HookContext, HookEngine
from lantu.memory import MemoryManager, SessionManager, load_instructions
from lantu.permissions import (
    DangerousCommandDetector,
    PathSandbox,
    PermissionChecker,
    PermissionMode,
    RuleEngine,
)
from lantu.skills.executor import SkillExecutor
from lantu.skills.loader import SkillLoader
from lantu.teams.manager import TeamManager
from lantu.tools import create_default_registry
from lantu.tools.agent_tool import AgentTool
from lantu.tools.ask_user import AskUserTool
from lantu.tools.enter_worktree import EnterWorktreeTool
from lantu.tools.exit_plan_mode import ExitPlanModeTool
from lantu.tools.exit_worktree import ExitWorktreeTool
from lantu.tools.impl.tool_search import ToolSearchTool
from lantu.tools.install_skill import InstallSkillTool
from lantu.tools.load_skill import LoadSkill
from lantu.tools.synthetic_output import SyntheticOutputTool
from lantu.tools.team_create import TeamCreateTool
from lantu.tools.team_delete import TeamDeleteTool
from lantu.worktree.cleanup import start_stale_cleanup_task
from lantu.worktree.manager import WorktreeManager

from lantu.runtime.lifecycle import (
    _render_agent_catalog,
    _render_skill_catalog,
    close_interactive_runtime,
    initialize_runtime_mcp,
    switch_runtime_work_dir,
    track_background_task,
)
from lantu.runtime.models import InteractiveRuntime


async def _build_core(
    config: AppConfig,
    provider: ProviderConfig,
    permission_mode: PermissionMode,
    hook_engine: HookEngine | None,
    work_dir: Path,
) -> InteractiveRuntime:
    client = create_client(provider)
    try:
        work_dir_str = str(work_dir)
        home = Path.home()
        checker = PermissionChecker(
            detector=DangerousCommandDetector(),
            sandbox=PathSandbox(work_dir_str),
            rule_engine=RuleEngine(
                user_rules_path=home / ".lantu" / "permissions.yaml",
                project_rules_path=work_dir / ".lantu" / "permissions.yaml",
                local_rules_path=work_dir / ".lantu" / "permissions.local.yaml",
            ),
            mode=permission_mode,
            sandbox_enabled=config.sandbox.enabled and config.sandbox.auto_allow,
        )
        memory_manager = MemoryManager(work_dir_str)
        session_manager = SessionManager(work_dir_str)
        session_manager.cleanup()
        session = session_manager.create()
    except BaseException:
        try:
            await client.aclose()
        except BaseException:
            pass
        raise

    try:
        file_cache = FileCache()
        file_history = FileHistory(work_dir_str, session.session_id)
        registry = create_default_registry(
            file_cache=file_cache,
            file_history=file_history,
            work_dir=work_dir_str,
        )
        for tool in registry.list_tools():
            if hasattr(tool, "file_history"):
                tool.file_history = file_history

        if config.sandbox.enabled:
            from lantu.sandbox import SandboxConfig, create_sandbox

            os_sandbox = create_sandbox()
            if os_sandbox is not None and os_sandbox.available():
                bash_tool = registry.get("Bash")
                if bash_tool is not None:
                    bash_tool.sandbox = os_sandbox
                    bash_tool.sandbox_config = SandboxConfig(
                        allow_write=[work_dir_str, "/tmp"],
                        deny_write=[
                            str(work_dir / ".lantu" / "config.yaml"),
                            str(work_dir / ".lantu" / "permissions.local.yaml"),
                        ],
                        network_enabled=config.sandbox.network_enabled,
                    )

        agent = Agent(
            client=client,
            registry=registry,
            protocol=provider.protocol,
            work_dir=work_dir_str,
            permission_checker=checker,
            context_window=provider.get_context_window(),
            instructions_content=load_instructions(work_dir_str),
            memory_manager=memory_manager,
            hook_engine=hook_engine,
        )
        agent.file_history = file_history
        agent.session_id = session.session_id

        command_registry = CommandRegistry()
        register_all_commands(command_registry)
        task_manager = TaskManager()
        trace_manager = TraceManager()

        # The remaining managers are replaced in the later phases.
        skill_loader = SkillLoader(work_dir_str)
        skill_executor = SkillExecutor(agent, client, provider.protocol)
        worktree_manager = WorktreeManager(work_dir_str)
        agent_loader = AgentLoader(work_dir_str)
        team_manager = TeamManager(worktree_manager, trace_manager)
        return InteractiveRuntime(
            config=config,
            provider=provider,
            client=client,
            agent=agent,
            conversation=ConversationManager(),
            registry=registry,
            command_registry=command_registry,
            memory_manager=memory_manager,
            session_manager=session_manager,
            session=session,
            skill_loader=skill_loader,
            skill_executor=skill_executor,
            task_manager=task_manager,
            trace_manager=trace_manager,
            worktree_manager=worktree_manager,
            team_manager=team_manager,
            hook_engine=hook_engine,
            work_dir=work_dir,
            permission_checker=checker,
            file_cache=file_cache,
            file_history=file_history,
            agent_loader=agent_loader,
            load_skill_tool=None,
            install_skill_tool=None,
            exit_plan_mode_tool=None,
        )
    except BaseException:
        session.close()
        try:
            await client.aclose()
        except BaseException:
            pass
        raise


def _register_skills(runtime: InteractiveRuntime) -> None:
    load_skill_tool = LoadSkill()
    install_skill_tool = InstallSkillTool()
    exit_plan_mode_tool = ExitPlanModeTool()
    runtime.registry.register(load_skill_tool)
    runtime.registry.register(install_skill_tool)
    runtime.registry.register(
        ToolSearchTool(runtime.registry, protocol=runtime.provider.protocol)
    )
    runtime.registry.register(AskUserTool())
    runtime.registry.register(exit_plan_mode_tool)

    exit_plan_mode_tool._is_plan_mode = lambda: runtime.agent.plan_mode
    exit_plan_mode_tool._plan_exists = lambda: runtime.agent._get_plan_path().exists()

    runtime.skill_loader.load_all()
    load_skill_tool.set_loader(runtime.skill_loader)
    load_skill_tool.set_agent(runtime.agent)
    install_skill_tool.set_loader(runtime.skill_loader)
    runtime.agent.set_skill_catalog(
        _render_skill_catalog(runtime.skill_loader.get_catalog())
    )
    register_skill_commands(
        runtime.command_registry, runtime.skill_loader, runtime.skill_executor
    )

    def on_skill_installed(_name: str) -> None:
        register_skill_commands(
            runtime.command_registry, runtime.skill_loader, runtime.skill_executor
        )
        runtime.agent.set_skill_catalog(
            _render_skill_catalog(runtime.skill_loader.get_catalog())
        )

    install_skill_tool.set_on_installed(on_skill_installed)
    runtime.load_skill_tool = load_skill_tool
    runtime.install_skill_tool = install_skill_tool
    runtime.exit_plan_mode_tool = exit_plan_mode_tool


def _register_worktree_and_agents(runtime: InteractiveRuntime) -> None:
    config = runtime.config
    work_dir_str = str(runtime.work_dir)
    runtime.worktree_manager = WorktreeManager(
        repo_root=work_dir_str,
        symlink_directories=config.worktree.symlink_directories,
    )
    restored = runtime.worktree_manager.restore_session()
    if restored is not None:
        switch_runtime_work_dir(runtime, restored.worktree_path)
    switch_work_dir = lambda path: switch_runtime_work_dir(runtime, path)
    runtime.command_registry.register_sync(
        create_worktree_command(
            runtime.worktree_manager, on_work_dir_changed=switch_work_dir
        )
    )
    runtime.registry.register(
        EnterWorktreeTool(
            runtime.worktree_manager, on_work_dir_changed=switch_work_dir
        )
    )
    runtime.registry.register(
        ExitWorktreeTool(
            runtime.worktree_manager, on_work_dir_changed=switch_work_dir
        )
    )

    runtime.agent_loader = AgentLoader(
        work_dir_str, enable_verification=config.enable_verification_agent
    )
    runtime.agent_loader.load_all()
    agent_catalog = runtime.agent_loader.list_agents()
    runtime.agent.set_agent_catalog(
        _render_agent_catalog(agent_catalog, config.enable_fork),
        catalog_list=agent_catalog,
    )

    runtime.team_manager = TeamManager(
        worktree_manager=runtime.worktree_manager,
        trace_manager=runtime.trace_manager,
    )
    runtime.registry.register(
        AgentTool(
            agent_loader=runtime.agent_loader,
            task_manager=runtime.task_manager,
            trace_manager=runtime.trace_manager,
            parent_agent=runtime.agent,
            enable_fork=config.enable_fork,
            provider_config=runtime.provider,
            worktree_manager=runtime.worktree_manager,
            team_manager=runtime.team_manager,
        )
    )
    runtime.registry.register(
        TeamCreateTool(
            team_manager=runtime.team_manager,
            parent_agent=runtime.agent,
            teammate_mode=config.teammate_mode,
            is_interactive=True,
            enable_coordinator_mode=config.enable_coordinator_mode,
        )
    )
    runtime.registry.register(
        TeamDeleteTool(runtime.team_manager, parent_agent=runtime.agent)
    )
    runtime.registry.register(SyntheticOutputTool())
    runtime.command_registry.register_sync(create_tasks_command(runtime.task_manager))
    runtime.command_registry.register_sync(
        create_trace_command(runtime.trace_manager, runtime.agent.agent_id)
    )
    runtime.agent._team_manager = runtime.team_manager
    runtime.agent.notification_fn = runtime.team_manager.drain_lead_mailbox
    switch_runtime_work_dir(runtime, runtime.agent.work_dir)


async def _start_runtime_services(runtime: InteractiveRuntime) -> None:
    async def update_context_window() -> None:
        await resolve_context_window(runtime.provider)
        if not runtime._closed:
            runtime.agent.context_window = runtime.provider.get_context_window()

    track_background_task(runtime, update_context_window())
    track_background_task(
        runtime,
        start_stale_cleanup_task(
            runtime.worktree_manager,
            runtime.config.worktree.stale_cleanup_interval,
            runtime.config.worktree.stale_cutoff_hours,
        ),
    )
    if runtime.hook_engine is not None:
        track_background_task(
            runtime,
            runtime.hook_engine.run_hooks(
                "startup", HookContext(event_name="startup")
            ),
        )
    if runtime.config.mcp_servers:
        runtime.mcp_task = asyncio.create_task(initialize_runtime_mcp(runtime))
        runtime.mcp_task.add_done_callback(lambda task: task.exception() if not task.cancelled() else None)


async def build_interactive_runtime(
    config: AppConfig,
    provider: ProviderConfig,
    permission_mode: PermissionMode,
    hook_engine: HookEngine | None,
    work_dir: str | Path,
) -> InteractiveRuntime:
    runtime = await _build_core(
        config, provider, permission_mode, hook_engine, Path(work_dir).resolve()
    )
    try:
        _register_skills(runtime)
        _register_worktree_and_agents(runtime)
        await _start_runtime_services(runtime)
        return runtime
    except BaseException:
        await close_interactive_runtime(runtime)
        raise
