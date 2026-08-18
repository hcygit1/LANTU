from __future__ import annotations

from lantu.commands.handlers.clear import CLEAR_COMMAND
from lantu.commands.handlers.compact import COMPACT_COMMAND
from lantu.commands.handlers.exit import EXIT_COMMAND
from lantu.commands.handlers.help import HELP_COMMAND
from lantu.commands.handlers.mcp import MCP_COMMAND
from lantu.commands.handlers.memory import MEMORY_COMMAND
from lantu.commands.handlers.permission import PERMISSION_COMMAND
from lantu.commands.handlers.plan import PLAN_COMMAND
from lantu.commands.handlers.sandbox import SANDBOX_COMMAND
from lantu.commands.handlers.session import SESSION_COMMAND
from lantu.commands.handlers.skill import SKILL_COMMAND
from lantu.commands.handlers.rewind import REWIND_COMMAND
from lantu.commands.handlers.repo_map import REPO_MAP_COMMAND
from lantu.commands.handlers.status import STATUS_COMMAND
from lantu.commands.handlers.tools import TOOLS_COMMAND
from lantu.commands.registry import CommandRegistry


ALL_COMMANDS = [
    HELP_COMMAND,
    EXIT_COMMAND,
    COMPACT_COMMAND,
    CLEAR_COMMAND,
    PLAN_COMMAND,
    SESSION_COMMAND,
    MCP_COMMAND,
    MEMORY_COMMAND,
    PERMISSION_COMMAND,
    SANDBOX_COMMAND,
    REWIND_COMMAND,
    STATUS_COMMAND,
    SKILL_COMMAND,
    TOOLS_COMMAND,
    REPO_MAP_COMMAND,
]


def register_all_commands(registry: CommandRegistry) -> None:
    for cmd in ALL_COMMANDS:
        registry.register_sync(cmd)
