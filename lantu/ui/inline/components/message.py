from rich.console import RenderableType
from rich.markdown import Markdown
from rich.table import Table
from rich.text import Text

from lantu.ui.shared.formatting import sanitize_terminal_text
from lantu.ui.shared.theme import DEFAULT_THEME


def _safe_message(content: str) -> str:
    return sanitize_terminal_text(content, preserve_newlines=True)


def render_user_message(content: str) -> RenderableType:
    text = Text("❯ ", style=DEFAULT_THEME.user_marker)
    text.append(_safe_message(content))
    return text


def render_assistant_message(content: str) -> RenderableType:
    message = Table.grid(padding=0)
    message.add_column(no_wrap=True)
    message.add_column()
    message.add_row(Text("● "), Markdown(_safe_message(content)))
    return message


def render_system_message(content: str) -> RenderableType:
    return Text(f"  {_safe_message(content)}", style=DEFAULT_THEME.muted)


def render_error_message(content: str) -> RenderableType:
    return Text(f"✗ {_safe_message(content)}", style=DEFAULT_THEME.error)
