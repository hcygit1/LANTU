from __future__ import annotations

import asyncio
from collections import deque
from contextlib import suppress
import signal
from pathlib import Path
from typing import Any

from rich.console import Console

from lantu.agent import (
    CompactNotification,
    LoopComplete,
    PermissionRequest,
    PermissionResponse,
    ToolResultEvent,
    TurnComplete,
)
from lantu.agents.notification import inject_task_notifications
from lantu.client import LLMError
from lantu.commands import CommandContext
from lantu.conversation import ConversationManager, Message
from lantu.filehistory import FileHistory
from lantu.memory.session import make_compact_boundary
from lantu.permissions import PermissionMode
from lantu.prompts import build_plan_mode_exit_reminder
from lantu.runtime import InteractiveRuntime
from lantu.tools.ask_user import AskUserEvent, AskUserTool
from lantu.ui.inline.commands import InlineCommandDispatcher
from lantu.ui.inline.components.interaction import (
    render_permission_request,
    render_plan_request,
    render_question,
)
from lantu.ui.inline.event_handler import InlineEventHandler
from lantu.ui.inline.live import LiveRenderer
from lantu.ui.inline.session import InlinePromptSession
from lantu.ui.inline.transcript import TranscriptRenderer
from lantu.ui.shared.formatting import format_tokens
from lantu.ui.shared.references import expand_at_refs


PERMISSION_CHOICES = ["allow", "always", "deny"]
PLAN_CHOICES = ["yolo", "manual", "feedback"]
MAX_PLAN_CONTENT = 64 * 1024


class InlineApp:
    def __init__(
        self,
        runtime: InteractiveRuntime,
        console: Console | None = None,
        prompt: Any | None = None,
        transcript: Any | None = None,
        live: Any | None = None,
    ) -> None:
        self.runtime = runtime
        self.console = console or Console()
        self.agent = runtime.agent
        self.conversation = runtime.conversation
        self.command_registry = runtime.command_registry
        self.transcript = transcript or TranscriptRenderer(self.console)
        self.live = live or LiveRenderer(self.console)
        history_path = Path(self.agent.work_dir) / ".lantu" / "history"
        self.prompt = prompt or InlinePromptSession(
            self.command_registry,
            self.agent.work_dir,
            str(history_path),
            on_toggle_details=self.show_last_tool_details,
        )
        self.events = InlineEventHandler(
            self.live,
            self.transcript,
            permission_handler=self.handle_permission,
        )
        self.dispatcher = InlineCommandDispatcher(self)

        self.running = True
        self.confirm_exit = False
        self.pending_prompts: deque[str] = deque()
        self._pre_plan_mode = self.agent.permission_mode
        self._mcp_injected = False
        self._agent_task: asyncio.Task[None] | None = None
        self._processing_notifications = False
        self._has_exited_plan_mode = False
        self._command_config: dict[str, Any] = {}

    @property
    def model(self) -> str:
        return self.runtime.provider.model

    @property
    def status_text(self) -> str:
        mode = self.agent.permission_mode.value
        tokens = format_tokens(
            self.runtime.conversation.current_tokens(),
            self.agent.context_window,
        )
        return f"{mode} · {self.model} · {tokens}"

    async def run(self) -> None:
        self.transcript.header(
            self.model,
            self.agent.permission_mode.value,
            self.agent.work_dir,
        )
        startup_messages = list(self.runtime.startup_messages)
        self.runtime.startup_messages.clear()
        for message in startup_messages:
            self.transcript.system_message(message)

        try:
            while self.running:
                try:
                    if self.pending_prompts:
                        text = self.pending_prompts.popleft()
                    else:
                        text = await self.prompt.prompt(self.status_text)
                    self.confirm_exit = False
                except KeyboardInterrupt:
                    if self.confirm_exit:
                        self.running = False
                    else:
                        self.confirm_exit = True
                        self.transcript.system_message("再次按 Ctrl+C 退出")
                    continue
                except EOFError:
                    self.running = False
                    continue

                text = text.strip()
                if not text:
                    continue
                if await self.dispatcher.dispatch(text):
                    continue
                await self.run_prompt_interruptible(text)
        finally:
            self.live.stop()
            await self.runtime.close()

    async def run_prompt_interruptible(self, text: str) -> None:
        loop = asyncio.get_running_loop()
        previous_handler = signal.getsignal(signal.SIGINT)
        signal_installed = False
        task: asyncio.Task[None] | None = None
        try:
            try:
                loop.add_signal_handler(signal.SIGINT, self.interrupt_active_turn)
                signal_installed = True
            except (NotImplementedError, RuntimeError, ValueError):
                pass

            task = asyncio.create_task(self.run_prompt(text))
            self._agent_task = task
            await task
        finally:
            if self._agent_task is task:
                self._agent_task = None
            if signal_installed:
                loop.remove_signal_handler(signal.SIGINT)
                with suppress(OSError, RuntimeError, ValueError):
                    signal.signal(signal.SIGINT, previous_handler)

    def interrupt_active_turn(self) -> None:
        task = self._agent_task
        if task is not None and not task.done():
            task.cancel()

    async def run_prompt(self, text: str, is_notification: bool = False) -> None:
        self.confirm_exit = False
        prefetch_task: asyncio.Task[str] | None = None
        history_cursor: int | None = None
        finished = False
        try:
            self.runtime.refresh_skills_if_needed()
            await self.runtime.wait_until_ready()

            if text and "@" in text:
                text = expand_at_refs(text, self.agent.work_dir)

            if text:
                prefetch_task = asyncio.create_task(
                    self.runtime.prefetch_relevant_memories(text)
                )
                self.transcript.user_message(text)
                self.runtime.conversation.add_user_message(text)
                self.runtime.session.append(Message(role="user", content=text))

            if self.runtime.mcp_instructions and not self._mcp_injected:
                self.runtime.conversation.add_system_reminder(
                    self.runtime.mcp_instructions
                )
                self._mcp_injected = True

            if prefetch_task is not None:
                self.agent.memory_recall_task = prefetch_task
                self.agent._memory_recall_consumed = False

            history_cursor = len(self.runtime.conversation.history)
            async for event in self.agent.run(self.runtime.conversation):
                if isinstance(event, CompactNotification):
                    self.persist_compact_boundary(event)
                    history_cursor = len(self.runtime.conversation.history)

                await self.events.handle(event)

                if isinstance(event, ToolResultEvent):
                    ask_tool = self.runtime.registry.get("AskUserQuestion")
                    if (
                        isinstance(ask_tool, AskUserTool)
                        and ask_tool._pending_event is not None
                    ):
                        await self.handle_ask_user(ask_tool._pending_event)
                elif isinstance(event, TurnComplete):
                    history_cursor = self.persist_history_from(history_cursor)
                elif isinstance(event, LoopComplete):
                    finished = True
                    history_cursor = self.persist_history_from(history_cursor)
                    if self.agent.plan_mode:
                        await self.handle_plan_approval()
        except asyncio.CancelledError:
            if not finished:
                self.events.finish()
                finished = True
            self.transcript.system_message("Operation cancelled")
        except LLMError as exc:
            if not finished:
                self.events.finish()
                finished = True
            self.transcript.error_message(str(exc))
        finally:
            if not finished:
                self.events.finish()
            if history_cursor is not None:
                self.persist_history_from(history_cursor)
            self.runtime.session.meta.total_tokens = (
                self.agent.total_input_tokens + self.agent.total_output_tokens
            )
            await self._cleanup_memory_prefetch(prefetch_task)
            if not is_notification:
                await self.process_task_notifications()

    async def _cleanup_memory_prefetch(
        self,
        prefetch_task: asyncio.Task[str] | None,
    ) -> None:
        if prefetch_task is None:
            return
        if not self.agent._memory_recall_consumed and not prefetch_task.done():
            prefetch_task.cancel()
        with suppress(asyncio.CancelledError, Exception):
            await prefetch_task
        if self.agent.memory_recall_task is prefetch_task:
            self.agent.memory_recall_task = None

    def persist_history_from(self, cursor: int) -> int:
        history = self.runtime.conversation.history
        for message in history[cursor:]:
            self.runtime.session.append(message)
        return len(history)

    def persist_compact_boundary(self, notification: CompactNotification) -> None:
        boundary = notification.boundary
        if boundary is None:
            return
        self.runtime.session.append_record(
            make_compact_boundary(boundary.summary, boundary.keep)
        )

    async def process_task_notifications(self) -> None:
        if self._processing_notifications:
            return

        self._processing_notifications = True
        try:
            completed = self.runtime.task_manager.poll_completed()
            if completed:
                inject_task_notifications(self.runtime.conversation, completed)
                for task in completed:
                    marker = "✓" if task.status == "completed" else "✗"
                    self.transcript.system_message(
                        f"{marker} 后台任务完成: [{task.id}] "
                        f"{task.name} — {task.status}"
                    )

            notes: list[str] = []
            drain_mailbox = self.runtime.team_manager.drain_lead_mailbox
            notes = drain_mailbox()
            for note in notes:
                self.runtime.conversation.add_system_reminder(note)

            if completed or notes:
                await self.run_prompt("", is_notification=True)
        finally:
            self._processing_notifications = False

    async def handle_permission(self, request: PermissionRequest) -> None:
        self.live.stop()
        self.transcript.commit(
            render_permission_request(request.tool_name, request.description),
            blank_after=False,
        )
        response = PermissionResponse.DENY
        try:
            choice = await self.prompt.choose("选择", PERMISSION_CHOICES)
            response = {
                "allow": PermissionResponse.ALLOW,
                "always": PermissionResponse.ALLOW_ALWAYS,
                "deny": PermissionResponse.DENY,
            }.get(choice, PermissionResponse.DENY)
        except (KeyboardInterrupt, EOFError):
            pass
        finally:
            if not request.future.done():
                request.future.set_result(response)

    async def handle_ask_user(self, event: AskUserEvent) -> None:
        self.live.stop()
        answers: dict[str, str] = {}
        try:
            for question in event.questions:
                name = str(question.get("name", ""))
                message = str(question.get("message", name))
                question_type = str(question.get("type", "text"))
                options = [
                    str(option.get("label", ""))
                    if isinstance(option, dict)
                    else str(option)
                    for option in question.get("options", [])
                ]
                self.transcript.commit(
                    render_question(name, message, options),
                    blank_after=False,
                )
                if question_type == "checkbox":
                    selected = await self.prompt.choose_many(message, options)
                    answers[name] = ", ".join(selected)
                elif question_type in {"radio", "select"}:
                    answers[name] = await self.prompt.choose(message, options)
                else:
                    answers[name] = await self.prompt.ask_text(message)
        except (KeyboardInterrupt, EOFError):
            answers = {}
        finally:
            if not event.future.done():
                event.future.set_result(answers)

    async def handle_plan_approval(self) -> None:
        self.live.stop()
        try:
            plan_path = Path(self.agent._get_plan_path())
            plan_content, plan_exists = self._read_plan(plan_path)
        except OSError:
            plan_path = Path(self.agent.work_dir) / ".lantu" / "plans" / "plan.md"
            plan_content, plan_exists = "", False
        self.transcript.commit(
            render_plan_request(str(plan_path), plan_content),
            blank_after=False,
        )

        try:
            choice = await self.prompt.choose("计划", PLAN_CHOICES)
        except (KeyboardInterrupt, EOFError):
            choice = "manual"

        if choice == "feedback":
            try:
                feedback = await self.prompt.ask_text("修改意见")
            except (KeyboardInterrupt, EOFError):
                feedback = ""
            if feedback:
                self.pending_prompts.append(feedback)
            return

        if choice == "yolo":
            self.agent.set_permission_mode(PermissionMode.BYPASS)
        else:
            self.agent.set_permission_mode(self._pre_plan_mode)

        reminder = build_plan_mode_exit_reminder(str(plan_path), plan_exists)
        approved = reminder + "\n\nUser has approved your plan. You can now start coding."
        if plan_content:
            approved += "\n\nApproved Plan:\n" + plan_content
        self.pending_prompts.append(approved)
        self._has_exited_plan_mode = True
        self.refresh_status()

    @staticmethod
    def _read_plan(plan_path: Path) -> tuple[str, bool]:
        try:
            with plan_path.open("r", encoding="utf-8") as plan_file:
                content = plan_file.read(MAX_PLAN_CONTENT + 1)
        except OSError:
            return "", False
        if len(content) > MAX_PLAN_CONTENT:
            content = content[:MAX_PLAN_CONTENT] + "\n... (truncated)"
        return content, True

    def build_command_context(self, args: str) -> CommandContext:
        self.agent = self.runtime.agent
        self.conversation = self.runtime.conversation
        self.command_registry = self.runtime.command_registry
        self._command_config.update(
            {
                "registry": self.command_registry,
                "set_session": self._set_session,
                "set_conversation": self._set_conversation,
                "clear_chat": self.transcript.clear_boundary,
                "render_restored": self._render_restored,
                "skill_loader": self.runtime.skill_loader,
                "skill_executor": self.runtime.skill_executor,
                "request_exit": self.request_exit,
            }
        )
        return CommandContext(
            args=args,
            agent=self.agent,
            conversation=self.conversation,
            session=self.runtime.session,
            session_manager=self.runtime.session_manager,
            memory_manager=self.runtime.memory_manager,
            ui=self,
            config=self._command_config,
        )

    def _set_session(self, session: Any) -> None:
        self.runtime.session = session
        self.agent.session_id = session.session_id
        file_history = FileHistory(self.agent.work_dir, session.session_id)
        self.runtime.file_history = file_history
        self.agent.file_history = file_history
        for tool in self.runtime.registry.list_tools():
            if hasattr(tool, "file_history"):
                tool.file_history = file_history

    def _set_conversation(self, conversation: ConversationManager) -> None:
        self.runtime.conversation = conversation
        self.conversation = conversation

    async def _render_restored(self, messages: list[Message]) -> None:
        for message in messages:
            if message.tool_results or not message.content:
                continue
            if message.role == "user":
                self.transcript.user_message(message.content)
            elif message.role == "assistant":
                self.transcript.assistant_message(message.content)

    def add_system_message(self, text: str) -> None:
        self.transcript.system_message(text)

    def send_user_message(self, text: str) -> None:
        if text.strip():
            self.pending_prompts.append(text)

    def set_plan_mode(self, enabled: bool) -> None:
        if enabled:
            if not self.agent.plan_mode:
                self._pre_plan_mode = self.agent.permission_mode
            self.agent.set_permission_mode(PermissionMode.PLAN)
        else:
            self.agent.set_permission_mode(self._pre_plan_mode)
        self.refresh_status()

    def get_token_count(self) -> tuple[int, int]:
        return self.agent.total_input_tokens, self.agent.total_output_tokens

    def refresh_status(self) -> None:
        self.events.state.status_text = self.status_text

    def request_exit(self) -> None:
        self.running = False

    async def show_last_tool_details(self) -> None:
        if self.events.last_tool is None:
            self.transcript.system_message("暂无工具详情")
            return
        self.live.stop()
        self.transcript.tool_details(self.events.last_tool)

    async def show_command_list(self) -> None:
        lines = ["可用命令："]
        for command in self.command_registry.list_commands():
            names = [f"/{command.name}", *(f"/{alias}" for alias in command.aliases)]
            lines.append(f"  {', '.join(names):<24} {command.description}")
        self.transcript.system_message("\n".join(lines))
