from __future__ import annotations

from collections.abc import Sequence

from rich.console import Group, RenderableType
from rich.text import Text

from lantu.ui.shared.formatting import sanitize_terminal_text
from lantu.ui.shared.theme import DEFAULT_THEME


def _safe(value: object, *, preserve_newlines: bool = False) -> str:
    return sanitize_terminal_text(str(value), preserve_newlines=preserve_newlines)


def render_permission_request(
    tool_name: str,
    description: str,
) -> RenderableType:
    return Group(
        Text("权限请求", style=DEFAULT_THEME.warning),
        Text.assemble(
            ("  工具: ", DEFAULT_THEME.muted),
            (_safe(tool_name), DEFAULT_THEME.text),
        ),
        Text.assemble(
            ("  描述: ", DEFAULT_THEME.muted),
            (_safe(description, preserve_newlines=True), DEFAULT_THEME.text),
        ),
    )


def render_plan_request(plan_path: str, content: str) -> RenderableType:
    safe_content = _safe(content, preserve_newlines=True) or "(计划文件为空或不可读取)"
    return Group(
        Text("计划审批", style=DEFAULT_THEME.accent),
        Text(f"  路径: {_safe(plan_path)}", style=DEFAULT_THEME.muted),
        Text(safe_content),
    )


def render_question(
    name: str,
    message: str,
    options: Sequence[str] = (),
) -> RenderableType:
    lines: list[RenderableType] = [
        Text(f"问题 · {_safe(name)}", style=DEFAULT_THEME.accent),
        Text(_safe(message, preserve_newlines=True)),
    ]
    lines.extend(
        Text(f"  - {_safe(option)}", style=DEFAULT_THEME.muted)
        for option in options
    )
    return Group(*lines)
