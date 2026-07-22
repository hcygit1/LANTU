# 来源：公众号@小林coding
# 后端八股网站：xiaolincoding.com
# Agent网站：xiaolinnote.com
# 简历模版：jianli.xiaolinnote.com

from __future__ import annotations

import inspect

from lantu.commands.registry import Command, CommandContext, CommandType


async def handle_exit(ctx: CommandContext) -> None:
    request_exit = ctx.config.get("request_exit")
    if request_exit is None:
        ctx.ui.add_system_message("当前前端不支持 /exit")
        return

    result = request_exit()
    if inspect.isawaitable(result):
        await result


EXIT_COMMAND = Command(
    name="exit",
    aliases=["quit"],
    description="退出 Lantu",
    usage="/exit",
    type=CommandType.LOCAL_UI,
    handler=handle_exit,
)
