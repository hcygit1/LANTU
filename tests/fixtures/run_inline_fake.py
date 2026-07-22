from __future__ import annotations

import asyncio
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from typing import Any

os.environ["PROMPT_TOOLKIT_NO_CPR"] = "1"
os.environ["TERM"] = "xterm-256color"
os.environ.pop("NO_COLOR", None)

from lantu.agent import (
    LoopComplete,
    StreamText,
    ToolResultEvent,
    ToolUseEvent,
)
from lantu.commands.handlers import register_all_commands
from lantu.commands.registry import CommandRegistry
from lantu.conversation import (
    ConversationManager,
    Message,
    ToolResultBlock,
    ToolUseBlock,
)
from lantu.permissions import PermissionMode
from lantu.tools import ToolRegistry
from lantu.ui.inline.app import InlineApp


class FakeSession:
    def __init__(self) -> None:
        self.session_id = "inline-pty"
        self.messages: list[Message] = []
        self.records: list[Any] = []
        self.meta = SimpleNamespace(total_tokens=0)

    def append(self, message: Message) -> None:
        self.messages.append(message)

    def append_record(self, record: Any) -> None:
        self.records.append(record)

    def update_total_tokens(self, total: int) -> None:
        self.meta.total_tokens = total


class FakeAgent:
    def __init__(self, work_dir: Path, registry: ToolRegistry) -> None:
        self.work_dir = str(work_dir)
        self.registry = registry
        self.permission_mode = PermissionMode.DEFAULT
        self.context_window = 200_000
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self.memory_recall_task: asyncio.Task[str] | None = None
        self._memory_recall_consumed = False
        self.session_id = "inline-pty"
        self.file_history: Any = None

    @property
    def plan_mode(self) -> bool:
        return False

    async def run(self, conversation: ConversationManager):
        prompt = conversation.history[-1].content
        yield StreamText("正在处理")
        if prompt == "slow":
            await asyncio.sleep(30)
            return

        conversation.add_assistant_message(
            "正在处理",
            tool_uses=[ToolUseBlock("t1", "ReadFile", {"file_path": "demo.py"})],
        )
        yield ToolUseEvent("ReadFile", "t1", {"file_path": "demo.py"})

        conversation.add_tool_results_message(
            [ToolResultBlock("t1", "a\nb\n", False)]
        )
        yield ToolResultEvent("t1", "ReadFile", "a\nb\n", False, 0.1)

        yield StreamText("处理完成")
        conversation.add_assistant_message("处理完成")
        yield LoopComplete(1)


class FakeTaskManager:
    def poll_completed(self) -> list[Any]:
        return []


class FakeTeamManager:
    def drain_lead_mailbox(self) -> list[str]:
        return []


class FakeRuntime:
    def __init__(self, work_dir: Path) -> None:
        self.registry = ToolRegistry()
        self.agent = FakeAgent(work_dir, self.registry)
        self.conversation = ConversationManager()
        self.command_registry = CommandRegistry()
        register_all_commands(self.command_registry)
        self.provider = SimpleNamespace(model="fake-model")
        self.session = FakeSession()
        self.session_manager = SimpleNamespace()
        self.memory_manager = SimpleNamespace()
        self.skill_loader = SimpleNamespace()
        self.skill_executor = SimpleNamespace()
        self.task_manager = FakeTaskManager()
        self.team_manager = FakeTeamManager()
        self.startup_messages: list[str] = []
        self.mcp_instructions = ""
        self.file_history: Any = None
        self.close_calls = 0

    def refresh_skills_if_needed(self) -> None:
        return None

    async def wait_until_ready(self) -> None:
        return None

    async def prefetch_relevant_memories(self, query: str) -> str:
        return ""

    async def close(self) -> None:
        self.close_calls += 1


async def main() -> None:
    with TemporaryDirectory(prefix="lantu-inline-pty-") as work_dir:
        runtime = FakeRuntime(Path(work_dir))
        await InlineApp(runtime).run()
        if runtime.close_calls != 1:
            raise RuntimeError(f"runtime.close called {runtime.close_calls} times")


if __name__ == "__main__":
    asyncio.run(main())
