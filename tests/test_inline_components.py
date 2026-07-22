from dataclasses import FrozenInstanceError
from io import StringIO

import pytest
from rich.console import Console

from lantu.ui.inline.components.header import render_header
from lantu.ui.inline.components.message import (
    render_assistant_message,
    render_error_message,
    render_system_message,
    render_user_message,
)
from lantu.ui.inline.components.status import render_live_state
from lantu.ui.inline.components.tool import (
    render_tool,
    render_tool_details,
    summarize_tool_output,
)
from lantu.ui.inline.transcript import TranscriptRenderer
from lantu.ui.shared.models import LiveViewState, ToolStatus, ToolViewState
from lantu.ui.shared.theme import DEFAULT_THEME


def render_text(renderable, width: int = 100) -> str:
    output = StringIO()
    Console(file=output, force_terminal=False, width=width).print(renderable)
    return output.getvalue()


class NoSplitlinesString(str):
    def splitlines(self, *args, **kwargs):
        raise AssertionError("summary must not call str.splitlines()")


class ScanLimitedString(str):
    def __new__(cls, value: str, iteration_limit: int):
        instance = super().__new__(cls, value)
        instance.iteration_limit = iteration_limit
        return instance

    def __iter__(self):
        for index, character in enumerate(super().__iter__()):
            if index >= self.iteration_limit:
                raise AssertionError("summary scanned beyond its bounded prefix")
            yield character


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


@pytest.mark.parametrize("status", list(ToolStatus))
def test_tool_view_state_normalizes_string_status(status):
    tool = ToolViewState("tool-1", "Read", {}, status=status.value)

    assert tool.status is status


def test_tool_view_state_rejects_unknown_status():
    with pytest.raises(ValueError):
        ToolViewState("tool-1", "Read", {}, status="paused")


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


def test_user_and_assistant_markers_are_distinct():
    assert "❯ 修改配置" in render_text(render_user_message("修改配置"))
    assert "● 已完成" in render_text(render_assistant_message("已完成"))


def test_assistant_heading_keeps_markdown_semantics():
    text = render_text(render_assistant_message("# 配置结果"))

    assert "●" in text
    assert "配置结果" in text
    assert "# 配置结果" not in text


def test_assistant_fenced_code_keeps_markdown_semantics():
    text = render_text(
        render_assistant_message('```python\nprint("ok")\n```'),
        width=120,
    )

    assert "●" in text
    assert 'print("ok")' in text
    assert "```" not in text


def test_assistant_list_keeps_first_block_markdown_semantics():
    text = render_text(render_assistant_message("- 第一项\n- 第二项"))

    assert "●" in text
    assert "• 第一项" in text
    assert "• 第二项" in text


def test_system_message_is_indented():
    assert "  正在连接" in render_text(render_system_message("正在连接"))


def test_error_has_symbol_without_relying_on_color():
    assert "✗ 网络错误" in render_text(render_error_message("网络错误"))


@pytest.mark.parametrize(
    "renderer",
    [render_user_message, render_assistant_message, render_system_message, render_error_message],
)
def test_messages_remove_terminal_controls_but_preserve_newlines(renderer):
    text = render_text(renderer("第一行\n第二行\x1b[2J\x9b0m\x00"))

    assert "第一行" in text
    assert "第二行" in text
    assert "\x1b" not in text
    assert "\x9b" not in text
    assert "\x00" not in text


def test_read_tool_summary_is_compact():
    tool = ToolViewState(
        tool_id="t1",
        name="ReadFile",
        arguments={"file_path": "lantu/config.py"},
        status=ToolStatus.SUCCESS,
        output="line\n" * 12,
        elapsed=0.2,
    )

    text = render_text(render_tool(tool))

    assert "ReadFile lantu/config.py" in text
    assert "读取 12 行" in text
    assert "line\nline\nline" not in text


def test_long_output_summary_is_bounded():
    summary = summarize_tool_output("Bash", "x" * 10_000, False)

    assert len(summary) <= 180


def test_read_tool_counts_lines_from_raw_output():
    assert summarize_tool_output("ReadFile", "a\rb", False) == "读取 2 行"


@pytest.mark.parametrize(
    "separator",
    ["\r\n", "\r", "\n", "\v", "\f", "\x1c", "\x1d", "\x1e", "\x85", "\u2028", "\u2029"],
)
def test_read_tool_matches_splitlines_for_all_line_boundaries(separator):
    output = NoSplitlinesString(f"a{separator}b{separator}")

    assert summarize_tool_output("Read", output, False) == "读取 2 行"


def test_large_read_and_bash_summaries_do_not_build_splitlines_lists():
    line_count = 250_000
    output = NoSplitlinesString("ignored\r\n" * line_count + "  final\tresult  ")

    assert len(output) > 2_000_000
    assert summarize_tool_output("ReadFile", output, True) == f"读取 {line_count + 1} 行"
    assert summarize_tool_output("Bash", output, True) == "final result"


def test_write_success_does_not_scan_output():
    output = ScanLimitedString("x" * 2_000_000, iteration_limit=0)

    assert summarize_tool_output("WriteFile", output, False) == "修改已写入"


def test_generic_summary_stops_after_bounded_normalized_prefix():
    output = ScanLimitedString("x" * 2_000_000, iteration_limit=180)

    assert summarize_tool_output("Search", output, False) == "x" * 180


def test_read_and_bash_keep_contract_specific_error_summaries():
    assert summarize_tool_output("Read", "a\nb", True) == "读取 2 行"
    assert summarize_tool_output("Bash", "first\nlast", True) == "last"


@pytest.mark.parametrize(
    ("name", "output", "is_error", "expected"),
    [
        ("WriteFile", "ignored", False, "修改已写入"),
        ("Edit", "  failed\nreason  ", True, "failed reason"),
        ("Bash", "first\n\nlast\n", False, "last"),
        ("Bash", "\n ", False, "命令完成"),
        ("Search", "  one\n two  ", False, "one two"),
    ],
)
def test_tool_output_summary_rules_are_deterministic(name, output, is_error, expected):
    assert summarize_tool_output(name, output, is_error) == expected


def test_tool_statuses_use_distinct_visible_symbols():
    running = ToolViewState("t1", "Bash", {"command": "pwd"})
    success = ToolViewState(
        "t2", "Bash", {"command": "pwd"}, status=ToolStatus.SUCCESS
    )
    failed = ToolViewState(
        "t3", "Bash", {"command": "pwd"}, status=ToolStatus.ERROR
    )

    assert "◐ Bash pwd" in render_text(render_tool(running))
    assert "✓ Bash pwd" in render_text(render_tool(success))
    assert "✗ Bash pwd" in render_text(render_tool(failed))


def test_tool_rendering_falls_back_for_mutated_unknown_status():
    tool = ToolViewState("t1", "Bash", {"command": "pwd"})
    tool.status = "paused"

    assert "? Bash pwd" in render_text(render_tool(tool))


def test_tool_details_preserve_full_output():
    tool = ToolViewState(
        "t1",
        "Bash",
        {"command": "pwd"},
        output="/tmp/project\n" + "x" * 1_000,
    )

    text = render_text(render_tool_details(tool), width=1_200)

    assert "Bash 详细输出" in text
    assert "/tmp/project" in text
    assert "x" * 1_000 in text


def test_tool_rendering_sanitizes_external_text():
    tool = ToolViewState(
        "t1",
        "Bash\x00",
        {"command": "pwd\nforged\x9b0m"},
        status=ToolStatus.ERROR,
        output="failed\nagain\x1b]0;owned\x07",
    )

    text = render_text(render_tool(tool)) + render_text(render_tool_details(tool))

    assert "pwd forged" in text
    assert "failed\nagain" in text
    assert "\x1b" not in text
    assert "\x9b" not in text
    assert "\x07" not in text
    assert "\x00" not in text


def test_live_state_renders_non_empty_sections():
    state = LiveViewState(
        assistant_text="正在处理",
        thinking_text="分析配置",
        tools={"t1": ToolViewState("t1", "ReadFile", {"file_path": "config.py"})},
        status_text="等待工具",
        input_tokens=120,
        output_tokens=45,
    )

    text = render_text(render_live_state(state))

    assert "正在处理" in text
    assert "分析配置" in text
    assert "ReadFile config.py" in text
    assert "等待工具" in text
    assert "120" in text
    assert "45" in text


def test_live_state_accepts_deserialized_running_status():
    tool = ToolViewState("t1", "ReadFile", {"file_path": "config.py"})
    tool.status = "running"
    state = LiveViewState(tools={"t1": tool})

    assert "ReadFile config.py" in render_text(render_live_state(state))


def test_transcript_prints_committed_content_once_with_spacing_and_boundary():
    output = StringIO()
    console = Console(file=output, force_terminal=False, width=80)
    transcript = TranscriptRenderer(console)

    transcript.user_message("修改配置")
    transcript.system_message("已连接")
    transcript.clear_boundary()

    text = output.getvalue()
    assert text.count("❯ 修改配置") == 1
    assert text.count("已连接") == 1
    assert "\n\n" in text
    assert "新会话" in text


def test_transcript_header_uses_console_width():
    output = StringIO()
    transcript = TranscriptRenderer(
        Console(file=output, force_terminal=False, width=35)
    )

    transcript.header("deepseek-chat", "default", "/tmp/project", version="0.2.0")

    assert "LANTU 0.2.0" in output.getvalue()
    assert "█▀█" not in output.getvalue()
