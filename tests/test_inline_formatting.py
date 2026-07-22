from importlib.metadata import PackageNotFoundError
from pathlib import Path, PureWindowsPath

import pytest

from lantu.ui.shared import formatting
from lantu.ui.shared.formatting import (
    format_elapsed,
    format_tokens,
    package_version,
    shorten_home,
)


def test_package_version_falls_back_when_distribution_is_missing(monkeypatch):
    def missing_version(distribution_name: str) -> str:
        raise PackageNotFoundError(distribution_name)

    monkeypatch.setattr(formatting, "version", missing_version)

    assert package_version() == "0.0.0"


def test_shorten_home_replaces_home_prefix(monkeypatch):
    monkeypatch.setattr(
        formatting.Path,
        "home",
        classmethod(lambda cls: Path("/Users/demo")),
    )

    assert shorten_home("/Users/demo/work/repo") == "~/work/repo"
    assert shorten_home("/Users/demo") == "~"
    assert shorten_home("/Users/demonstration/repo") == "/Users/demonstration/repo"


def test_shorten_home_uses_path_separator_semantics(monkeypatch):
    class TestWindowsPath(PureWindowsPath):
        @classmethod
        def home(cls):
            return cls("C:/Users/demo")

        def expanduser(self):
            return self

    monkeypatch.setattr(formatting, "Path", TestWindowsPath)

    assert shorten_home(r"C:\Users\demo\work\repo") == r"~\work\repo"


def test_sanitize_terminal_text_replaces_c0_and_c1_controls():
    controls = "".join(
        chr(code) for code in (*range(0x20), *range(0x7F, 0xA0))
    )
    value = f"safe{controls}text"

    assert formatting.sanitize_terminal_text(value) == f"safe{' ' * len(controls)}text"


@pytest.mark.parametrize(
    ("used", "limit", "expected"),
    [
        (999, 1_000, "999/1.0k"),
        (18_200, 128_000, "18.2k/128k"),
        (100_000, 1_000_000, "100k/1000k"),
    ],
)
def test_format_tokens(used, limit, expected):
    assert format_tokens(used, limit) == expected


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [
        (0, "0.0s"),
        (12.34, "12.3s"),
        (60, "1m 0s"),
        (125.9, "2m 5s"),
    ],
)
def test_format_elapsed(seconds, expected):
    assert format_elapsed(seconds) == expected
