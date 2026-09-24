from __future__ import annotations

from lantu.commands.registry import Command, CommandContext, CommandType


async def handle_mcp(ctx: CommandContext) -> None:
    app = ctx.ui
    runtime = getattr(app, "runtime", None)
    wait_until_ready = getattr(runtime, "wait_until_ready", None)
    if callable(wait_until_ready):
        await wait_until_ready()

    mcp_mgr = getattr(app, "mcp_manager", None)
    if mcp_mgr is None and runtime is not None:
        mcp_mgr = getattr(runtime, "mcp_manager", None)

    clients = getattr(mcp_mgr, "_clients", {}) if mcp_mgr is not None else {}
    if not clients:
        ctx.ui.add_system_message("No MCP servers connected")
        return

    tools = ctx.agent.registry.list_tools()
    tool_count = sum(
        1
        for tool in tools
        if tool.name.startswith(("mcp_", "mcp__"))
    )
    lines = ["MCP 状态", "─────────────"]
    lines.append(
        f"Connected to {len(clients)} MCP server(s), "
        f"{tool_count} tools registered"
    )

    if mcp_mgr and hasattr(mcp_mgr, "_clients"):
        for name, client in mcp_mgr._clients.items():
            tool_names = [
                t.name for t in ctx.agent.registry.list_tools()
                if t.name.startswith((f"mcp_{name}_", f"mcp__{name}__"))
            ]
            lines.append(f"\n  {name}: {len(tool_names)} tools")
            for tn in tool_names[:10]:
                short = tn.removeprefix(f"mcp_{name}_").removeprefix(
                    f"mcp__{name}__"
                )
                lines.append(f"    - {short}")
            if len(tool_names) > 10:
                lines.append(f"    … and {len(tool_names) - 10} more")

    ctx.ui.add_system_message("\n".join(lines))


MCP_COMMAND = Command(
    name="mcp",
    aliases=[],
    description="显示 MCP 服务器状态",
    usage="/mcp",
    type=CommandType.LOCAL,
    handler=handle_mcp,
)
