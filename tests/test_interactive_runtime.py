from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import AsyncIterator

import pytest

from lantu.client import LLMClient
from lantu.config import AppConfig, MCPServerConfig, ProviderConfig
from lantu.mcp import ConnectResult, ServerInfo
from lantu.permissions import PermissionMode
from lantu.runtime import build_interactive_runtime
from lantu.runtime import builder as runtime_builder
from lantu.runtime import lifecycle
from lantu.tools.base import StreamEnd, StreamEvent, TextDelta


class FakeClient(LLMClient):
    def __init__(self, events: list[StreamEvent] | None = None) -> None:
        self.events = events or [StreamEnd(stop_reason="end_turn")]

    async def stream(self, conversation, system="", tools=None) -> AsyncIterator[StreamEvent]:
        for event in self.events:
            yield event


@pytest.fixture
def provider() -> ProviderConfig:
    return ProviderConfig(
        name="fake",
        protocol="openai-compat",
        base_url="https://example.test/v1",
        model="fake-model",
        api_key="test-key",
    )


@pytest.fixture
def offline_builder(monkeypatch: pytest.MonkeyPatch) -> FakeClient:
    client = FakeClient()
    monkeypatch.setattr(runtime_builder, "create_client", lambda _provider: client)

    async def resolve(_provider: ProviderConfig) -> None:
        return None

    async def stale_cleanup(*_args, **_kwargs) -> None:
        await asyncio.Event().wait()

    monkeypatch.setattr(runtime_builder, "resolve_context_window", resolve)
    monkeypatch.setattr(runtime_builder, "start_stale_cleanup_task", stale_cleanup)
    return client


@pytest.mark.asyncio
async def test_build_runtime_registers_engineering_tools_and_closes_twice(
    tmp_path: Path,
    provider: ProviderConfig,
    offline_builder: FakeClient,
) -> None:
    runtime = await build_interactive_runtime(
        AppConfig(providers=[provider]),
        provider,
        PermissionMode.DEFAULT,
        None,
        tmp_path,
    )

    tool_names = {tool.name for tool in runtime.registry.list_tools()}
    assert {
        "ReadFile",
        "WriteFile",
        "EditFile",
        "Bash",
        "ToolSearch",
        "AskUserQuestion",
        "ExitPlanMode",
        "Agent",
        "TeamCreate",
    } <= tool_names
    assert runtime.session.session_id == runtime.agent.session_id
    assert runtime.agent.notification_fn == runtime.team_manager.drain_lead_mailbox
    command_names = {
        command.name for command in runtime.command_registry.list_commands()
    }
    assert {"help", "worktree", "tasks", "trace"} <= command_names

    sleeper = asyncio.create_task(asyncio.Event().wait())
    runtime.background_tasks.add(sleeper)

    await runtime.close()
    await runtime.close()

    assert sleeper.cancelled()
    assert runtime.session._file.closed


@pytest.mark.asyncio
async def test_refresh_skills_reloads_commands_and_clears_empty_catalog(
    tmp_path: Path,
    provider: ProviderConfig,
    offline_builder: FakeClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = await build_interactive_runtime(
        AppConfig(providers=[provider]), provider, PermissionMode.DEFAULT, None, tmp_path
    )
    calls: list[str] = []
    monkeypatch.setattr(runtime.skill_loader, "needs_reload", lambda: True)
    monkeypatch.setattr(runtime.skill_loader, "reload", lambda: calls.append("reload"))
    monkeypatch.setattr(runtime.skill_loader, "get_catalog", lambda: [])
    monkeypatch.setattr(
        lifecycle,
        "register_skill_commands",
        lambda *_args: calls.append("commands"),
    )

    runtime.refresh_skills_if_needed()

    assert calls == ["reload", "commands"]
    assert runtime.agent._skill_catalog == ""
    await runtime.close()


@pytest.mark.asyncio
async def test_prefetch_memories_uses_fresh_client_and_renders_result(
    tmp_path: Path,
    provider: ProviderConfig,
    offline_builder: FakeClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = await build_interactive_runtime(
        AppConfig(providers=[provider]), provider, PermissionMode.DEFAULT, None, tmp_path
    )
    side_client = FakeClient([TextDelta('{"selected_memories": []}'), StreamEnd("end_turn")])
    monkeypatch.setattr(lifecycle, "create_client", lambda selected: side_client)

    async def find_memories(**kwargs):
        selected = await kwargs["selector"]("selector system", "selector query")
        assert selected == '{"selected_memories": []}'
        return ["memory-result"]

    monkeypatch.setattr(lifecycle, "find_relevant_memories", find_memories)
    monkeypatch.setattr(lifecycle, "render_reminder", lambda results: f"rendered:{results[0]}")

    assert await runtime.prefetch_relevant_memories("query") == "rendered:memory-result"
    await runtime.close()


@pytest.mark.asyncio
async def test_prefetch_memories_propagates_cancellation(
    tmp_path: Path,
    provider: ProviderConfig,
    offline_builder: FakeClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = await build_interactive_runtime(
        AppConfig(providers=[provider]), provider, PermissionMode.DEFAULT, None, tmp_path
    )

    async def never_returns(**_kwargs):
        await asyncio.Event().wait()

    monkeypatch.setattr(lifecycle, "find_relevant_memories", never_returns)
    task = asyncio.create_task(runtime.prefetch_relevant_memories("query"))
    await asyncio.sleep(0)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
    await runtime.close()


@pytest.mark.asyncio
async def test_mcp_connection_errors_are_non_fatal_and_reported(
    tmp_path: Path,
    provider: ProviderConfig,
    offline_builder: FakeClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeMCPManager:
        def __init__(self) -> None:
            self.configs = []
            self.shutdown_called = False

        def load_configs(self, configs) -> None:
            self.configs = configs

        async def register_all_tools(self, _registry) -> ConnectResult:
            return ConnectResult(
                servers=[ServerInfo(name="offline", instructions="Use offline tools.")],
                errors=["offline connection failed"],
            )

        async def shutdown(self) -> None:
            self.shutdown_called = True

    monkeypatch.setattr(lifecycle, "MCPManager", FakeMCPManager)
    config = AppConfig(
        providers=[provider],
        mcp_servers=[MCPServerConfig(name="offline", command="never-run")],
    )

    runtime = await build_interactive_runtime(
        config, provider, PermissionMode.DEFAULT, None, tmp_path
    )
    await runtime.wait_until_ready()

    assert "# MCP Server Instructions" in runtime.mcp_instructions
    assert "Use offline tools." in runtime.mcp_instructions
    assert any("offline connection failed" in message for message in runtime.startup_messages)
    manager = runtime.mcp_manager
    await runtime.close()
    assert manager.shutdown_called


@pytest.mark.asyncio
async def test_mcp_instructions_fall_back_to_registered_tool_names(
    tmp_path: Path,
    provider: ProviderConfig,
    offline_builder: FakeClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeMCPManager:
        def load_configs(self, _configs) -> None:
            return None

        async def register_all_tools(self, registry) -> ConnectResult:
            registry.register(SimpleNamespace(name="mcp__fallback__search"))
            return ConnectResult(servers=[ServerInfo(name="fallback")])

        async def shutdown(self) -> None:
            return None

    monkeypatch.setattr(lifecycle, "MCPManager", FakeMCPManager)
    config = AppConfig(
        providers=[provider],
        mcp_servers=[MCPServerConfig(name="fallback", command="never-run")],
    )
    runtime = await build_interactive_runtime(
        config, provider, PermissionMode.DEFAULT, None, tmp_path
    )

    await runtime.wait_until_ready()

    assert "Available tools: mcp__fallback__search" in runtime.mcp_instructions
    await runtime.close()


@pytest.mark.asyncio
async def test_close_during_mcp_initialization_does_not_publish_manager(
    tmp_path: Path,
    provider: ProviderConfig,
    offline_builder: FakeClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = asyncio.Event()
    managers = []

    class SlowMCPManager:
        def __init__(self) -> None:
            self.shutdown_called = False
            managers.append(self)

        def load_configs(self, _configs) -> None:
            return None

        async def register_all_tools(self, _registry) -> ConnectResult:
            started.set()
            await asyncio.Event().wait()
            return ConnectResult()

        async def shutdown(self) -> None:
            self.shutdown_called = True

    monkeypatch.setattr(lifecycle, "MCPManager", SlowMCPManager)
    config = AppConfig(
        providers=[provider],
        mcp_servers=[MCPServerConfig(name="slow", command="never-run")],
    )
    runtime = await build_interactive_runtime(
        config, provider, PermissionMode.DEFAULT, None, tmp_path
    )
    await started.wait()

    await runtime.close()

    assert runtime.mcp_manager is None
    assert managers[0].shutdown_called


@pytest.mark.asyncio
async def test_close_still_closes_session_when_close_task_is_cancelled(
    tmp_path: Path,
    provider: ProviderConfig,
    offline_builder: FakeClient,
) -> None:
    runtime = await build_interactive_runtime(
        AppConfig(providers=[provider]), provider, PermissionMode.DEFAULT, None, tmp_path
    )
    cancelling = asyncio.Event()

    async def slow_to_cancel() -> None:
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelling.set()
            await asyncio.sleep(1)

    background = asyncio.create_task(slow_to_cancel())
    runtime.background_tasks.add(background)
    close_task = asyncio.create_task(runtime.close())
    await cancelling.wait()
    close_task.cancel()

    await close_task

    assert runtime.session._file.closed


@pytest.mark.asyncio
async def test_partial_build_failure_closes_created_session(
    tmp_path: Path,
    provider: ProviderConfig,
    offline_builder: FakeClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created_sessions = []
    real_manager = runtime_builder.SessionManager

    class RecordingSessionManager(real_manager):
        def create(self):
            session = super().create()
            created_sessions.append(session)
            return session

    def fail_skills(_runtime) -> None:
        raise RuntimeError("skill registration failed")

    monkeypatch.setattr(runtime_builder, "SessionManager", RecordingSessionManager)
    monkeypatch.setattr(runtime_builder, "_register_skills", fail_skills)

    with pytest.raises(RuntimeError, match="skill registration failed"):
        await build_interactive_runtime(
            AppConfig(providers=[provider]),
            provider,
            PermissionMode.DEFAULT,
            None,
            tmp_path,
        )

    assert created_sessions[0]._file.closed


def test_importing_runtime_does_not_load_textual() -> None:
    code = (
        "import sys; import lantu.runtime; "
        "assert not any(n == 'textual' or n.startswith('textual.') for n in sys.modules)"
    )
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(sys.path)}
    result = subprocess.run(
        [sys.executable, "-c", code],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
