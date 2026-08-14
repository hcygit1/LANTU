from rich.console import Group, RenderableType
from rich.spinner import Spinner
from rich.text import Text

from lantu.ui.inline.components.message import render_assistant_message
from lantu.ui.inline.components.tool import render_tool
from lantu.ui.shared.formatting import sanitize_terminal_text
from lantu.ui.shared.models import LiveViewState, ToolStatus
from lantu.ui.shared.theme import DEFAULT_THEME


def render_live_state(state: LiveViewState) -> RenderableType:
    sections: list[RenderableType] = []
    if state.is_waiting:
        sections.append(
            Spinner("dots", " 正在思考...", style=DEFAULT_THEME.muted)
        )
    if state.thinking_text:
        thinking = sanitize_terminal_text(
            state.thinking_text,
            preserve_newlines=True,
        )
        sections.append(Text(f"  思考: {thinking}", style=DEFAULT_THEME.muted))
    if state.assistant_text:
        sections.append(render_assistant_message(state.assistant_text))
    sections.extend(
        render_tool(tool)
        for tool in state.tools.values()
        if tool.status == ToolStatus.RUNNING
    )
    if state.status_text:
        status = sanitize_terminal_text(state.status_text, preserve_newlines=True)
        sections.append(Text(f"  {status}", style=DEFAULT_THEME.muted))
    if state.input_tokens or state.output_tokens:
        sections.append(
            Text(
                f"  Token  输入 {state.input_tokens} · 输出 {state.output_tokens}",
                style=DEFAULT_THEME.muted,
            )
        )
    return Group(*sections)
