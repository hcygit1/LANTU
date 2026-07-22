from __future__ import annotations

from contextlib import contextmanager
from io import BytesIO
import os
import re
from typing import Iterator

import pexpect


FIXTURE = "tests/fixtures/run_inline_fake.py"
PYTHON = ".venv/bin/python"
PROMPT = "❯ ".encode()
WIDE_WORDMARK = "█▀█ █▄░█ ▀█▀ █░█".encode()
RUNTIME_CLOSED = b"RUNTIME_CLOSED"
CURSOR_HIDE = b"\x1b[?25l"
CURSOR_SHOW = b"\x1b[?25h"
FORBIDDEN_SEQUENCES = (
    b"\x1b[?47h",
    b"\x1b[?47l",
    b"\x1b[?1047h",
    b"\x1b[?1047l",
    b"\x1b[?1049h",
    b"\x1b[?1049l",
    b"\x1b[2J",
    b"\x1b[3J",
)
OSC_PATTERN = rb"\x1b\](?:(?!\x07|\x1b\\).)*(?:\x07|\x1b\\)"
CSI_PATTERN = rb"\x1b\[[0-?]*[ -/]*[@-~]"
ANSI_RE = re.compile(rb"(?:" + OSC_PATTERN + rb"|" + CSI_PATTERN + rb")", re.DOTALL)
TRANSIENT_REGION_RE = re.compile(
    re.escape(CURSOR_HIDE) + rb".*?" + re.escape(CURSOR_SHOW),
    re.DOTALL,
)


def strip_ansi(output: bytes) -> str:
    return ANSI_RE.sub(b"", output).replace(b"\r", b"").decode(errors="replace")


def static_scrollback_text(output: bytes) -> str:
    return strip_ansi(TRANSIENT_REGION_RE.sub(b"", output))


def test_strip_ansi_preserves_text_between_st_terminated_osc_sequences() -> None:
    output = b"\x1b]0;first\x1b\\ordinary text\x1b]0;second\x1b\\"

    assert strip_ansi(output) == "ordinary text"


def assert_terminal_restored(output: bytes) -> None:
    for sequence in FORBIDDEN_SEQUENCES:
        assert sequence not in output
    assert output.count(RUNTIME_CLOSED) == 1
    assert CURSOR_SHOW in output
    assert output.rfind(CURSOR_SHOW) > output.rfind(CURSOR_HIDE)


@contextmanager
def spawn_inline(
    *, rows: int = 24, cols: int = 80
) -> Iterator[tuple[pexpect.spawn, BytesIO]]:
    output = BytesIO()
    child = pexpect.spawn(
        PYTHON,
        [FIXTURE],
        cwd=os.getcwd(),
        encoding=None,
        timeout=10,
        dimensions=(rows, cols),
    )
    child.logfile_read = output
    try:
        yield child, output
    finally:
        if not child.closed:
            if child.isalive():
                child.terminate(force=True)
            child.close()


def exit_from_prompt(child: pexpect.spawn, command: bytes = b"/exit") -> None:
    child.sendline(command)
    child.expect(RUNTIME_CLOSED)
    child.expect(pexpect.EOF)
    child.close()
    assert child.exitstatus == 0


def test_inline_conversation_remains_in_scrollback_without_screen_switches() -> None:
    with spawn_inline(cols=100) as (child, log):
        child.expect(WIDE_WORDMARK)
        child.expect(PROMPT)
        child.sendline(b"hello")
        child.expect("处理完成".encode())
        child.expect(PROMPT)
        exit_from_prompt(child)

    output = log.getvalue()
    text = strip_ansi(output)
    static_text = static_scrollback_text(output)
    assert "hello" in text
    assert "正在处理" in text
    assert "ReadFile demo.py" in text
    assert "读取 2 行" in text
    assert "处理完成" in text
    assert static_text.count("❯ hello") == 1
    assert static_text.count("● 正在处理") == 1
    assert static_text.count("✓ ReadFile demo.py") == 1
    assert static_text.count("● 处理完成") == 1
    assert_terminal_restored(output)


def test_quit_alias_exits_normally() -> None:
    with spawn_inline() as (child, log):
        child.expect(WIDE_WORDMARK)
        child.expect(PROMPT)
        exit_from_prompt(child, b"/quit")

    assert_terminal_restored(log.getvalue())


def test_narrow_terminal_uses_compact_ascii_header() -> None:
    with spawn_inline(cols=40) as (child, log):
        child.expect(b"LANTU 0.2.0")
        child.expect(PROMPT)
        exit_from_prompt(child)

    output = log.getvalue()
    text = strip_ansi(output)
    assert "LANTU 0.2.0" in text
    assert "█▀█" not in text
    assert_terminal_restored(output)


def test_ctrl_c_cancels_active_turn_and_returns_to_prompt() -> None:
    with spawn_inline(cols=100) as (child, log):
        child.expect(WIDE_WORDMARK)
        child.expect(PROMPT)
        child.sendline(b"slow")
        child.expect("正在处理".encode())
        child.sendcontrol("c")
        child.expect(b"Operation cancelled")
        child.expect(PROMPT)
        assert child.isalive()
        child.sendline(b"hello")
        child.expect("处理完成".encode())
        child.expect(PROMPT)
        exit_from_prompt(child)

    output = log.getvalue()
    text = strip_ansi(output)
    assert "Operation cancelled" in text
    assert "处理完成" in text
    assert_terminal_restored(output)


def test_permission_selection_is_audited_without_repeating_input() -> None:
    with spawn_inline(cols=100) as (child, log):
        child.expect(WIDE_WORDMARK)
        child.expect(PROMPT)
        child.sendline(b"permission")
        child.expect("选择:".encode())
        child.sendline(b"allow")
        child.expect("权限选择: allow".encode())
        child.expect(PROMPT)
        exit_from_prompt(child)

    output = log.getvalue()
    static_text = static_scrollback_text(output)
    assert static_text.count("权限选择: allow") == 1
    assert static_text.count("allow") == 1
    assert_terminal_restored(output)


def test_runtime_failure_closes_runtime_and_restores_terminal() -> None:
    with spawn_inline(cols=100) as (child, log):
        child.expect(WIDE_WORDMARK)
        child.expect(PROMPT)
        child.sendline(b"crash")
        child.expect("即将失败".encode())
        child.expect(RUNTIME_CLOSED)
        child.expect(pexpect.EOF)
        child.close()
        assert child.exitstatus not in (None, 0)

    output = log.getvalue()
    assert "即将失败" in strip_ansi(output)
    assert_terminal_restored(output)


def test_wide_terminal_uses_pixel_wordmark() -> None:
    with spawn_inline(cols=100) as (child, log):
        child.expect(WIDE_WORDMARK)
        child.expect(PROMPT)
        exit_from_prompt(child)

    output = log.getvalue()
    text = strip_ansi(output)
    assert "█▀█ █▄░█ ▀█▀ █░█" in text
    assert "┗━━ 0.2.0 · fake-model · default" in text
    assert "LANTU 0.2.0" not in text
    assert_terminal_restored(output)
