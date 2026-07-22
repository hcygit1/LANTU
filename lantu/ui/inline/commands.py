from __future__ import annotations

from typing import TYPE_CHECKING

from lantu.commands import parse_command

if TYPE_CHECKING:
    from lantu.ui.inline.app import InlineApp


class InlineCommandDispatcher:
    def __init__(self, app: InlineApp) -> None:
        self.app = app

    async def dispatch(self, text: str) -> bool:
        name, args, is_command = parse_command(text)
        if not is_command:
            return False

        if not name:
            await self.app.show_command_list()
            return True

        command = self.app.command_registry.find(name)
        if command is None:
            self.app.add_system_message(
                f"未知命令：/{name}，输入 /help 查看可用命令"
            )
            return True

        if not args and command.arg_prompt:
            self.app.add_system_message(command.arg_prompt)
            return True

        try:
            await command.handler(self.app.build_command_context(args))
        except Exception as exc:
            self.app.transcript.error_message(f"命令执行失败: {exc}")
        return True
