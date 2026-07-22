from importlib.metadata import PackageNotFoundError, version
from pathlib import Path


def package_version() -> str:
    try:
        return version("lantu")
    except PackageNotFoundError:
        return "0.0.0"


def sanitize_terminal_text(value: str) -> str:
    return "".join(
        " " if ord(character) < 0x20 or 0x7F <= ord(character) <= 0x9F else character
        for character in value
    )


def shorten_home(path: str) -> str:
    value = Path(path).expanduser()
    home = Path.home()
    try:
        relative = value.relative_to(home)
    except ValueError:
        return str(value)
    return "~" if relative == Path() else str(Path("~") / relative)


def format_tokens(used: int, limit: int) -> str:
    def compact(value: int) -> str:
        if value < 1000:
            return str(value)
        scaled = value / 1000
        return f"{scaled:.1f}k" if scaled < 100 else f"{scaled:.0f}k"

    return f"{compact(used)}/{compact(limit)}"


def format_elapsed(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.1f}s"
    return f"{int(seconds // 60)}m {int(seconds % 60)}s"
