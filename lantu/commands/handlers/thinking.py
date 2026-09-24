from __future__ import annotations

from lantu.commands.registry import Command, CommandContext, CommandType


async def handle_thinking(ctx: CommandContext) -> None:
    runtime = ctx.config["runtime"]
    if not ctx.args:
        state = "on" if runtime.provider.thinking else "off"
        ctx.ui.add_system_message(f"思考模式: {state}\n用法: /thinking <on|off>")
        return
    value = ctx.args.lower()
    if value not in {"on", "off"}:
        ctx.ui.add_system_message("用法: /thinking <on|off>")
        return
    runtime.set_thinking(value == "on")
    ctx.ui.refresh_status()
    ctx.ui.add_system_message(f"思考模式已切换为: {value}")


THINKING_COMMAND = Command(
    name="thinking",
    description="开启或关闭模型思考模式",
    usage="/thinking [on|off]",
    type=CommandType.LOCAL,
    handler=handle_thinking,
)
