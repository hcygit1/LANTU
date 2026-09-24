from __future__ import annotations

from lantu.commands.registry import Command, CommandContext, CommandType


def _format_aliases(cmd: Command) -> str:
    if not cmd.aliases:
        return cmd.name
    return cmd.name + ", " + ", ".join(f"/{a}" for a in cmd.aliases)


async def handle_help(ctx: CommandContext) -> None:
    registry = ctx.config["registry"]

    if ctx.args:
        cmd = registry.find(ctx.args.lower())
        if cmd is None:
            ctx.ui.add_system_message(f"未知命令：{ctx.args}，输入 /help 查看可用命令")
            return
        lines = [f"/{cmd.name}"]
        if cmd.aliases:
            lines[0] += f"  (别名: {', '.join('/' + a for a in cmd.aliases)})"
        lines.append(f"  {cmd.description}")
        if cmd.usage:
            lines.append(f"  用法: {cmd.usage}")
        if cmd.arg_prompt:
            lines.append(f"  参数: {cmd.arg_prompt}")
        ctx.ui.add_system_message("\n".join(lines))
        return

    groups = {
        "会话控制": {"clear", "compact", "session", "rewind", "exit"},
        "权限与安全": {"permission", "sandbox", "plan"},
        "工具与模型": {"tools", "model", "thinking", "mcp", "skill"},
        "状态与辅助": {"status", "memory", "help"},
    }
    commands = {cmd.name: cmd for cmd in registry.list_commands()}
    lines = ["可用命令："]
    shown: set[str] = set()
    for group, names in groups.items():
        group_commands = [commands[name] for name in names if name in commands]
        if not group_commands:
            continue
        lines.append("")
        lines.append(f"{group}")
        for cmd in sorted(group_commands, key=lambda item: item.name):
            shown.add(cmd.name)
            aliases_str = f"/{_format_aliases(cmd)}"
            lines.append(f"  {aliases_str:<30} {cmd.description}")
    for cmd in sorted(commands.values(), key=lambda item: item.name):
        if cmd.name in shown:
            continue
        aliases_str = f"/{_format_aliases(cmd)}"
        lines.append(f"  {aliases_str:<30} {cmd.description}")
    lines.append("")
    lines.append("输入 /help <命令名> 查看详细用法。")
    ctx.ui.add_system_message("\n".join(lines))


HELP_COMMAND = Command(
    name="help",
    aliases=["h", "?"],
    description="显示帮助信息",
    usage="/help [命令名]",
    type=CommandType.LOCAL,
    handler=handle_help,
)

