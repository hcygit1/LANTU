from dataclasses import dataclass


@dataclass(frozen=True)
class InlineTheme:
    accent: str = "bold cyan"
    text: str = "default"
    muted: str = "dim"
    success: str = "green"
    warning: str = "yellow"
    error: str = "bold red"
    user_marker: str = "bold cyan"


DEFAULT_THEME = InlineTheme()
