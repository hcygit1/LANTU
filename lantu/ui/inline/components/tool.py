from typing import Any

from rich.console import Group, RenderableType
from rich.text import Text

from lantu.ui.shared.formatting import format_elapsed, sanitize_terminal_text
from lantu.ui.shared.models import ToolStatus, ToolViewState
from lantu.ui.shared.theme import DEFAULT_THEME


_STATUS_MARKERS = {
    ToolStatus.RUNNING: ("◐", DEFAULT_THEME.warning),
    ToolStatus.SUCCESS: ("✓", DEFAULT_THEME.success),
    ToolStatus.ERROR: ("✗", DEFAULT_THEME.error),
}


def _safe_inline(value: Any) -> str:
    return sanitize_terminal_text(str(value))


def _key_argument(state: ToolViewState, name: str) -> str:
    if name in {"Read", "ReadFile", "Write", "WriteFile", "Edit", "EditFile"}:
        value = state.arguments.get("file_path", state.arguments.get("path", ""))
    elif name == "Bash":
        value = state.arguments.get("command", "")
    else:
        value = ""
    return _safe_inline(value).strip()[:180]


def summarize_tool_output(name: str, output: str, is_error: bool) -> str:
    name = _safe_inline(name).strip()
    safe_output = sanitize_terminal_text(output, preserve_newlines=True)
    clean = " ".join(safe_output.strip().split())
    if name in {"Read", "ReadFile"}:
        return f"读取 {len(safe_output.splitlines())} 行"
    if name in {"Write", "WriteFile", "Edit", "EditFile"}:
        return "修改已写入" if not is_error else clean[:180]
    if name == "Bash":
        lines = [line.strip() for line in safe_output.splitlines() if line.strip()]
        return (lines[-1] if lines else "命令完成")[:180]
    return clean[:180]


def render_tool(state: ToolViewState) -> RenderableType:
    marker, style = _STATUS_MARKERS[state.status]
    safe_name = _safe_inline(state.name).strip()
    argument = _key_argument(state, safe_name)
    title = f"{marker} {safe_name}"
    if argument:
        title += f" {argument}"

    summary = summarize_tool_output(
        state.name,
        state.output,
        state.status is ToolStatus.ERROR,
    )
    return Group(
        Text(title, style=style),
        Text(
            f"  {summary} · {format_elapsed(state.elapsed)}",
            style=DEFAULT_THEME.muted,
        ),
    )


def render_tool_details(state: ToolViewState) -> RenderableType:
    safe_name = _safe_inline(state.name).strip()
    safe_output = sanitize_terminal_text(state.output, preserve_newlines=True)
    heading = Text(f"{safe_name} 详细输出", style=DEFAULT_THEME.muted)
    body = Text(safe_output or "(无输出)")
    return Group(heading, body)
