from __future__ import annotations

import asyncio
import gc
import inspect
from pathlib import Path
from types import SimpleNamespace
from typing import Any, AsyncIterator
import weakref

import pytest
from rich.console import Console

from lantu.agent import (
    Agent,
    CompactNotification,
    LoopComplete,
    PermissionRequest,
    PermissionResponse,
    StreamText,
    ToolResultEvent,
    TurnComplete,
)
from lantu.client import LLMClient, LLMError
from lantu.commands import Command, CommandRegistry, CommandType
from lantu.conversation import ConversationManager, Message
from lantu.memory.session import SessionManager
from lantu.permissions import PermissionMode
from lantu.tools import ToolRegistry
from lantu.tools.ask_user import AskUserEvent, AskUserParams, AskUserTool
from lantu.tools.base import StreamEnd, StreamEvent, TextDelta, ToolCallComplete


class FakePrompt:
    def __init__(
        self,
        prompts: list[Any] | None = None,
        choices: list[Any] | None = None,
        texts: list[Any] | None = None,
        many: list[Any] | None = None,
    ) -> None:
        self.prompts = list(prompts or [])
        self.choices = list(choices or [])
        self.texts = list(texts or [])
        self.many = list(many or [])
        self.prompt_calls: list[str] = []
        self.choose_calls: list[tuple[str, list[str]]] = []

    @staticmethod
    def _take(items: list[Any], default: Any = None) -> Any:
        value = items.pop(0) if items else default
        if isinstance(value, BaseException):
            raise value
        return value

    async def prompt(self, status: str) -> str:
        self.prompt_calls.append(status)
        return self._take(self.prompts, EOFError())

    async def choose(self, label: str, choices: list[str]) -> str:
        self.choose_calls.append((label, choices))
        return self._take(self.choices, choices[0])

    async def ask_text(self, label: str) -> str:
        return self._take(self.texts, "")

    async def choose_many(self, label: str, choices: list[str]) -> list[str]:
        return self._take(self.many, [])


class FakeTranscript:
    def __init__(self) -> None:
        self.headers: list[tuple[str, str, str]] = []
        self.user_messages: list[str] = []
        self.assistant_messages: list[str] = []
        self.system_messages: list[str] = []
        self.error_messages: list[str] = []
        self.commits: list[Any] = []
        self.clear_count = 0
        self.details: list[Any] = []
        self.tool_messages: list[Any] = []

    def header(self, model: str, mode: str, work_dir: str) -> None:
        self.headers.append((model, mode, work_dir))

    def user_message(self, content: str) -> None:
        self.user_messages.append(content)

    def assistant_message(self, content: str) -> None:
        self.assistant_messages.append(content)

    def system_message(self, content: str) -> None:
        self.system_messages.append(content)

    def error_message(self, content: str) -> None:
        self.error_messages.append(content)

    def commit(self, renderable: Any, *, blank_after: bool = True) -> None:
        self.commits.append((renderable, blank_after))

    def clear_boundary(self) -> None:
        self.clear_count += 1

    def tool_details(self, state: Any) -> None:
        self.details.append(state)

    def tool(self, state: Any) -> None:
        self.tool_messages.append(state)


class FakeLive:
    def __init__(self) -> None:
        self.stop_count = 0
        self.states: list[Any] = []

    def stop(self) -> None:
        self.stop_count += 1

    def update(self, state: Any) -> None:
        self.states.append(state)


class FakeSession:
    def __init__(self, session_id: str = "session-1") -> None:
        self.session_id = session_id
        self.messages: list[Message] = []
        self.records: list[Any] = []
        self.meta = SimpleNamespace(total_tokens=0)
        self.closed = False
        self.runtime_id: str | None = None
        self.turn_id: str | None = None

    def start_runtime(self, mode: str) -> str:
        self.runtime_id = f"runtime-{mode}"
        return self.runtime_id

    def start_turn(self, trigger: str) -> str:
        if self.runtime_id is None:
            raise RuntimeError("runtime is not active")
        self.turn_id = f"turn-{trigger}"
        return self.turn_id

    def complete_turn(self, iteration_count: int) -> None:
        self.turn_id = None

    def interrupt_turn(self, reason: str) -> None:
        self.turn_id = None

    def context_compacted(self, summary: str, keep: list[Message]) -> None:
        self.records.append(SimpleNamespace(content={"summary": summary}))

    def append(self, message: Message) -> None:
        self.messages.append(message)

    def append_record(self, record: Any) -> None:
        self.records.append(record)

    def close(self) -> None:
        self.closed = True

    def update_total_tokens(self, total: int) -> None:
        self.meta.total_tokens = total


class FakeToolRegistry:
    def __init__(self, ask_tool: AskUserTool | None = None) -> None:
        self.ask_tool = ask_tool
        self.tools: list[Any] = []

    def get(self, name: str) -> Any:
        if name == "AskUserQuestion":
            return self.ask_tool
        return None

    def list_tools(self) -> list[Any]:
        return self.tools


class FakeAgent:
    def __init__(
        self,
        work_dir: Path,
        event_batches: list[list[Any]] | None = None,
        ask_tool: AskUserTool | None = None,
    ) -> None:
        self.work_dir = str(work_dir)
        self.permission_mode = PermissionMode.DEFAULT
        self.context_window = 200_000
        self.total_input_tokens = 11
        self.total_output_tokens = 7
        self.memory_recall_task: asyncio.Task[str] | None = None
        self._memory_recall_consumed = False
        self.session_id = "session-1"
        self.file_history: Any = None
        self.registry = FakeToolRegistry(ask_tool)
        self.notification_fn = None
        self.event_batches = list(event_batches or [[]])
        self.run_count = 0
        self._plan_path = work_dir / "plan.md"

    @property
    def plan_mode(self) -> bool:
        return self.permission_mode == PermissionMode.PLAN

    def set_permission_mode(self, mode: PermissionMode) -> None:
        self.permission_mode = mode

    def _get_plan_path(self) -> Path:
        return self._plan_path

    async def run(self, conversation: ConversationManager):
        self.run_count += 1
        batch = self.event_batches.pop(0) if self.event_batches else []
        assistant = ""
        for event in batch:
            if isinstance(event, StreamText):
                assistant += event.text
            if isinstance(event, LoopComplete) and assistant:
                conversation.add_assistant_message(assistant)
            yield event


class FakeTaskManager:
    def __init__(self, batches: list[list[Any]] | None = None) -> None:
        self.batches = list(batches or [[]])

    def poll_completed(self) -> list[Any]:
        return self.batches.pop(0) if self.batches else []


class FakeTeamManager:
    def __init__(self, batches: list[list[str]] | None = None) -> None:
        self.batches = list(batches or [[]])
        self.drain_count = 0

    def drain_lead_mailbox(self) -> list[str]:
        self.drain_count += 1
        return self.batches.pop(0) if self.batches else []


class FakeRuntime:
    def __init__(
        self,
        tmp_path: Path,
        event_batches: list[list[Any]] | None = None,
        ask_tool: AskUserTool | None = None,
    ) -> None:
        self.agent = FakeAgent(tmp_path, event_batches, ask_tool)
        self.conversation = ConversationManager()
        self.command_registry = CommandRegistry()
        self.session = FakeSession()
        self.session.start_runtime("new")
        self.session_manager = SimpleNamespace()
        self.memory_manager = SimpleNamespace()
        self.skill_loader = SimpleNamespace()
        self.skill_executor = SimpleNamespace()
        self.task_manager = FakeTaskManager()
        self.team_manager = FakeTeamManager()
        self.registry = self.agent.registry
        self.provider = SimpleNamespace(model="test-model")
        self.startup_messages: list[str] = []
        self.mcp_instructions = ""
        self.file_history: Any = None
        self.closed = False
        self.refresh_count = 0
        self.wait_count = 0

    def refresh_skills_if_needed(self) -> None:
        self.refresh_count += 1

    async def wait_until_ready(self) -> None:
        self.wait_count += 1

    async def prefetch_relevant_memories(self, query: str) -> str:
        return ""

    async def close(self) -> None:
        self.closed = True


class ScriptedClient(LLMClient):
    def __init__(self, responses: list[list[StreamEvent]]) -> None:
        self.responses = list(responses)

    async def stream(
        self,
        conversation: ConversationManager,
        system: str = "",
        tools: list[dict[str, Any]] | None = None,
    ) -> AsyncIterator[StreamEvent]:
        for event in self.responses.pop(0):
            yield event


def make_app(
    runtime: FakeRuntime,
    *,
    prompt: FakePrompt | None = None,
    transcript: FakeTranscript | None = None,
    live: FakeLive | None = None,
):
    from lantu.ui.inline.app import InlineApp

    return InlineApp(
        runtime,
        prompt=prompt or FakePrompt(),
        transcript=transcript or FakeTranscript(),
        live=live or FakeLive(),
    )


def render_text(renderable: Any) -> str:
    console = Console(record=True, width=100, color_system=None)
    console.print(renderable)
    return console.export_text()


@pytest.mark.asyncio
async def test_run_sends_prompt_renders_response_and_closes_runtime(tmp_path: Path) -> None:
    runtime = FakeRuntime(
        tmp_path,
        [[StreamText("完成"), LoopComplete(1)]],
    )
    prompt = FakePrompt(prompts=["介绍项目", EOFError()])
    transcript = FakeTranscript()
    app = make_app(runtime, prompt=prompt, transcript=transcript)

    await app.run()

    assert transcript.user_messages == ["介绍项目"]
    assert transcript.assistant_messages == ["完成"]
    assert runtime.closed is True
    assert runtime.conversation.history[0].content == "介绍项目"


@pytest.mark.asyncio
async def test_run_prompt_shows_waiting_state_before_first_agent_event(
    tmp_path: Path,
) -> None:
    runtime = FakeRuntime(tmp_path)
    live = FakeLive()
    app = make_app(runtime, live=live)
    agent_started = asyncio.Event()
    release_agent = asyncio.Event()

    async def delayed_run(conversation: ConversationManager):
        agent_started.set()
        await release_agent.wait()
        yield StreamText("done")
        yield LoopComplete(1)

    runtime.agent.run = delayed_run  # type: ignore[method-assign]
    task = asyncio.create_task(app.run_prompt("hello"))
    await agent_started.wait()

    try:
        assert live.states
        assert live.states[-1].is_waiting is True
    finally:
        release_agent.set()
        await task


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("choice", "expected_response", "expected_audit"),
    [
        ("allow", PermissionResponse.ALLOW, "权限选择: allow"),
        ("always", PermissionResponse.ALLOW_ALWAYS, "权限选择: always"),
        ("deny", PermissionResponse.DENY, "权限选择: deny"),
    ],
)
async def test_handle_permission_audits_selection_before_completing_request(
    tmp_path: Path,
    choice: str,
    expected_response: PermissionResponse,
    expected_audit: str,
) -> None:
    runtime = FakeRuntime(tmp_path)
    future = asyncio.get_running_loop().create_future()

    class OrderingTranscript(FakeTranscript):
        def system_message(self, content: str) -> None:
            assert not future.done()
            super().system_message(content)

    transcript = OrderingTranscript()
    app = make_app(
        runtime,
        prompt=FakePrompt(choices=[choice]),
        transcript=transcript,
    )

    await app.handle_permission(PermissionRequest("Bash", "rm file", future))

    assert future.result() is expected_response
    assert transcript.system_messages == [expected_audit]


@pytest.mark.asyncio
@pytest.mark.parametrize("interruption", [KeyboardInterrupt(), EOFError()])
async def test_handle_permission_audits_default_deny_after_cancel(
    tmp_path: Path,
    interruption: BaseException,
) -> None:
    runtime = FakeRuntime(tmp_path)
    transcript = FakeTranscript()
    app = make_app(
        runtime,
        prompt=FakePrompt(choices=[interruption]),
        transcript=transcript,
    )
    future = asyncio.get_running_loop().create_future()

    await app.handle_permission(PermissionRequest("Bash", "rm file", future))

    assert future.result() is PermissionResponse.DENY
    assert transcript.system_messages == ["权限选择: deny"]


@pytest.mark.asyncio
async def test_handle_permission_shows_and_accepts_numbered_choices(
    tmp_path: Path,
) -> None:
    runtime = FakeRuntime(tmp_path)
    prompt = FakePrompt(choices=["2"])
    transcript = FakeTranscript()
    app = make_app(runtime, prompt=prompt, transcript=transcript)
    future = asyncio.get_running_loop().create_future()

    await app.handle_permission(PermissionRequest("Bash", "rm file", future))

    rendered = render_text(transcript.commits[0][0])
    assert "1" in rendered and "允许一次" in rendered
    assert "2" in rendered and "始终允许" in rendered
    assert "3" in rendered and "拒绝" in rendered
    assert "1、2 或 3" in prompt.choose_calls[0][0]
    assert future.result() is PermissionResponse.ALLOW_ALWAYS


@pytest.mark.asyncio
async def test_interrupt_active_turn_only_cancels_agent_task(tmp_path: Path) -> None:
    runtime = FakeRuntime(tmp_path)
    started = asyncio.Event()

    async def slow_run(conversation: ConversationManager):
        started.set()
        await asyncio.Event().wait()
        yield

    runtime.agent.run = slow_run  # type: ignore[method-assign]
    transcript = FakeTranscript()
    app = make_app(runtime, transcript=transcript)
    turn = asyncio.create_task(app.run_prompt_interruptible("slow"))
    await started.wait()

    app.interrupt_active_turn()

    await turn
    assert app.running is True
    assert app._turn_cancel_requested is False
    assert transcript.system_messages[-1] == "Operation cancelled"


@pytest.mark.asyncio
async def test_external_run_cancellation_propagates_and_closes_runtime(
    tmp_path: Path,
) -> None:
    runtime = FakeRuntime(tmp_path)
    started = asyncio.Event()

    async def slow_run(conversation: ConversationManager):
        started.set()
        await asyncio.Event().wait()
        yield

    runtime.agent.run = slow_run  # type: ignore[method-assign]
    app = make_app(runtime, prompt=FakePrompt(prompts=["slow"]))
    run_task = asyncio.create_task(app.run())
    await started.wait()

    run_task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await run_task
    assert runtime.closed is True


@pytest.mark.asyncio
async def test_external_cancellation_during_prefetch_cleanup_propagates(
    tmp_path: Path,
) -> None:
    runtime = FakeRuntime(tmp_path)
    prefetch_started = asyncio.Event()
    agent_finished = asyncio.Event()
    prefetch_tasks: list[asyncio.Task[str]] = []

    async def blocking_prefetch(query: str) -> str:
        task = asyncio.current_task()
        assert task is not None
        prefetch_tasks.append(task)
        prefetch_started.set()
        await asyncio.Event().wait()
        return ""

    async def completed_agent(conversation: ConversationManager):
        await prefetch_started.wait()
        runtime.agent._memory_recall_consumed = True
        yield LoopComplete(1)
        agent_finished.set()

    runtime.prefetch_relevant_memories = blocking_prefetch  # type: ignore[method-assign]
    runtime.agent.run = completed_agent  # type: ignore[method-assign]
    app = make_app(runtime)
    turn = asyncio.create_task(app.run_prompt_interruptible("prefetch"))
    await agent_finished.wait()
    await asyncio.sleep(0)

    turn.cancel()

    with pytest.raises(asyncio.CancelledError):
        await turn
    for prefetch_task in prefetch_tasks:
        if not prefetch_task.done():
            prefetch_task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await prefetch_task


@pytest.mark.asyncio
async def test_local_interrupt_cleans_blocked_prefetch_and_returns_to_input(
    tmp_path: Path,
) -> None:
    runtime = FakeRuntime(tmp_path)
    prefetch_started = asyncio.Event()
    prefetch_cancelled = asyncio.Event()

    async def blocking_prefetch(query: str) -> str:
        prefetch_started.set()
        try:
            await asyncio.Event().wait()
        finally:
            prefetch_cancelled.set()
        return ""

    async def slow_agent(conversation: ConversationManager):
        await prefetch_started.wait()
        await asyncio.Event().wait()
        yield

    runtime.prefetch_relevant_memories = blocking_prefetch  # type: ignore[method-assign]
    runtime.agent.run = slow_agent  # type: ignore[method-assign]
    app = make_app(runtime)
    turn = asyncio.create_task(app.run_prompt_interruptible("local"))
    await prefetch_started.wait()

    app.interrupt_active_turn()
    await turn

    assert app.running is True
    assert prefetch_cancelled.is_set()
    assert runtime.agent.memory_recall_task is None


@pytest.mark.asyncio
async def test_normal_turn_cancels_unconsumed_prefetch_without_leak(
    tmp_path: Path,
) -> None:
    runtime = FakeRuntime(tmp_path)
    prefetch_started = asyncio.Event()
    prefetch_cancelled = asyncio.Event()

    async def blocking_prefetch(query: str) -> str:
        prefetch_started.set()
        try:
            await asyncio.Event().wait()
        finally:
            prefetch_cancelled.set()
        return ""

    async def completed_agent(conversation: ConversationManager):
        await prefetch_started.wait()
        yield LoopComplete(1)

    runtime.prefetch_relevant_memories = blocking_prefetch  # type: ignore[method-assign]
    runtime.agent.run = completed_agent  # type: ignore[method-assign]
    app = make_app(runtime)

    await app.run_prompt("normal")

    assert prefetch_cancelled.is_set()
    assert runtime.agent.memory_recall_task is None


def test_interaction_renderables_are_sanitized_and_have_explicit_labels() -> None:
    from lantu.ui.inline.components.interaction import (
        render_permission_request,
        render_plan_request,
        render_question,
    )

    permission = render_text(
        render_permission_request("\x1b]8;;https://evil\aBash\x1b]8;;\a", "删除\x1b[31m文件")
    )
    plan = render_text(render_plan_request("/tmp/plan.md", "第一步\x1b[2J"))
    question = render_text(render_question("范围", "请选择", ["当前", "全部"]))

    assert "权限请求" in permission and "工具" in permission and "描述" in permission
    assert "\x1b" not in permission + plan + question
    assert "/tmp/plan.md" in plan and "第一步" in plan
    assert "范围" in question and "当前" in question


@pytest.mark.asyncio
async def test_command_dispatcher_handles_command_protocol(tmp_path: Path) -> None:
    from lantu.ui.inline.commands import InlineCommandDispatcher

    runtime = FakeRuntime(tmp_path)
    transcript = FakeTranscript()
    app = make_app(runtime, transcript=transcript)
    dispatcher = InlineCommandDispatcher(app)

    assert await dispatcher.dispatch("plain text") is False
    assert await dispatcher.dispatch("/") is True
    assert transcript.system_messages[-1].startswith("可用命令")
    assert await dispatcher.dispatch("/missing") is True
    assert transcript.system_messages[-1] == "未知命令：/missing，输入 /help 查看可用命令"


@pytest.mark.asyncio
async def test_exit_command_stops_run_and_closes_runtime(tmp_path: Path) -> None:
    from lantu.commands.handlers import register_all_commands

    runtime = FakeRuntime(tmp_path)
    register_all_commands(runtime.command_registry)
    app = make_app(runtime)

    assert await app.dispatcher.dispatch("/exit") is True
    assert app.running is False
    await app.run()

    assert runtime.closed is True


@pytest.mark.asyncio
async def test_command_dispatcher_prompts_persists_config_and_reports_errors(
    tmp_path: Path,
) -> None:
    from lantu.ui.inline.commands import InlineCommandDispatcher

    runtime = FakeRuntime(tmp_path)
    transcript = FakeTranscript()
    app = make_app(runtime, transcript=transcript)

    async def remember(ctx: Any) -> None:
        if ctx.args:
            ctx.config["remembered"] = ctx.args
        else:
            ctx.ui.add_system_message(ctx.config["remembered"])

    async def fail(ctx: Any) -> None:
        raise RuntimeError("boom")

    runtime.command_registry.register_sync(
        Command("need", "needs arg", CommandType.LOCAL, remember, arg_prompt="请输入参数")
    )
    runtime.command_registry.register_sync(
        Command("remember", "remember", CommandType.LOCAL, remember)
    )
    runtime.command_registry.register_sync(
        Command("fail", "fail", CommandType.LOCAL, fail)
    )
    dispatcher = InlineCommandDispatcher(app)

    await dispatcher.dispatch("/need")
    await dispatcher.dispatch("/remember value")
    await dispatcher.dispatch("/remember")
    await dispatcher.dispatch("/fail")

    assert "请输入参数" in transcript.system_messages
    assert "value" in transcript.system_messages
    assert transcript.error_messages[-1] == "命令执行失败: boom"


@pytest.mark.asyncio
async def test_command_dispatcher_does_not_swallow_cancellation(tmp_path: Path) -> None:
    from lantu.ui.inline.commands import InlineCommandDispatcher

    runtime = FakeRuntime(tmp_path)
    app = make_app(runtime)

    async def cancel(ctx: Any) -> None:
        raise asyncio.CancelledError

    runtime.command_registry.register_sync(
        Command("cancel", "cancel", CommandType.LOCAL, cancel)
    )

    with pytest.raises(asyncio.CancelledError):
        await InlineCommandDispatcher(app).dispatch("/cancel")


@pytest.mark.asyncio
async def test_run_ctrl_c_requires_confirmation_and_eof_exits(tmp_path: Path) -> None:
    runtime = FakeRuntime(tmp_path)
    transcript = FakeTranscript()
    app = make_app(
        runtime,
        prompt=FakePrompt(prompts=[KeyboardInterrupt(), KeyboardInterrupt()]),
        transcript=transcript,
    )

    await app.run()

    assert transcript.system_messages == ["再次按 Ctrl+C 退出"]
    assert runtime.closed is True


@pytest.mark.asyncio
async def test_successful_input_resets_ctrl_c_confirmation(tmp_path: Path) -> None:
    runtime = FakeRuntime(tmp_path)
    transcript = FakeTranscript()
    app = make_app(
        runtime,
        prompt=FakePrompt(
            prompts=[KeyboardInterrupt(), "", KeyboardInterrupt(), KeyboardInterrupt()]
        ),
        transcript=transcript,
    )

    await app.run()

    assert transcript.system_messages.count("再次按 Ctrl+C 退出") == 2


@pytest.mark.asyncio
async def test_run_prompt_persists_messages_tokens_and_mcp_once(tmp_path: Path) -> None:
    runtime = FakeRuntime(
        tmp_path,
        [
            [StreamText("一"), TurnComplete(1), LoopComplete(1)],
            [StreamText("二"), LoopComplete(1)],
        ],
    )
    runtime.mcp_instructions = "MCP rules"
    app = make_app(runtime)

    await app.run_prompt("first")
    await app.run_prompt("second")

    assert [message.role for message in runtime.session.messages] == [
        "user",
        "assistant",
        "user",
        "assistant",
    ]
    reminders = [m for m in runtime.conversation.history if "MCP rules" in m.content]
    assert len(reminders) == 1
    assert reminders[0].reminder_key == "mcp_instructions"
    assert reminders[0].reminder_hash
    assert runtime.session.meta.total_tokens == 18


@pytest.mark.asyncio
async def test_real_agent_session_persists_only_user_and_assistant_once(
    tmp_path: Path,
) -> None:
    client = ScriptedClient(
        [[TextDelta("完成"), StreamEnd("end_turn", input_tokens=9, output_tokens=3)]]
    )
    registry = ToolRegistry()
    runtime = FakeRuntime(tmp_path)
    runtime.agent = Agent(client, registry, "anthropic", work_dir=str(tmp_path))
    runtime.registry = registry
    runtime.conversation = ConversationManager()
    manager = SessionManager(str(tmp_path))
    session = manager.create()
    session.start_runtime("new")
    runtime.session_manager = manager
    runtime.session = session
    runtime.mcp_instructions = "MCP system reminder"
    app = make_app(runtime)

    await app.run_prompt("介绍项目")
    session_id = session.session_id
    session.close()
    resumed = manager.resume(session_id)

    assert resumed is not None
    assert [(message.role, message.content) for message in resumed.messages] == [
        ("user", "介绍项目"),
        ("assistant", "完成"),
    ]
    resumed.session.close()


@pytest.mark.asyncio
async def test_agent_error_before_first_event_does_not_persist_injected_history(
    tmp_path: Path,
) -> None:
    runtime = FakeRuntime(tmp_path)

    async def fail_before_event(conversation: ConversationManager):
        conversation.inject_environment("internal environment")
        raise LLMError("failed before event")
        yield

    runtime.agent.run = fail_before_event  # type: ignore[method-assign]
    app = make_app(runtime)

    await app.run_prompt("user input")

    assert [(message.role, message.content) for message in runtime.session.messages] == [
        ("user", "user input")
    ]


def test_persist_unseen_messages_compares_identity_when_ids_are_reused(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import lantu.ui.inline.app as inline_app_module

    runtime = FakeRuntime(tmp_path)
    app = make_app(runtime)
    old_message = Message(role="assistant", content="old")
    new_message = Message(role="assistant", content="new")
    runtime.conversation.history = [new_message]
    seen_messages = {7: old_message}
    monkeypatch.setattr(inline_app_module, "id", lambda _message: 7, raising=False)

    app.persist_unseen_messages(seen_messages)

    assert runtime.session.messages == [new_message]
    assert seen_messages[7] is new_message


@pytest.mark.asyncio
async def test_compact_keeps_seen_messages_strongly_referenced_until_turn_ends(
    tmp_path: Path,
) -> None:
    runtime = FakeRuntime(tmp_path)
    retained_during_compact: list[bool] = []

    async def compacting_run(conversation: ConversationManager):
        old_message = Message(role="assistant", content="old")
        old_reference = weakref.ref(old_message)
        conversation.history.append(old_message)
        yield StreamText("working")
        conversation.history.remove(old_message)
        del old_message
        gc.collect()
        retained_during_compact.append(old_reference() is not None)
        conversation.history[:] = [Message(role="assistant", content="summary")]
        yield CompactNotification(100, "compacted", None)
        yield LoopComplete(1)

    runtime.agent.run = compacting_run  # type: ignore[method-assign]
    app = make_app(runtime)

    await app.run_prompt("start")

    assert retained_during_compact == [True]


def test_session_update_total_tokens_is_saved_to_meta(tmp_path: Path) -> None:
    manager = SessionManager(str(tmp_path))
    session = manager.create()
    session_id = session.session_id

    session.update_total_tokens(321)
    session.close()

    persisted = next(meta for meta in manager.list() if meta.id == session_id)
    assert persisted.total_tokens == 321


@pytest.mark.asyncio
async def test_unmarked_agent_cancellation_propagates(tmp_path: Path) -> None:
    runtime = FakeRuntime(tmp_path)

    async def cancelled_run(conversation: ConversationManager):
        raise asyncio.CancelledError
        yield

    runtime.agent.run = cancelled_run  # type: ignore[method-assign]
    transcript = FakeTranscript()
    app = make_app(runtime, transcript=transcript)

    with pytest.raises(asyncio.CancelledError):
        await app.run_prompt("cancel me")

    assert "Operation cancelled" not in transcript.system_messages
    assert app.running is True


@pytest.mark.asyncio
async def test_unmarked_runtime_readiness_cancellation_propagates(tmp_path: Path) -> None:
    runtime = FakeRuntime(tmp_path)

    async def cancelled_wait() -> None:
        raise asyncio.CancelledError

    runtime.wait_until_ready = cancelled_wait  # type: ignore[method-assign]
    transcript = FakeTranscript()
    app = make_app(runtime, transcript=transcript)

    with pytest.raises(asyncio.CancelledError):
        await app.run_prompt("cancel before agent")

    assert "Operation cancelled" not in transcript.system_messages
    assert runtime.agent.run_count == 0


@pytest.mark.asyncio
async def test_compact_boundary_is_persisted_without_reappending_prefix(
    tmp_path: Path,
) -> None:
    runtime = FakeRuntime(tmp_path)
    boundary = SimpleNamespace(
        summary="摘要",
        keep=[Message(role="assistant", content="保留")],
    )
    app = make_app(runtime)

    app.persist_compact_boundary(CompactNotification(100, "已压缩", boundary))

    assert len(runtime.session.records) == 1
    assert runtime.session.records[0].content["summary"] == "摘要"


@pytest.mark.asyncio
async def test_handle_ask_user_supports_all_question_types(tmp_path: Path) -> None:
    runtime = FakeRuntime(tmp_path)
    prompt = FakePrompt(choices=["A"], texts=["detail"], many=[["X", "Y"]])
    app = make_app(runtime, prompt=prompt)
    future = asyncio.get_running_loop().create_future()
    event = AskUserEvent(
        [
            {"name": "single", "message": "选一个", "type": "select", "options": [{"label": "A"}]},
            {"name": "multi", "message": "选多个", "type": "checkbox", "options": ["X", "Y"]},
            {"name": "text", "message": "补充", "type": "text", "options": []},
        ],
        future,
    )

    await app.handle_ask_user(event)

    assert future.result() == {"single": "A", "multi": "X, Y", "text": "detail"}


@pytest.mark.asyncio
async def test_handle_ask_user_cancellation_returns_empty_answers(tmp_path: Path) -> None:
    runtime = FakeRuntime(tmp_path)
    app = make_app(runtime, prompt=FakePrompt(choices=[EOFError()]))
    future = asyncio.get_running_loop().create_future()

    await app.handle_ask_user(
        AskUserEvent(
            [{"name": "x", "message": "x", "type": "radio", "options": ["a"]}],
            future,
        )
    )

    assert future.result() == {}


@pytest.mark.asyncio
async def test_ask_user_tool_without_handler_keeps_pending_event_compatibility() -> None:
    tool = AskUserTool()
    params = AskUserParams.model_validate(
        {
            "questions": [
                {
                    "name": "choice",
                    "message": "选择",
                    "type": "radio",
                    "options": ["A"],
                }
            ]
        }
    )

    task = asyncio.create_task(tool.execute(params))
    await asyncio.sleep(0)
    assert tool._pending_event is not None
    tool._pending_event.future.set_result({"choice": "A"})

    result = await task

    assert result.output == "choice: A"
    assert tool._pending_event is None


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [RuntimeError("handler failed"), asyncio.CancelledError()])
async def test_ask_user_handler_failure_completes_future_and_cleans_pending(
    error: BaseException,
) -> None:
    tool = AskUserTool()
    captured: list[AskUserEvent] = []

    async def fail(event: AskUserEvent) -> None:
        captured.append(event)
        raise error

    tool.set_handler(fail)
    params = AskUserParams.model_validate(
        {"questions": [{"name": "x", "message": "x", "type": "text"}]}
    )

    with pytest.raises(type(error)):
        await tool.execute(params)

    assert captured[0].future.done()
    assert tool._pending_event is None


@pytest.mark.asyncio
async def test_ask_user_handler_timeout_cancels_handler_and_cleans_pending(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("lantu.tools.ask_user.ASK_USER_TIMEOUT_SECONDS", 0.01)
    tool = AskUserTool()
    cancelled = False
    captured: list[AskUserEvent] = []

    async def wait_forever(event: AskUserEvent) -> None:
        nonlocal cancelled
        captured.append(event)
        try:
            await asyncio.Event().wait()
        finally:
            cancelled = True

    tool.set_handler(wait_forever)
    params = AskUserParams.model_validate(
        {"questions": [{"name": "x", "message": "x", "type": "text"}]}
    )

    result = await tool.execute(params)

    assert result.is_error is True
    assert cancelled is True
    assert captured[0].future.done()
    assert tool._pending_event is None


@pytest.mark.asyncio
async def test_real_agent_ask_user_tool_completes_inside_agent_loop(tmp_path: Path) -> None:
    ask_tool = AskUserTool()
    registry = ToolRegistry()
    registry.register(ask_tool)
    registry.mark_discovered(ask_tool.name)
    client = ScriptedClient(
        [
            [
                TextDelta("需要确认"),
                ToolCallComplete(
                    "ask-1",
                    "AskUserQuestion",
                    {
                        "questions": [
                            {
                                "name": "choice",
                                "message": "选择",
                                "type": "radio",
                                "options": ["A", "B"],
                            }
                        ]
                    },
                ),
                StreamEnd("end_turn", input_tokens=10, output_tokens=5),
            ],
            [
                TextDelta("已收到"),
                StreamEnd("end_turn", input_tokens=12, output_tokens=4),
            ],
        ]
    )
    runtime = FakeRuntime(tmp_path)
    runtime.agent = Agent(client, registry, "anthropic", work_dir=str(tmp_path))
    runtime.registry = registry
    runtime.conversation = ConversationManager()
    transcript = FakeTranscript()
    app = make_app(
        runtime,
        prompt=FakePrompt(choices=["A"]),
        transcript=transcript,
    )

    await asyncio.wait_for(app.run_prompt("需要答案"), timeout=0.5)

    assert any(tool.output == "choice: A" for tool in transcript.tool_messages)
    assert transcript.assistant_messages[-1] == "已收到"
    assert runtime.agent._loop_count == 1
    assert ask_tool._pending_event is None


@pytest.mark.asyncio
async def test_ask_user_option_fallbacks_do_not_require_labels_or_choices(
    tmp_path: Path,
) -> None:
    runtime = FakeRuntime(tmp_path)
    prompt = FakePrompt(choices=["{'value': 'raw'}"], texts=["free text"])
    app = make_app(runtime, prompt=prompt)
    future = asyncio.get_running_loop().create_future()

    await app.handle_ask_user(
        AskUserEvent(
            [
                {
                    "name": "raw",
                    "message": "raw",
                    "type": "select",
                    "options": [{"value": "raw"}],
                },
                {
                    "name": "empty",
                    "message": "empty",
                    "type": "radio",
                    "options": [],
                },
            ],
            future,
        )
    )

    assert future.result() == {
        "raw": "{'value': 'raw'}",
        "empty": "free text",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("choice", "expected_mode"),
    [("yolo", PermissionMode.BYPASS), ("manual", PermissionMode.DEFAULT)],
)
async def test_plan_approval_queues_approved_plan(
    tmp_path: Path, choice: str, expected_mode: PermissionMode
) -> None:
    runtime = FakeRuntime(tmp_path)
    runtime.agent.permission_mode = PermissionMode.PLAN
    runtime.agent._plan_path.write_text("执行第一步", encoding="utf-8")
    app = make_app(runtime, prompt=FakePrompt(choices=[choice]))
    app._pre_plan_mode = PermissionMode.DEFAULT

    await app.handle_plan_approval()

    assert runtime.agent.permission_mode is expected_mode
    assert "Approved Plan:\n执行第一步" in app.pending_prompts[-1]
    assert app._has_exited_plan_mode is True


@pytest.mark.asyncio
async def test_plan_feedback_stays_in_plan_mode_and_queues_feedback(tmp_path: Path) -> None:
    runtime = FakeRuntime(tmp_path)
    runtime.agent.permission_mode = PermissionMode.PLAN
    app = make_app(runtime, prompt=FakePrompt(choices=["feedback"], texts=["补充测试"]))

    await app.handle_plan_approval()

    assert runtime.agent.permission_mode is PermissionMode.PLAN
    assert list(app.pending_prompts) == ["补充测试"]


@pytest.mark.asyncio
async def test_plan_path_oserror_returns_to_input_loop(tmp_path: Path) -> None:
    runtime = FakeRuntime(tmp_path)
    app = make_app(runtime, prompt=FakePrompt(choices=["manual"]))
    app.set_plan_mode(True)

    def fail_plan_path() -> Path:
        raise OSError("read only")

    runtime.agent._get_plan_path = fail_plan_path  # type: ignore[method-assign]

    await app.handle_plan_approval()

    assert runtime.agent.permission_mode is PermissionMode.DEFAULT
    assert len(app.pending_prompts) == 1


@pytest.mark.asyncio
async def test_plan_async_cancellation_propagates_without_approval(
    tmp_path: Path,
) -> None:
    runtime = FakeRuntime(tmp_path)
    app = make_app(runtime, prompt=FakePrompt(choices=[asyncio.CancelledError()]))
    app.set_plan_mode(True)

    with pytest.raises(asyncio.CancelledError):
        await app.handle_plan_approval()

    assert runtime.agent.permission_mode is PermissionMode.PLAN
    assert list(app.pending_prompts) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [KeyboardInterrupt(), EOFError()])
async def test_plan_terminal_cancellation_keeps_plan_mode(
    tmp_path: Path,
    error: BaseException,
) -> None:
    runtime = FakeRuntime(tmp_path)
    transcript = FakeTranscript()
    app = make_app(
        runtime,
        prompt=FakePrompt(choices=[error]),
        transcript=transcript,
    )
    app.set_plan_mode(True)

    await app.handle_plan_approval()

    assert runtime.agent.permission_mode is PermissionMode.PLAN
    assert list(app.pending_prompts) == []
    assert transcript.system_messages[-1] == "计划审批已取消"


@pytest.mark.asyncio
async def test_plan_feedback_async_cancellation_propagates(tmp_path: Path) -> None:
    runtime = FakeRuntime(tmp_path)
    app = make_app(
        runtime,
        prompt=FakePrompt(choices=["feedback"], texts=[asyncio.CancelledError()]),
    )
    app.set_plan_mode(True)

    with pytest.raises(asyncio.CancelledError):
        await app.handle_plan_approval()

    assert runtime.agent.permission_mode is PermissionMode.PLAN
    assert list(app.pending_prompts) == []


@pytest.mark.asyncio
async def test_loop_complete_awaits_plan_approval(tmp_path: Path) -> None:
    runtime = FakeRuntime(tmp_path, [[LoopComplete(1)]])
    app = make_app(runtime, prompt=FakePrompt(choices=["manual"]))
    app.set_plan_mode(True)

    await app.run_prompt("make plan")

    assert len(app.pending_prompts) == 1
    assert runtime.agent.permission_mode is PermissionMode.DEFAULT


@pytest.mark.asyncio
async def test_initial_plan_mode_manual_approval_returns_to_default(
    tmp_path: Path,
) -> None:
    runtime = FakeRuntime(tmp_path)
    runtime.agent.set_permission_mode(PermissionMode.PLAN)
    app = make_app(runtime, prompt=FakePrompt(choices=["manual"]))

    await app.handle_plan_approval()

    assert runtime.agent.permission_mode is PermissionMode.DEFAULT
    assert len(app.pending_prompts) == 1


@pytest.mark.asyncio
async def test_notifications_trigger_one_guarded_follow_up_turn(tmp_path: Path) -> None:
    task = SimpleNamespace(
        id="task-1",
        name="worker",
        status="completed",
        result="done",
        start_time=0.0,
        end_time=1.0,
        progress=SimpleNamespace(input_tokens=0, output_tokens=0),
    )
    runtime = FakeRuntime(
        tmp_path,
        [[LoopComplete(1)], [LoopComplete(1)]],
    )
    runtime.task_manager = FakeTaskManager([[task], []])
    app = make_app(runtime)

    await app.run_prompt("start")

    assert runtime.agent.run_count == 2
    assert any("后台任务完成" in message for message in app.transcript.system_messages)


@pytest.mark.asyncio
async def test_completed_task_interrupts_idle_prompt_and_notifies_immediately(
    tmp_path: Path,
) -> None:
    task = SimpleNamespace(
        id="task-idle",
        name="worker",
        status="completed",
        result="done",
        start_time=0.0,
        end_time=1.0,
        progress=SimpleNamespace(input_tokens=0, output_tokens=0),
    )
    ready = asyncio.Event()

    class NotificationTaskManager:
        delivered = False

        def has_completed(self) -> bool:
            return ready.is_set() and not self.delivered

        def poll_completed(self) -> list[Any]:
            if not self.has_completed():
                return []
            self.delivered = True
            return [task]

    class IdlePrompt(FakePrompt):
        def __init__(self) -> None:
            super().__init__()
            self.started = asyncio.Event()
            self.cancelled = False
            self.calls = 0

        async def prompt(self, status: str) -> str:
            self.calls += 1
            if self.calls > 1:
                raise EOFError
            self.started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                self.cancelled = True
                raise

    runtime = FakeRuntime(tmp_path, [[LoopComplete(1)]])
    runtime.task_manager = NotificationTaskManager()
    prompt = IdlePrompt()
    app = make_app(runtime, prompt=prompt)
    run_task = asyncio.create_task(app.run())
    await prompt.started.wait()

    ready.set()
    await asyncio.wait_for(run_task, timeout=1)

    assert prompt.cancelled
    assert runtime.agent.run_count == 1
    assert any(
        "task-idle" in message for message in app.transcript.system_messages
    )


@pytest.mark.asyncio
async def test_idle_prompt_syncs_completion_directory_after_worktree_switch(
    tmp_path: Path,
) -> None:
    switched = tmp_path / "worktree"
    switched.mkdir()

    class WorkDirPrompt(FakePrompt):
        def __init__(self) -> None:
            super().__init__()
            self.work_dirs: list[str] = []

        def set_work_dir(self, work_dir: str) -> None:
            self.work_dirs.append(work_dir)

    runtime = FakeRuntime(tmp_path)
    prompt = WorkDirPrompt()
    app = make_app(runtime, prompt=prompt)
    runtime.agent.work_dir = str(switched)

    await app.run()

    assert prompt.work_dirs == [str(switched)]


@pytest.mark.asyncio
async def test_notifications_drain_agent_mailbox_once(tmp_path: Path) -> None:
    runtime = FakeRuntime(tmp_path)
    runtime.team_manager = FakeTeamManager([[]])
    runtime.agent.notification_fn = runtime.team_manager.drain_lead_mailbox
    app = make_app(runtime)

    await app.process_task_notifications()

    assert runtime.team_manager.drain_count == 1


@pytest.mark.asyncio
async def test_mailbox_notification_triggers_follow_up_when_agent_does_not_drain(
    tmp_path: Path,
) -> None:
    runtime = FakeRuntime(tmp_path, [[LoopComplete(1)]])
    runtime.team_manager = FakeTeamManager([["mail"], []])
    app = make_app(runtime)

    await app.process_task_notifications()

    assert runtime.team_manager.drain_count == 1
    assert runtime.agent.run_count == 1
    assert any("mail" in message.content for message in runtime.conversation.history)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("include_task", "mailbox_notes", "expected_fragments"),
    [
        (True, [], ["<task-notification>"]),
        (False, ["mailbox context"], ["mailbox context"]),
        (True, ["mailbox context"], ["<task-notification>", "mailbox context"]),
    ],
)
async def test_notifications_are_persisted_once_with_follow_up_reply(
    tmp_path: Path,
    include_task: bool,
    mailbox_notes: list[str],
    expected_fragments: list[str],
) -> None:
    task = SimpleNamespace(
        id="task-persist",
        name="worker",
        status="completed",
        result="task context",
        start_time=0.0,
        end_time=1.0,
        progress=SimpleNamespace(input_tokens=0, output_tokens=0),
    )
    runtime = FakeRuntime(
        tmp_path,
        [[StreamText("notification reply"), LoopComplete(1)]],
    )
    runtime.task_manager = FakeTaskManager([([task] if include_task else [])])
    runtime.team_manager = FakeTeamManager([mailbox_notes])
    manager = SessionManager(str(tmp_path))
    session = manager.create()
    session.start_runtime("new")
    runtime.session_manager = manager
    runtime.session = session
    app = make_app(runtime)

    await app.process_task_notifications()
    session_id = session.session_id
    session.close()
    resumed = manager.resume(session_id)

    assert resumed is not None
    persisted_content = [message.content for message in resumed.messages]
    for fragment in expected_fragments:
        assert sum(fragment in content for content in persisted_content) == 1
    assert persisted_content[-1] == "notification reply"
    resumed.session.close()


@pytest.mark.asyncio
async def test_command_context_syncs_runtime_references_and_restores_messages(
    tmp_path: Path,
) -> None:
    runtime = FakeRuntime(tmp_path)
    transcript = FakeTranscript()
    app = make_app(runtime, transcript=transcript)
    new_session = FakeSession("session-2")
    new_conversation = ConversationManager()
    context = app.build_command_context("")

    context.config["set_session"](new_session)
    context.config["set_conversation"](new_conversation)
    await context.config["render_restored"](
        [
            Message(role="user", content="question"),
            Message(role="assistant", content="answer"),
            Message(role="user", content="", tool_results=[]),
        ]
    )

    assert runtime.session is new_session
    assert runtime.agent.session_id == "session-2"
    assert runtime.conversation is new_conversation
    assert transcript.user_messages == ["question"]
    assert transcript.assistant_messages == ["answer"]


@pytest.mark.asyncio
async def test_set_new_session_clears_agent_and_event_token_state(tmp_path: Path) -> None:
    runtime = FakeRuntime(tmp_path)
    app = make_app(runtime)
    runtime.agent.total_input_tokens = 80
    runtime.agent.total_output_tokens = 20
    runtime.agent._loop_count = 4
    recall_task = asyncio.create_task(asyncio.sleep(60))
    runtime.agent.memory_recall_task = recall_task
    runtime.agent._memory_recall_consumed = False
    app.events.state.input_tokens = 80
    app.events.state.output_tokens = 20
    new_session = FakeSession("new-session")

    app.build_command_context("").config["set_session"](new_session)
    await asyncio.sleep(0)

    assert runtime.agent.total_input_tokens == 0
    assert runtime.agent.total_output_tokens == 0
    assert runtime.agent._loop_count == 0
    assert runtime.agent.memory_recall_task is None
    assert runtime.agent._memory_recall_consumed is True
    assert recall_task.cancelled() is True
    assert app.events.state.input_tokens == 0
    assert app.events.state.output_tokens == 0


@pytest.mark.asyncio
async def test_resumed_session_tokens_become_next_turn_baseline(tmp_path: Path) -> None:
    runtime = FakeRuntime(tmp_path)
    app = make_app(runtime)
    resumed_session = FakeSession("resumed-session")
    resumed_session.start_runtime("resume")
    resumed_session.meta.total_tokens = 120
    app.build_command_context("").config["set_session"](resumed_session)

    async def one_more_turn(conversation: ConversationManager):
        runtime.agent.total_input_tokens += 8
        runtime.agent.total_output_tokens += 2
        yield LoopComplete(1)

    runtime.agent.run = one_more_turn  # type: ignore[method-assign]

    await app.run_prompt("")

    assert runtime.agent.total_input_tokens == 128
    assert runtime.agent.total_output_tokens == 2
    assert resumed_session.meta.total_tokens == 130


@pytest.mark.asyncio
async def test_set_conversation_reinjects_mcp_on_next_prompt(tmp_path: Path) -> None:
    runtime = FakeRuntime(
        tmp_path,
        [[LoopComplete(1)], [LoopComplete(1)]],
    )
    runtime.mcp_instructions = "MCP rules"
    app = make_app(runtime)

    await app.run_prompt("first")
    new_conversation = ConversationManager()
    app.build_command_context("").config["set_conversation"](new_conversation)
    await app.run_prompt("second")

    reminders = [message for message in new_conversation.history if "MCP rules" in message.content]
    assert len(reminders) == 1


@pytest.mark.asyncio
async def test_startup_messages_added_while_waiting_are_drained_once(
    tmp_path: Path,
) -> None:
    runtime = FakeRuntime(tmp_path, [[LoopComplete(1)]])
    runtime.startup_messages = ["initial warning"]
    added = False

    async def wait_with_warning() -> None:
        nonlocal added
        if not added:
            runtime.startup_messages.append("late warning")
            added = True

    runtime.wait_until_ready = wait_with_warning  # type: ignore[method-assign]
    transcript = FakeTranscript()
    app = make_app(
        runtime,
        prompt=FakePrompt(prompts=["go", EOFError()]),
        transcript=transcript,
    )

    await app.run()

    assert transcript.system_messages.count("initial warning") == 1
    assert transcript.system_messages.count("late warning") == 1
    assert runtime.startup_messages == []


def test_ui_controller_methods_queue_prompts_modes_and_exit(tmp_path: Path) -> None:
    runtime = FakeRuntime(tmp_path)
    transcript = FakeTranscript()
    app = make_app(runtime, transcript=transcript)

    app.send_user_message("queued")
    app.set_plan_mode(True)
    assert list(app.pending_prompts) == ["queued"]
    assert runtime.agent.permission_mode is PermissionMode.PLAN
    assert app.get_token_count() == (11, 7)
    app.set_plan_mode(False)
    assert runtime.agent.permission_mode is PermissionMode.DEFAULT
    app.request_exit()
    assert app.running is False


def test_show_last_tool_details_is_synchronous_and_has_empty_state(tmp_path: Path) -> None:
    transcript = FakeTranscript()
    app = make_app(FakeRuntime(tmp_path), transcript=transcript)

    result = app.show_last_tool_details()

    assert inspect.isawaitable(result) is False
    assert transcript.system_messages[-1] == "暂无工具详情"


def test_show_last_tool_details_synchronously_outputs_tool(tmp_path: Path) -> None:
    transcript = FakeTranscript()
    live = FakeLive()
    app = make_app(FakeRuntime(tmp_path), transcript=transcript, live=live)
    app.events.last_tool = SimpleNamespace(name="Bash", output="done")

    result = app.show_last_tool_details()

    assert inspect.isawaitable(result) is False
    assert transcript.details == [app.events.last_tool]
    assert live.stop_count == 1


@pytest.mark.asyncio
async def test_close_cancellation_stops_live_and_propagates(tmp_path: Path) -> None:
    runtime = FakeRuntime(tmp_path)

    async def cancelled_close() -> None:
        raise asyncio.CancelledError

    runtime.close = cancelled_close  # type: ignore[method-assign]
    live = FakeLive()
    app = make_app(runtime, prompt=FakePrompt(prompts=[EOFError()]), live=live)

    with pytest.raises(asyncio.CancelledError):
        await app.run()

    assert live.stop_count >= 1


@pytest.mark.asyncio
async def test_run_closes_runtime_when_live_stop_fails(tmp_path: Path) -> None:
    runtime = FakeRuntime(tmp_path)

    class FailingLive(FakeLive):
        def stop(self) -> None:
            super().stop()
            raise RuntimeError("live stop failed")

    app = make_app(
        runtime,
        prompt=FakePrompt(prompts=[EOFError()]),
        live=FailingLive(),
    )

    with pytest.raises(RuntimeError, match="live stop failed"):
        await app.run()

    assert runtime.closed is True


@pytest.mark.asyncio
async def test_run_closes_runtime_when_header_fails(tmp_path: Path) -> None:
    runtime = FakeRuntime(tmp_path)
    transcript = FakeTranscript()

    def broken_header(model: str, mode: str, work_dir: str) -> None:
        raise BrokenPipeError("closed output")

    transcript.header = broken_header  # type: ignore[method-assign]
    app = make_app(runtime, transcript=transcript)

    with pytest.raises(BrokenPipeError, match="closed output"):
        await app.run()

    assert runtime.closed is True
