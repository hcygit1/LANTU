from __future__ import annotations

from lantu.commands.registry import Command, CommandContext, CommandType


async def handle_model(ctx: CommandContext) -> None:
    runtime = ctx.config["runtime"]
    provider = runtime.provider
    if not ctx.args or ctx.args == "list":
        models = provider.models or [provider.model]
        ctx.ui.add_system_message(
            f"当前模型: {provider.model}\n可用模型: {', '.join(models)}"
        )
        return
    model = ctx.args.strip()
    try:
        runtime.set_model(model)
    except ValueError as exc:
        ctx.ui.add_system_message(str(exc))
        return
    ctx.ui.refresh_status()
    ctx.ui.add_system_message(f"模型已切换为: {model}")


MODEL_COMMAND = Command(
    name="model",
    description="查看或切换当前 Provider 的模型",
    usage="/model [list|模型名]",
    type=CommandType.LOCAL,
    handler=handle_model,
)
