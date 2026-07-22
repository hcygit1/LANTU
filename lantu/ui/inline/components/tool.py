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

_LINE_BOUNDARIES = frozenset("\n\r\v\f\x1c\x1d\x1e\x85\u2028\u2029")
_SUMMARY_LIMIT = 180


def _safe_inline(value: Any) -> str:
    return sanitize_terminal_text(str(value))


def _key_argument(state: ToolViewState, name: str) -> str:
    if name in {"Read", "ReadFile", "Write", "WriteFile", "Edit", "EditFile"}:
        value = state.arguments.get("file_path", state.arguments.get("path", ""))
    elif name == "Bash":
        value = state.arguments.get("command", "")
    else:
        value = ""
    return _safe_inline(value).strip()[:_SUMMARY_LIMIT]


def _is_terminal_control(character: str) -> bool:
    codepoint = ord(character)
    return codepoint < 0x20 or 0x7F <= codepoint <= 0x9F


def _count_splitlines(value: str) -> int:
    if not value:
        return 0

    count = 0
    previous_was_cr = False
    ended_with_boundary = False
    for character in value:
        if character == "\n" and previous_was_cr:
            previous_was_cr = False
            ended_with_boundary = True
            continue
        if character in _LINE_BOUNDARIES:
            count += 1
            previous_was_cr = character == "\r"
            ended_with_boundary = True
        else:
            previous_was_cr = False
            ended_with_boundary = False
    return count if ended_with_boundary else count + 1


def _normalized_prefix(value: str) -> str:
    result: list[str] = []
    pending_space = False
    for character in value:
        if _is_terminal_control(character):
            character = " "
        if character.isspace():
            pending_space = bool(result)
            continue
        if pending_space:
            result.append(" ")
            if len(result) == _SUMMARY_LIMIT:
                break
            pending_space = False
        result.append(character)
        if len(result) == _SUMMARY_LIMIT:
            break
    return "".join(result)


def _last_nonempty_line(value: str) -> str:
    current: list[str] = []
    last = ""
    pending_space = False
    previous_was_cr = False

    for character in value:
        if character == "\n" and previous_was_cr:
            previous_was_cr = False
            continue
        if character in _LINE_BOUNDARIES:
            if current:
                last = "".join(current)
            current = []
            pending_space = False
            previous_was_cr = character == "\r"
            continue

        previous_was_cr = False
        if _is_terminal_control(character):
            character = " "
        if character.isspace():
            pending_space = bool(current)
            continue
        if pending_space:
            if len(current) < _SUMMARY_LIMIT:
                current.append(" ")
            pending_space = False
        if len(current) < _SUMMARY_LIMIT:
            current.append(character)

    if current:
        last = "".join(current)
    return last or "命令完成"


def summarize_tool_output(name: str, output: str, is_error: bool) -> str:
    name = _safe_inline(name).strip()
    if name in {"Read", "ReadFile"}:
        return f"读取 {_count_splitlines(output)} 行"
    if name in {"Write", "WriteFile", "Edit", "EditFile"}:
        return "修改已写入" if not is_error else _normalized_prefix(output)
    if name == "Bash":
        return _last_nonempty_line(output)
    return _normalized_prefix(output)


def render_tool(state: ToolViewState) -> RenderableType:
    try:
        status = ToolStatus(state.status)
    except ValueError:
        status = None
    marker, style = _STATUS_MARKERS.get(status, ("?", DEFAULT_THEME.warning))
    safe_name = _safe_inline(state.name).strip()
    argument = _key_argument(state, safe_name)
    title = f"{marker} {safe_name}"
    if argument:
        title += f" {argument}"

    summary = summarize_tool_output(
        state.name,
        state.output,
        status == ToolStatus.ERROR,
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
