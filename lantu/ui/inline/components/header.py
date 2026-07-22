from rich.console import Group, RenderableType
from rich.text import Text

from lantu.ui.shared.formatting import (
    package_version,
    sanitize_terminal_text,
    shorten_home,
)
from lantu.ui.shared.theme import DEFAULT_THEME


def render_header(
    model: str,
    mode: str,
    work_dir: str,
    *,
    version: str | None = None,
    width: int = 80,
    unicode: bool = True,
) -> RenderableType:
    release = sanitize_terminal_text(version or package_version())
    safe_model = sanitize_terminal_text(model)
    safe_mode = sanitize_terminal_text(mode)
    path = sanitize_terminal_text(shorten_home(work_dir))

    if width < 48 or not unicode:
        if not unicode:
            release = release.encode("ascii", "backslashreplace").decode("ascii")
            safe_model = safe_model.encode("ascii", "backslashreplace").decode("ascii")
            path = path.encode("ascii", "backslashreplace").decode("ascii")
        separator = " · " if unicode else " | "
        return Group(
            Text(
                f"LANTU {release}{separator}{safe_model}",
                style=DEFAULT_THEME.accent,
            ),
            Text(path, style=DEFAULT_THEME.muted, overflow="ellipsis"),
        )

    first = Text("┃  █▀█ █▄░█ ▀█▀ █░█", style=DEFAULT_THEME.accent)
    second = Text("┃  █▀█ █░▀█  █  █▄█", style=DEFAULT_THEME.accent)
    third = Text("┗━━ ", style=DEFAULT_THEME.accent)
    third.append(
        f"LANTU {release} · {safe_model} · {safe_mode}",
        style=DEFAULT_THEME.muted,
    )
    fourth = Text(f"    {path}", style=DEFAULT_THEME.muted, overflow="ellipsis")
    return Group(first, second, third, fourth)
