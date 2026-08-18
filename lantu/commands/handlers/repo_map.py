from __future__ import annotations

from lantu.commands.registry import Command, CommandContext, CommandType


def _format_snapshot(snapshot) -> str:
    suffix = "，已截断" if snapshot.truncated else ""
    return (
        f"RepoMap 已启用：{snapshot.files_indexed} 个文件，"
        f"{snapshot.symbols_indexed} 个符号，约 {snapshot.estimated_tokens} tokens"
        f"{suffix}"
    )


async def handle_repo_map(ctx: CommandContext) -> None:
    if ctx.agent is None:
        ctx.ui.add_system_message("当前没有活跃 Agent")
        return

    repo_map = getattr(ctx.agent, "repo_map", None)
    if repo_map is None:
        ctx.ui.add_system_message(
            "RepoMap 当前未启用。请在 config.yaml 中设置 "
            "context.repo_map.enabled: true，然后重新启动 Lantu。"
        )
        return

    action = ctx.args.strip().lower()
    if not action:
        ctx.ui.add_system_message(_format_snapshot(repo_map.snapshot))
        return
    if action != "refresh":
        ctx.ui.add_system_message("用法: /repo-map [refresh]")
        return

    snapshot = ctx.agent.refresh_repo_map()
    ctx.ui.add_system_message(f"RepoMap 已刷新。{_format_snapshot(snapshot)}")


REPO_MAP_COMMAND = Command(
    name="repo-map",
    description="查看或刷新仓库符号映射",
    usage="/repo-map [refresh]",
    type=CommandType.LOCAL,
    handler=handle_repo_map,
)
