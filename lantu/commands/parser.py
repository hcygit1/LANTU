from __future__ import annotations

from lantu.commands.registry import CommandRegistry


_ARGUMENTS: dict[str, dict[str, str]] = {
    "permission": {
        "mode": "切换权限模式",
        "rules": "查看权限规则",
        "add": "添加本地权限规则",
        "reset": "清空本地权限规则",
    },
    "tools": {"mode": "切换工具加载模式"},
    "model": {"list": "查看当前 Provider 的模型列表"},
    "thinking": {"on": "开启思考模式", "off": "关闭思考模式"},
}

_VALUES: dict[tuple[str, str], dict[str, str]] = {
    ("permission", "mode"): {
        "default": "自动允许读取，修改和命令需要确认",
        "acceptEdits": "自动允许文件修改，命令仍需确认",
        "plan": "只读规划模式",
        "bypassPermissions": "自动允许所有工具操作",
    },
    ("tools", "mode"): {
        "standard": "固定加载全部标准工具",
        "progressive": "按需加载延迟工具",
    },
}


def parse_command(text: str) -> tuple[str, str, bool]:
    text = text.strip()
    if not text.startswith("/"):
        return "", "", False
    text = text[1:]
    if not text:
        return "", "", True
    parts = text.split(None, 1)
    name = parts[0].lower()
    args = parts[1].strip() if len(parts) > 1 else ""
    return name, args, True


def complete(registry: CommandRegistry, prefix: str) -> list[tuple[str, str]]:
    """返回匹配命令的 (display_text, command_value) 列表。"""
    prefix = prefix.lstrip("/")
    parts = prefix.split()
    if len(parts) > 1 or prefix.endswith(" ") or prefix.lower() in _ARGUMENTS:
        command = parts[0].lower() if parts else ""
        argument_prefix = (
            parts[-1].lower()
            if parts and not prefix.endswith(" ") and len(parts) > 1
            else ""
        )
        options = _ARGUMENTS.get(command, {})
        if len(parts) >= 2 and parts[1].lower() == "mode":
            options = _VALUES.get((command, "mode"), {})
        if options:
            base = "/" + " ".join(parts[:-1]) + " "
            if len(parts) == 1:
                base = f"/{command} "
            elif len(parts) >= 2 and parts[1].lower() == "mode":
                base = f"/{command} mode "
            return [
                (f"{base}{name:<20} — {description}", f"{base}{name}")
                for name, description in options.items()
                if name.lower().startswith(argument_prefix)
            ]
    seen: set[str] = set()
    matches: list[tuple[str, str]] = []
    for cmd in registry.list_commands():
        if cmd.name in seen:
            continue
        if cmd.name.startswith(prefix) or any(a.startswith(prefix) for a in cmd.aliases):
            seen.add(cmd.name)
            desc = cmd.description
            if len(desc) > 30:
                desc = desc[:28] + "…"
            desc = desc.replace("[", "\\[")
            display = f"/{cmd.name:<16} — {desc}"
            matches.append((display, "/" + cmd.name))
    matches.sort(key=lambda x: x[1])
    return matches

