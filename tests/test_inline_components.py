from dataclasses import FrozenInstanceError
from io import StringIO

import pytest
from rich.console import Console

from lantu.ui.inline.components.header import render_header
from lantu.ui.shared.models import LiveViewState, ToolStatus, ToolViewState
from lantu.ui.shared.theme import DEFAULT_THEME


def render_text(renderable, width: int = 100) -> str:
    output = StringIO()
    Console(file=output, force_terminal=False, width=width).print(renderable)
    return output.getvalue()


def test_default_theme_uses_semantic_foreground_styles_only():
    assert DEFAULT_THEME.accent == "bold cyan"
    assert DEFAULT_THEME.text == "default"
    assert DEFAULT_THEME.muted == "dim"
    assert DEFAULT_THEME.success == "green"
    assert DEFAULT_THEME.warning == "yellow"
    assert DEFAULT_THEME.error == "bold red"
    assert DEFAULT_THEME.user_marker == "bold cyan"
    assert all(" on " not in style for style in vars(DEFAULT_THEME).values())

    with pytest.raises(FrozenInstanceError):
        DEFAULT_THEME.accent = "red"


def test_tool_view_state_has_running_defaults():
    tool = ToolViewState("tool-1", "Read", {"path": "README.md"})

    assert tool.status is ToolStatus.RUNNING
    assert tool.output == ""
    assert tool.elapsed == 0.0


def test_live_view_state_uses_independent_tool_mappings():
    first = LiveViewState()
    second = LiveViewState()
    first.tools["tool-1"] = ToolViewState("tool-1", "Read", {})

    assert second.tools == {}
    assert first.assistant_text == ""
    assert first.thinking_text == ""
    assert first.status_text == ""
    assert first.input_tokens == 0
    assert first.output_tokens == 0


def test_header_uses_pixel_wordmark_on_wide_terminal():
    text = render_text(
        render_header("deepseek-chat", "default", "/tmp/project", version="0.2.0"),
        width=100,
    )

    assert "█▀█" in text
    assert "0.2.0 · deepseek-chat · default" in text
    assert "/tmp/project" in text


def test_header_falls_back_on_narrow_terminal():
    text = render_text(
        render_header(
            "deepseek-chat",
            "default",
            "/tmp/project",
            version="0.2.0",
            width=35,
        ),
        width=35,
    )

    assert "LANTU 0.2.0" in text
    assert "█▀█" not in text


def test_header_has_ascii_fallback():
    text = render_text(
        render_header(
            "deepseek-chat",
            "default",
            "/tmp/project",
            version="0.2.0",
            unicode=False,
        )
    )

    assert "LANTU 0.2.0" in text
    assert "█" not in text
    assert text.isascii()


def test_header_ascii_fallback_escapes_unicode_dynamic_fields():
    text = render_text(
        render_header(
            "模型",
            "默认",
            "/tmp/项目",
            version="版本",
            unicode=False,
        )
    )

    assert text.isascii()


def test_header_sanitizes_csi_and_osc_sequences():
    text = render_text(
        render_header(
            "deep\x1b[2Jseek",
            "def\x1b]0;owned\x07ault",
            "/tmp/pro\x9b2Jject",
            version="0.2.0\x00",
        )
    )

    assert "\x1b" not in text
    assert "\x07" not in text
    assert "\x9b" not in text
    assert "\x00" not in text


def test_header_dynamic_fields_cannot_inject_extra_lines():
    text = render_text(
        render_header(
            "deepseek\nFORGED-MODEL",
            "default\rFORGED-MODE",
            "/tmp/project\nFORGED-PATH",
            version="0.2.0\nFORGED-VERSION",
        )
    )

    assert len(text.splitlines()) == 4
