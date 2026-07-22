from rich.console import Group, RenderableType
from rich.text import Text

from lantu.ui.shared.formatting import package_version, shorten_home
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
    release = version or package_version()
    path = shorten_home(work_dir)

    if width < 48 or not unicode:
        return Group(
            Text(f"LANTU {release} · {model}", style=DEFAULT_THEME.accent),
            Text(path, style=DEFAULT_THEME.muted, overflow="ellipsis"),
        )

    first = Text("┃  █▀█ █▄░█ ▀█▀ █░█", style=DEFAULT_THEME.accent)
    second = Text("┃  █▀█ █░▀█  █  █▄█", style=DEFAULT_THEME.accent)
    third = Text("┗━━ ", style=DEFAULT_THEME.accent)
    third.append(f"{release} · {model} · {mode}", style=DEFAULT_THEME.muted)
    fourth = Text(f"    {path}", style=DEFAULT_THEME.muted, overflow="ellipsis")
    return Group(first, second, third, fourth)
