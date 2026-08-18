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


PERMISSION_CHOICES = ["1", "2", "3", "allow", "always", "deny"]
PLAN_CHOICES = ["yolo", "manual", "feedback"]
MAX_PLAN_CONTENT = 64 * 1024
NOTIFICATION_POLL_INTERVAL = 0.1


class _PromptKeyboardInterrupt(Exception):
    pass


class InlineApp:
    def __init__(
        self,
        runtime: InteractiveRuntime,
        console: Console | None = None,
        prompt: Any | None = None,
        transcript: Any | None = None,
        live: Any | None = None,
        show_thinking: bool = False,
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
            show_thinking=show_thinking,
        )
        ask_user_tool = self.runtime.registry.get("AskUserQuestion")
        if isinstance(ask_user_tool, AskUserTool):
            ask_user_tool.set_handler(self.handle_ask_user)
        self.dispatcher = InlineCommandDispatcher(self)

        self.running = True
        self.confirm_exit = False
        self.pending_prompts: deque[str] = deque()
        self._pre_plan_mode = (
            PermissionMode.DEFAULT
            if self.agent.permission_mode is PermissionMode.PLAN
            else self.agent.permission_mode
        )
        self._agent_task: asyncio.Task[None] | None = None
        self._turn_cancel_requested = False
        self._processing_notifications = False
        self._pending_session_messages: list[Message] = []
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
        try:
            self.transcript.header(
                self.model,
                self.agent.permission_mode.value,
                self.agent.work_dir,
            )
            self._drain_startup_messages()

            while self.running:
                try:
                    if self.pending_prompts:
                        text = self.pending_prompts.popleft()
                    else:
                        text = await self._prompt_or_process_notifications()
                        if text is None:
                            continue
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
            try:
                self.live.stop()
            finally:
                await self.runtime.close()

    async def _prompt_or_process_notifications(self) -> str | None:
        set_work_dir = getattr(self.prompt, "set_work_dir", None)
        if callable(set_work_dir):
            set_work_dir(self.agent.work_dir)

        async def read_prompt() -> str:
            try:
                return await self.prompt.prompt(self.status_text)
            except KeyboardInterrupt as exc:
                raise _PromptKeyboardInterrupt from exc

        prompt_task = asyncio.create_task(read_prompt())
        notification_task = asyncio.create_task(
            self._wait_for_pending_notifications()
        )
        try:
            done, _ = await asyncio.wait(
                {prompt_task, notification_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if prompt_task in done:
                notification_task.cancel()
                with suppress(asyncio.CancelledError):
                    await notification_task
                try:
                    return await prompt_task
                except _PromptKeyboardInterrupt:
                    raise KeyboardInterrupt from None

            prompt_task.cancel()
            with suppress(asyncio.CancelledError):
                await prompt_task
            await self.process_task_notifications()
            return None
        finally:
            for task in (prompt_task, notification_task):
                if not task.done():
                    task.cancel()

    async def _wait_for_pending_notifications(self) -> None:
        while self.running:
            task_manager = self.runtime.task_manager
            has_tasks = getattr(task_manager, "has_completed", None)
            if callable(has_tasks) and has_tasks():
                return

            team_manager = self.runtime.team_manager
            has_team_notes = getattr(
                team_manager, "has_lead_notifications", None
            )
            if callable(has_team_notes) and has_team_notes():
                return
            await asyncio.sleep(NOTIFICATION_POLL_INTERVAL)

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
            if task is not None and task.done():
                self._turn_cancel_requested = False
            if signal_installed:
                loop.remove_signal_handler(signal.SIGINT)
                with suppress(OSError, RuntimeError, ValueError):
                    signal.signal(signal.SIGINT, previous_handler)

    def interrupt_active_turn(self) -> None:
        task = self._agent_task
        if task is not None and not task.done():
            self._turn_cancel_requested = True
            if not task.cancel():
                self._turn_cancel_requested = False

    async def run_prompt(self, text: str, is_notification: bool = False) -> None:
        self.confirm_exit = False
        prefetch_task: asyncio.Task[str] | None = None
        seen_messages: dict[int, Message] = {}
        agent_started = False
        finished = False
        external_cancel = False
        turn_open = False
        interruption_reason = "agent_error"
        try:
            self.runtime.refresh_skills_if_needed()
            await self.runtime.wait_until_ready()
            self._drain_startup_messages()
            if text and not is_notification:
                lock_mode = getattr(self.agent, "lock_tool_loading_mode", None)
                if callable(lock_mode):
                    lock_mode()
            self.runtime.session.start_turn(
                "notification" if is_notification else "user"
            )
            turn_open = True

            if self._pending_session_messages:
                for message in self._pending_session_messages:
                    self.runtime.session.append(message)
                self._pending_session_messages.clear()

            if text and "@" in text:
                text = expand_at_refs(text, self.agent.work_dir)

            if text:
                prefetch_task = asyncio.create_task(
                    self.runtime.prefetch_relevant_memories(text)
                )
                self.transcript.user_message(text)
                self.runtime.conversation.add_user_message(text)
                self.runtime.session.append(Message(role="user", content=text))

            if self.runtime.mcp_instructions:
                self.runtime.conversation.add_system_reminder(
                    self.runtime.mcp_instructions,
                    reminder_key="mcp_instructions",
                )

            if prefetch_task is not None:
                self.agent.memory_recall_task = prefetch_task
                self.agent._memory_recall_consumed = False

            self.events.start_waiting()
            async for event in self.agent.run(self.runtime.conversation):
                if not agent_started:
                    for message in self.runtime.conversation.history:
                        seen_messages[id(message)] = message
                    agent_started = True

                if isinstance(event, CompactNotification):
                    self.persist_compact_boundary(event)
                    for message in self.runtime.conversation.history:
                        seen_messages[id(message)] = message

                await self.events.handle(event)

                if isinstance(event, ToolResultEvent):
                    ask_tool = self.runtime.registry.get("AskUserQuestion")
                    if (
                        isinstance(ask_tool, AskUserTool)
                        and ask_tool._pending_event is not None
                    ):
                        await self.handle_ask_user(ask_tool._pending_event)
                elif isinstance(event, TurnComplete):
                    self.persist_unseen_messages(seen_messages)
                elif isinstance(event, LoopComplete):
                    finished = True
                    self.persist_unseen_messages(seen_messages)
                    self.runtime.session.complete_turn(event.total_turns)
                    turn_open = False
                    if self.agent.plan_mode:
                        await self.handle_plan_approval()
        except asyncio.CancelledError:
            interruption_reason = (
                "user_cancelled" if self._turn_cancel_requested else "runtime_cancelled"
            )
            if not finished:
                self.events.finish()
                finished = True
            if self._turn_cancel_requested:
                self._turn_cancel_requested = False
                current_task = asyncio.current_task()
                if current_task is not None and current_task.cancelling() > 0:
                    current_task.uncancel()
                self.transcript.system_message("Operation cancelled")
            else:
                external_cancel = True
                raise
        except LLMError as exc:
            interruption_reason = "model_error"
            if not finished:
                self.events.finish()
                finished = True
            self.transcript.error_message(str(exc))
        finally:
            if not finished:
                self.events.finish()
            if agent_started:
                self.persist_unseen_messages(seen_messages)
            if turn_open:
                self.runtime.session.interrupt_turn(interruption_reason)
            self.runtime.session.update_total_tokens(
                self.agent.total_input_tokens + self.agent.total_output_tokens
            )
            await self._cleanup_memory_prefetch(prefetch_task)
            if not is_notification and not external_cancel:
                await self.process_task_notifications()

    async def _cleanup_memory_prefetch(
        self,
        prefetch_task: asyncio.Task[str] | None,
    ) -> None:
        if prefetch_task is None:
            return
        current_task = asyncio.current_task()
        caller_cancelled = (
            current_task is not None and current_task.cancelling() > 0
        )
        cancelled_by_cleanup = False
        try:
            if not prefetch_task.done() and (
                caller_cancelled or not self.agent._memory_recall_consumed
            ):
                cancelled_by_cleanup = prefetch_task.cancel()

            try:
                await asyncio.shield(prefetch_task)
            except asyncio.CancelledError:
                if current_task is not None and current_task.cancelling() > 0:
                    if not prefetch_task.done():
                        prefetch_task.cancel()
                    with suppress(asyncio.CancelledError, Exception):
                        await prefetch_task
                    raise
                if not cancelled_by_cleanup and not prefetch_task.cancelled():
                    raise
            except Exception:
                pass
        finally:
            if self.agent.memory_recall_task is prefetch_task:
                self.agent.memory_recall_task = None

    def persist_unseen_messages(self, seen_messages: dict[int, Message]) -> None:
        for message in self.runtime.conversation.history:
            identity = id(message)
            if seen_messages.get(identity) is message:
                continue
            self.runtime.session.append(message)
            seen_messages[identity] = message

    def persist_compact_boundary(self, notification: CompactNotification) -> None:
        boundary = notification.boundary
        if boundary is None:
            return
        self.runtime.session.context_compacted(boundary.summary, boundary.keep)

    async def process_task_notifications(self) -> None:
        if self._processing_notifications:
            return

        self._processing_notifications = True
        try:
            history_boundary = len(self.runtime.conversation.history)
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
                self._pending_session_messages.extend(
                    self.runtime.conversation.history[history_boundary:]
                )
                await self.run_prompt("", is_notification=True)
        finally:
            self._processing_notifications = False

    async def handle_permission(self, request: PermissionRequest) -> None:
        self.live.stop()
        self.transcript.commit(
            render_permission_request(request.tool_name, request.description),
            blank_after=False,
        )
        selection = "deny"
        response = PermissionResponse.DENY
        try:
            choice = await self.prompt.choose("请输入 1、2 或 3", PERMISSION_CHOICES)
            responses = {
                "1": ("allow", PermissionResponse.ALLOW),
                "2": ("always", PermissionResponse.ALLOW_ALWAYS),
                "3": ("deny", PermissionResponse.DENY),
                "allow": ("allow", PermissionResponse.ALLOW),
                "always": ("always", PermissionResponse.ALLOW_ALWAYS),
                "deny": ("deny", PermissionResponse.DENY),
            }
            if choice in responses:
                selection, response = responses[choice]
        except (KeyboardInterrupt, EOFError):
            pass
        finally:
            try:
                self.transcript.system_message(f"权限选择: {selection}")
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
                    str(option["label"])
                    if isinstance(option, dict) and "label" in option
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
                elif question_type in {"radio", "select"} and options:
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
        except asyncio.CancelledError:
            raise
        except (KeyboardInterrupt, EOFError):
            self.transcript.system_message("计划审批已取消")
            return

        if choice == "feedback":
            try:
                feedback = await self.prompt.ask_text("修改意见")
            except asyncio.CancelledError:
                raise
            except (KeyboardInterrupt, EOFError):
                self.transcript.system_message("计划审批已取消")
                return
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
        recall_task = self.agent.memory_recall_task
        if recall_task is not None and not recall_task.done():
            recall_task.cancel()
        baseline_tokens = session.meta.total_tokens
        self.agent.total_input_tokens = baseline_tokens
        self.agent.total_output_tokens = 0
        self.agent._loop_count = 0
        self.agent.memory_recall_task = None
        self.agent._memory_recall_consumed = True
        self.events.state.input_tokens = baseline_tokens
        self.events.state.output_tokens = 0
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

    def show_last_tool_details(self) -> None:
        if self.events.last_tool is None:
            self.transcript.system_message("暂无工具详情")
            return
        self.live.stop()
        self.transcript.tool_details(self.events.last_tool)

    def _drain_startup_messages(self) -> None:
        startup_messages = list(self.runtime.startup_messages)
        self.runtime.startup_messages.clear()
        for message in startup_messages:
            self.transcript.system_message(message)

    async def show_command_list(self) -> None:
        lines = ["可用命令："]
        for command in self.command_registry.list_commands():
            names = [f"/{command.name}", *(f"/{alias}" for alias in command.aliases)]
            lines.append(f"  {', '.join(names):<24} {command.description}")
        self.transcript.system_message("\n".join(lines))
