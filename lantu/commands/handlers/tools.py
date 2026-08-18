from __future__ import annotations

from lantu.commands.registry import Command, CommandContext, CommandType


async def handle_tools(ctx: CommandContext) -> None:
    """Show or change the tool Schema loading mode before the first turn."""
    if ctx.agent is None:
        ctx.ui.add_system_message("当前没有活跃 Agent")
        return

    parts = ctx.args.split()
    if not parts or parts[0] != "mode":
        ctx.ui.add_system_message(
            f"当前工具模式: {ctx.agent.registry.loading_mode}\n"
            "用法: /tools mode [standard|progressive]"
        )
        return

    if len(parts) == 1:
        ctx.ui.add_system_message(
            f"当前工具模式: {ctx.agent.registry.loading_mode}\n"
            "可选模式: standard, progressive"
        )
        return

    mode = parts[1].lower()
    if mode not in {"standard", "progressive"}:
        ctx.ui.add_system_message(
            f"未知工具模式: {mode}\n可选模式: standard, progressive"
        )
        return

    if getattr(ctx.agent, "tool_loading_mode_locked", False):
        ctx.ui.add_system_message(
            "当前会话已经开始，不能切换工具模式。请使用 /session new 后再切换。"
        )
        return

    try:
        ctx.agent.set_tool_loading_mode(mode)
    except ValueError as exc:
        ctx.ui.add_system_message(str(exc))
        return
    ctx.ui.add_system_message(f"工具模式已切换为: {mode}")


TOOLS_COMMAND = Command(
    name="tools",
    description="查看或切换工具加载模式",
    usage="/tools mode [standard|progressive]",
    type=CommandType.LOCAL,
    handler=handle_tools,
)
