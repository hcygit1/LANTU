from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import AsyncIterator

import pytest

from lantu.client import LLMClient
from lantu.config import AppConfig, MCPServerConfig, ProviderConfig
from lantu.commands.handlers.worktree import create_worktree_command
from lantu.commands.registry import CommandContext
from lantu.mcp import ConnectResult, ServerInfo
from lantu.permissions import PermissionMode
from lantu.runtime import build_interactive_runtime
from lantu.runtime import builder as runtime_builder
from lantu.runtime import lifecycle
from lantu.tools import create_default_registry
from lantu.tools.base import StreamEnd, StreamEvent, TextDelta
from lantu.tools.edit_file import Params as EditFileParams
from lantu.tools.enter_worktree import EnterWorktreeParams, EnterWorktreeTool
from lantu.tools.exit_worktree import ExitWorktreeParams, ExitWorktreeTool
from lantu.tools.glob import Params as GlobParams
from lantu.tools.grep import Params as GrepParams
from lantu.tools.read_file import Params as ReadFileParams
from lantu.tools.write_file import Params as WriteFileParams
from lantu.teams.models import AgentTeam
from lantu.worktree.models import WorktreeSession


class FakeClient(LLMClient):
    def __init__(self, events: list[StreamEvent] | None = None) -> None:
        self.events = events or [StreamEnd(stop_reason="end_turn")]
        self.close_calls = 0

    async def stream(self, conversation, system="", tools=None) -> AsyncIterator[StreamEvent]:
        for event in self.events:
            yield event

    async def aclose(self) -> None:
        self.close_calls += 1


class DefaultCloseClient(LLMClient):
    def __init__(self, transport) -> None:
        self._client = transport

    async def stream(self, *_args, **_kwargs) -> AsyncIterator[StreamEvent]:
        yield StreamEnd("end_turn")


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
    assert offline_builder.close_calls == 1


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
    assert side_client.close_calls == 1
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
    release = asyncio.Event()

    async def slow_to_cancel() -> None:
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelling.set()
            await release.wait()

    background = asyncio.create_task(slow_to_cancel())
    runtime.background_tasks.add(background)
    close_task = asyncio.create_task(runtime.close())
    await cancelling.wait()
    close_task.cancel()

    try:
        with pytest.raises(asyncio.CancelledError):
            await close_task
        assert runtime.session._file.closed
        assert not background.done()
    finally:
        release.set()
        await asyncio.gather(background, return_exceptions=True)


@pytest.mark.asyncio
async def test_cleanup_timeout_does_not_wait_for_stubborn_cancelled_task() -> None:
    release = asyncio.Event()
    cancelled = asyncio.Event()
    task_refs: list[asyncio.Task[None]] = []

    async def stubborn_cleanup() -> None:
        task = asyncio.current_task()
        assert task is not None
        task_refs.append(task)
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            await release.wait()

    started = time.monotonic()
    await asyncio.wait_for(
        lifecycle._run_cleanup_tasks([stubborn_cleanup()], timeout=0.01),
        timeout=0.2,
    )
    await asyncio.sleep(0)

    try:
        assert time.monotonic() - started < 0.1
        assert cancelled.is_set()
        assert not task_refs[0].done()
    finally:
        release.set()
        await asyncio.gather(*task_refs, return_exceptions=True)


@pytest.mark.asyncio
async def test_cancelled_cleanup_wait_detaches_unfinished_task() -> None:
    release = asyncio.Event()
    started = asyncio.Event()
    cancelled = asyncio.Event()
    task_refs: list[asyncio.Task[None]] = []

    async def stubborn_cleanup() -> None:
        task = asyncio.current_task()
        assert task is not None
        task_refs.append(task)
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            await release.wait()

    cleanup_wait = asyncio.create_task(
        lifecycle._run_cleanup_tasks([stubborn_cleanup()], timeout=10)
    )
    await started.wait()
    cleanup_wait.cancel()

    await asyncio.wait_for(cleanup_wait, timeout=0.1)
    await asyncio.sleep(0)

    try:
        assert cancelled.is_set()
        assert not task_refs[0].done()
    finally:
        release.set()
        await asyncio.gather(*task_refs, return_exceptions=True)


@pytest.mark.asyncio
async def test_cleanup_consumes_ordinary_task_exception(caplog) -> None:
    async def fails() -> None:
        raise RuntimeError("cleanup failed")

    await lifecycle._run_cleanup_tasks([fails()], timeout=0.1)

    assert "Background runtime task failed" in caplog.text
    assert "cleanup failed" in caplog.text


@pytest.mark.asyncio
async def test_close_does_not_wait_for_stubborn_background_task(
    tmp_path: Path,
    provider: ProviderConfig,
    offline_builder: FakeClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = await build_interactive_runtime(
        AppConfig(providers=[provider]), provider, PermissionMode.DEFAULT, None, tmp_path
    )
    release = asyncio.Event()
    cancelled = asyncio.Event()

    async def stubborn_background() -> None:
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            await release.wait()

    background = asyncio.create_task(stubborn_background())
    runtime.background_tasks.add(background)
    await asyncio.sleep(0)
    monkeypatch.setattr(lifecycle, "BACKGROUND_TASK_CANCEL_TIMEOUT", 0.01)
    started = time.monotonic()

    await asyncio.wait_for(runtime.close(), timeout=0.2)

    try:
        assert time.monotonic() - started < 0.1
        assert cancelled.is_set()
        assert not background.done()
        assert runtime.session._file.closed
    finally:
        release.set()
        await asyncio.gather(background, return_exceptions=True)


@pytest.mark.asyncio
async def test_close_uses_one_total_deadline_for_all_async_cleanup(
    tmp_path: Path,
    provider: ProviderConfig,
    offline_builder: FakeClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = await build_interactive_runtime(
        AppConfig(providers=[provider]), provider, PermissionMode.DEFAULT, None, tmp_path
    )
    release = asyncio.Event()
    cleanup_tasks: list[asyncio.Task[None]] = []

    async def stubborn() -> None:
        task = asyncio.current_task()
        assert task is not None
        cleanup_tasks.append(task)
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            await release.wait()

    background = asyncio.create_task(stubborn())
    runtime.background_tasks.add(background)
    await asyncio.sleep(0)
    monkeypatch.setattr(runtime.agent, "_extract_memories", lambda _conv: stubborn())
    monkeypatch.setattr(lifecycle, "RUNTIME_CLOSE_TIMEOUT", 0.05, raising=False)
    monkeypatch.setattr(lifecycle, "BACKGROUND_TASK_CANCEL_TIMEOUT", 0.04)
    started = time.monotonic()

    await asyncio.wait_for(runtime.close(), timeout=0.2)

    try:
        assert time.monotonic() - started < 0.12
        assert runtime.session._file.closed
        assert any(not task.done() for task in cleanup_tasks)
    finally:
        release.set()
        await asyncio.gather(*cleanup_tasks, return_exceptions=True)


@pytest.mark.asyncio
async def test_close_detaches_blocking_mailbox_cleanup_at_deadline(
    tmp_path: Path,
    provider: ProviderConfig,
    offline_builder: FakeClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = await build_interactive_runtime(
        AppConfig(providers=[provider]), provider, PermissionMode.DEFAULT, None, tmp_path
    )
    team = AgentTeam(name="blocked", lead_agent_id="lead")
    runtime.team_manager._teams[team.name] = team
    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()
    directory_calls: list[str] = []

    class BlockingMailbox:
        def cleanup_all(self, deadline=None) -> None:
            started.set()
            release.wait()
            finished.set()

    runtime.team_manager._mailboxes[team.name] = BlockingMailbox()
    monkeypatch.setattr(
        runtime.team_manager,
        "_remove_dir",
        lambda *_args, **_kwargs: directory_calls.append("remove"),
    )
    monkeypatch.setattr(lifecycle, "RUNTIME_CLOSE_TIMEOUT", 0.01)
    began = time.monotonic()
    watchdog = threading.Timer(0.15, release.set)
    watchdog.daemon = True
    watchdog.start()

    await asyncio.wait_for(runtime.close(), timeout=0.4)

    try:
        assert time.monotonic() - began < 0.1
        assert started.is_set()
        assert runtime.session._file.closed
        assert directory_calls == []
    finally:
        watchdog.cancel()
        release.set()
        for _ in range(100):
            if finished.is_set():
                break
            await asyncio.sleep(0.001)
        assert finished.is_set()


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
    assert offline_builder.close_calls == 1


@pytest.mark.asyncio
async def test_core_build_failure_closes_created_client(
    tmp_path: Path,
    provider: ProviderConfig,
    offline_builder: FakeClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        runtime_builder.SessionManager,
        "cleanup",
        lambda _self: (_ for _ in ()).throw(RuntimeError("cleanup failed")),
    )

    with pytest.raises(RuntimeError, match="cleanup failed"):
        await build_interactive_runtime(
            AppConfig(providers=[provider]),
            provider,
            PermissionMode.DEFAULT,
            None,
            tmp_path,
        )

    assert offline_builder.close_calls == 1


@pytest.mark.asyncio
async def test_cancelled_waiter_does_not_cancel_shared_mcp_task(
    tmp_path: Path,
    provider: ProviderConfig,
    offline_builder: FakeClient,
) -> None:
    runtime = await build_interactive_runtime(
        AppConfig(providers=[provider]), provider, PermissionMode.DEFAULT, None, tmp_path
    )
    release = asyncio.Event()
    runtime.mcp_task = asyncio.create_task(release.wait())
    first = asyncio.create_task(runtime.wait_until_ready())
    second = asyncio.create_task(runtime.wait_until_ready())
    await asyncio.sleep(0)
    first.cancel()

    with pytest.raises(asyncio.CancelledError):
        await first
    assert not runtime.mcp_task.done()
    release.set()
    await second
    await runtime.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["error", "timeout"])
async def test_prefetch_closes_side_client_on_failure(
    tmp_path: Path,
    provider: ProviderConfig,
    offline_builder: FakeClient,
    monkeypatch: pytest.MonkeyPatch,
    outcome: str,
) -> None:
    runtime = await build_interactive_runtime(
        AppConfig(providers=[provider]), provider, PermissionMode.DEFAULT, None, tmp_path
    )

    class FailingSideClient(FakeClient):
        async def stream(self, *_args, **_kwargs):
            if outcome == "timeout":
                await asyncio.Event().wait()
            raise RuntimeError("side query failed")
            yield StreamEnd("end_turn")

    side_client = FailingSideClient()
    monkeypatch.setattr(lifecycle, "create_client", lambda _provider: side_client)

    async def find_memories(**kwargs):
        await kwargs["selector"]("system", "query")

    monkeypatch.setattr(lifecycle, "find_relevant_memories", find_memories)
    if outcome == "timeout":
        monkeypatch.setattr(
            lifecycle, "MEMORY_PREFETCH_TIMEOUT", 0.01, raising=False
        )

    assert await asyncio.wait_for(
        runtime.prefetch_relevant_memories("query"), timeout=0.2
    ) == ""
    assert side_client.close_calls == 1
    await runtime.close()


@pytest.mark.asyncio
async def test_prefetch_closes_side_client_on_cancellation(
    tmp_path: Path,
    provider: ProviderConfig,
    offline_builder: FakeClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = await build_interactive_runtime(
        AppConfig(providers=[provider]), provider, PermissionMode.DEFAULT, None, tmp_path
    )
    started = asyncio.Event()

    class BlockingSideClient(FakeClient):
        async def stream(self, *_args, **_kwargs):
            started.set()
            await asyncio.Event().wait()
            yield StreamEnd("end_turn")

    side_client = BlockingSideClient()
    monkeypatch.setattr(lifecycle, "create_client", lambda _provider: side_client)

    async def find_memories(**kwargs):
        await kwargs["selector"]("system", "query")

    monkeypatch.setattr(lifecycle, "find_relevant_memories", find_memories)
    task = asyncio.create_task(runtime.prefetch_relevant_memories("query"))
    await started.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert side_client.close_calls == 1
    await runtime.close()


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


@pytest.mark.asyncio
async def test_llm_client_close_failure_can_be_retried() -> None:
    class Transport:
        calls = 0

        async def aclose(self) -> None:
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("close failed")

    transport = Transport()
    client = DefaultCloseClient(transport)

    with pytest.raises(RuntimeError, match="close failed"):
        await client.aclose()
    await client.aclose()

    assert transport.calls == 2


@pytest.mark.asyncio
async def test_concurrent_llm_client_close_calls_share_one_task() -> None:
    started = asyncio.Event()
    release = asyncio.Event()

    class Transport:
        calls = 0

        async def aclose(self) -> None:
            self.calls += 1
            started.set()
            await release.wait()

    transport = Transport()
    client = DefaultCloseClient(transport)
    first = asyncio.create_task(client.aclose())
    await started.wait()
    second = asyncio.create_task(client.aclose())
    await asyncio.sleep(0)

    assert not second.done()
    release.set()
    await asyncio.gather(first, second)
    assert transport.calls == 1


@pytest.mark.asyncio
async def test_cancelled_close_waiter_does_not_cancel_shared_client_close() -> None:
    started = asyncio.Event()
    release = asyncio.Event()

    class Transport:
        calls = 0

        async def aclose(self) -> None:
            self.calls += 1
            started.set()
            await release.wait()

    transport = Transport()
    client = DefaultCloseClient(transport)
    first = asyncio.create_task(client.aclose())
    await started.wait()
    second = asyncio.create_task(client.aclose())
    await asyncio.sleep(0)
    first.cancel()

    with pytest.raises(asyncio.CancelledError):
        await first
    assert not second.done()
    release.set()
    await second
    assert transport.calls == 1


@pytest.mark.asyncio
async def test_default_file_tools_resolve_relative_paths_from_work_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    process_dir = tmp_path / "process"
    runtime_dir = tmp_path / "runtime"
    process_dir.mkdir()
    runtime_dir.mkdir()
    tracked: list[str] = []
    history = SimpleNamespace(track_edit=tracked.append)
    registry = create_default_registry(
        file_history=history, work_dir=str(runtime_dir)
    )
    monkeypatch.chdir(process_dir)

    write_result = await registry.get("WriteFile").execute(
        WriteFileParams(file_path="nested/example.txt", content="alpha")
    )
    read_result = await registry.get("ReadFile").execute(
        ReadFileParams(file_path="nested/example.txt")
    )
    edit_result = await registry.get("EditFile").execute(
        EditFileParams(
            file_path="nested/example.txt", old_string="alpha", new_string="beta"
        )
    )
    glob_result = await registry.get("Glob").execute(
        GlobParams(pattern="**/*.txt")
    )
    grep_result = await registry.get("Grep").execute(
        GrepParams(pattern="beta")
    )

    resolved = str((runtime_dir / "nested" / "example.txt").resolve())
    assert not write_result.is_error
    assert "alpha" in read_result.output
    assert not edit_result.is_error
    assert (runtime_dir / "nested" / "example.txt").read_text() == "beta"
    assert "nested/example.txt" in glob_result.output
    assert "nested/example.txt:1:beta" in grep_result.output
    assert tracked == [resolved, resolved]
    assert not (process_dir / "nested" / "example.txt").exists()


@pytest.mark.asyncio
async def test_restored_worktree_switches_all_runtime_path_owners(
    tmp_path: Path,
    provider: ProviderConfig,
    offline_builder: FakeClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    restored_dir = tmp_path / "restored"
    restored_dir.mkdir()
    restored = WorktreeSession(
        original_cwd=str(tmp_path),
        worktree_path=str(restored_dir),
        worktree_name="restored",
        original_branch="main",
        original_head_commit="abc",
    )
    monkeypatch.setattr(
        runtime_builder.WorktreeManager, "restore_session", lambda _self: restored
    )

    runtime = await build_interactive_runtime(
        AppConfig(providers=[provider]), provider, PermissionMode.DEFAULT, None, tmp_path
    )

    assert runtime.agent.work_dir == str(restored_dir)
    assert runtime.permission_checker.sandbox.project_root == restored_dir.resolve()
    assert runtime.registry.get("Bash").work_dir == str(restored_dir)
    assert all(
        tool.work_dir == str(restored_dir)
        for tool in runtime.registry.list_tools()
    )
    await runtime.close()


@pytest.mark.asyncio
async def test_worktree_tools_call_directory_switch_callback(tmp_path: Path) -> None:
    worktree_dir = tmp_path / "worktree"
    session = WorktreeSession(
        original_cwd=str(tmp_path),
        worktree_path=str(worktree_dir),
        worktree_name="feature",
        original_branch="main",
        original_head_commit="abc",
    )

    class Manager:
        current = None

        def get_current_session(self):
            return self.current

        async def create(self, _name):
            return SimpleNamespace(path=str(worktree_dir), branch="feature")

        async def enter(self, _name):
            self.current = session
            return session

        async def exit(self, *_args, **_kwargs):
            self.current = None

    manager = Manager()
    switched: list[str] = []
    enter = EnterWorktreeTool(manager, on_work_dir_changed=switched.append)
    exit_tool = ExitWorktreeTool(manager, on_work_dir_changed=switched.append)

    await enter.execute(EnterWorktreeParams(name="feature"))
    await exit_tool.execute(ExitWorktreeParams(action="keep"))

    assert switched == [str(worktree_dir), str(tmp_path)]


@pytest.mark.asyncio
async def test_worktree_command_calls_directory_switch_callback(tmp_path: Path) -> None:
    worktree_dir = tmp_path / "worktree"
    session = WorktreeSession(
        original_cwd=str(tmp_path),
        worktree_path=str(worktree_dir),
        worktree_name="feature",
        original_branch="main",
        original_head_commit="abc",
    )

    class Manager:
        current_session = None

        async def create(self, _name, _base):
            return SimpleNamespace(path=str(worktree_dir), branch="feature")

        async def enter(self, _name):
            self.current_session = session
            return session

        async def exit(self, *_args, **_kwargs):
            self.current_session = None

        def get_current_session(self):
            return self.current_session

    class UI:
        def add_system_message(self, _message):
            return None

    manager = Manager()
    switched: list[str] = []
    command = create_worktree_command(
        manager, on_work_dir_changed=switched.append
    )
    context = CommandContext(
        args="create feature",
        agent=SimpleNamespace(work_dir=str(tmp_path)),
        conversation=None,
        session=None,
        session_manager=None,
        memory_manager=None,
        ui=UI(),
        config={},
    )
    await command.handler(context)
    context.args = "exit"
    await command.handler(context)

    assert switched == [str(worktree_dir), str(tmp_path)]
