from __future__ import annotations

import asyncio
import io
import os
import subprocess
import sys
from types import ModuleType, SimpleNamespace

import pytest

import lantu.__main__ as cli
from lantu.__main__ import build_parser, main, run_inline, run_interactive
from lantu.permissions import PermissionMode


TTY_ERROR = 'Error: interactive mode requires a TTY; use `lantu -p "prompt"` instead.\n'


class TTYStream(io.StringIO):
    def __init__(self, is_tty: bool) -> None:
        super().__init__()
        self._is_tty = is_tty

    def isatty(self) -> bool:
        return self._is_tty


def make_config():
    return SimpleNamespace(
        providers=[object()],
        permission_mode="default",
        mcp_servers=[],
        raw_hooks=[],
        enable_fork=True,
        enable_verification_agent=True,
        worktree=object(),
        teammate_mode="in-process",
        enable_coordinator_mode=True,
        sandbox=object(),
    )


def prepare_main(monkeypatch, argv: list[str], config=None) -> None:
    monkeypatch.setattr(sys, "argv", ["lantu", *argv])
    monkeypatch.setattr("lantu.__main__.load_config", lambda: config or make_config())
    monkeypatch.setattr("lantu.__main__.load_hooks", lambda _raw_hooks: [])


def install_inline_dependencies(
    monkeypatch, *, calls, provider, runtime, inline_app
) -> None:
    session_module = ModuleType("lantu.ui.inline.session")

    async def select_provider(providers):
        calls.append(("select_provider", providers))
        return provider

    session_module.select_provider = select_provider

    runtime_module = ModuleType("lantu.runtime")

    async def build_interactive_runtime(*args):
        calls.append(("build_interactive_runtime", args))
        return runtime

    runtime_module.build_interactive_runtime = build_interactive_runtime

    inline_module = ModuleType("lantu.ui.inline")
    inline_module.InlineApp = inline_app
    monkeypatch.setitem(sys.modules, "lantu.ui.inline.session", session_module)
    monkeypatch.setitem(sys.modules, "lantu.runtime", runtime_module)
    monkeypatch.setitem(sys.modules, "lantu.ui.inline", inline_module)


def test_tui_flag_is_available() -> None:
    args = build_parser().parse_args(["--tui"])

    assert args.tui is True


def test_parser_keeps_existing_options() -> None:
    args = build_parser().parse_args(
        [
            "-p",
            "hello",
            "--output-format",
            "stream-json",
            "--remote",
            "--mode",
            "bypassPermissions",
        ]
    )

    assert args.p == "hello"
    assert args.output_format == "stream-json"
    assert args.remote is True
    assert args.mode == "bypassPermissions"


def test_default_interactive_mode_uses_inline(monkeypatch) -> None:
    called = []
    monkeypatch.setattr(
        "lantu.__main__.run_inline", lambda *args: called.append(("inline", args))
    )
    monkeypatch.setattr(
        "lantu.__main__.run_tui", lambda *args: called.append(("tui", args))
    )

    config = make_config()
    run_interactive(
        config,
        PermissionMode.DEFAULT,
        None,
        build_parser().parse_args([]),
    )

    assert called == [("inline", (config, PermissionMode.DEFAULT, None))]


def test_tui_flag_uses_legacy_frontend(monkeypatch) -> None:
    called = []
    monkeypatch.setattr(
        "lantu.__main__.run_inline", lambda *args: called.append(("inline", args))
    )
    monkeypatch.setattr(
        "lantu.__main__.run_tui", lambda *args: called.append(("tui", args))
    )

    config = make_config()
    run_interactive(
        config,
        PermissionMode.DEFAULT,
        None,
        build_parser().parse_args(["--tui"]),
    )

    assert called == [("tui", (config, PermissionMode.DEFAULT, None))]


def test_run_inline_builds_runtime_in_current_directory(monkeypatch, tmp_path) -> None:
    calls = []
    provider = object()

    class Runtime:
        async def close(self) -> None:
            calls.append(("close", None))

    runtime = Runtime()

    class InlineApp:
        def __init__(self, value) -> None:
            calls.append(("InlineApp", value))

        async def run(self) -> None:
            calls.append(("run", None))

    install_inline_dependencies(
        monkeypatch,
        calls=calls,
        provider=provider,
        runtime=runtime,
        inline_app=InlineApp,
    )
    monkeypatch.chdir(tmp_path)

    config = make_config()
    run_inline(config, PermissionMode.PLAN, None)

    assert calls == [
        ("select_provider", config.providers),
        (
            "build_interactive_runtime",
            (config, provider, PermissionMode.PLAN, None, os.getcwd()),
        ),
        ("InlineApp", runtime),
        ("run", None),
        ("close", None),
    ]


def test_run_inline_closes_runtime_when_app_construction_fails(monkeypatch) -> None:
    calls = []

    class Runtime:
        async def close(self) -> None:
            calls.append("close")

    class BrokenInlineApp:
        def __init__(self, _runtime) -> None:
            raise RuntimeError("construction failed")

    install_inline_dependencies(
        monkeypatch,
        calls=calls,
        provider=object(),
        runtime=Runtime(),
        inline_app=BrokenInlineApp,
    )

    with pytest.raises(RuntimeError, match="construction failed"):
        run_inline(make_config(), PermissionMode.DEFAULT, None)

    assert calls[-1] == "close"


class ClosingClient:
    def __init__(self) -> None:
        self.close_calls = 0

    async def aclose(self) -> None:
        self.close_calls += 1


@pytest.mark.asyncio
async def test_run_prompt_closes_client_after_normal_completion(monkeypatch) -> None:
    client = ClosingClient()
    calls = []

    async def run_with_client(*args) -> None:
        calls.append(args)

    monkeypatch.setattr("lantu.client.create_client", lambda _provider: client)
    monkeypatch.setattr(
        cli, "_run_prompt_with_client", run_with_client, raising=False
    )
    config = make_config()

    await cli._run_prompt(
        config, PermissionMode.DEFAULT, None, "hello", "stream-json"
    )

    assert calls == [
        (
            config,
            PermissionMode.DEFAULT,
            None,
            "hello",
            "stream-json",
            config.providers[0],
            client,
        )
    ]
    assert client.close_calls == 1


@pytest.mark.asyncio
async def test_run_prompt_closes_client_after_stream_failure(monkeypatch) -> None:
    client = ClosingClient()

    async def fail_stream(*_args) -> None:
        raise RuntimeError("stream failed")

    monkeypatch.setattr("lantu.client.create_client", lambda _provider: client)
    monkeypatch.setattr(cli, "_run_prompt_with_client", fail_stream, raising=False)

    with pytest.raises(RuntimeError, match="stream failed"):
        await cli._run_prompt(
            make_config(), PermissionMode.DEFAULT, None, "hello", "text"
        )

    assert client.close_calls == 1


@pytest.mark.asyncio
async def test_run_prompt_closes_client_after_cancellation(monkeypatch) -> None:
    client = ClosingClient()

    async def cancel(*_args) -> None:
        raise asyncio.CancelledError

    monkeypatch.setattr("lantu.client.create_client", lambda _provider: client)
    monkeypatch.setattr(cli, "_run_prompt_with_client", cancel, raising=False)

    with pytest.raises(asyncio.CancelledError):
        await cli._run_prompt(
            make_config(), PermissionMode.DEFAULT, None, "hello", "text"
        )

    assert client.close_calls == 1


def test_importing_cli_does_not_load_textual_or_legacy_app() -> None:
    code = (
        "import sys; import lantu.__main__; "
        "assert 'lantu.app' not in sys.modules; "
        "assert not any(n == 'textual' or n.startswith('textual.') for n in sys.modules)"
    )
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(sys.path)}

    result = subprocess.run(
        [sys.executable, "-c", code],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_prompt_mode_has_priority_and_does_not_require_tty(monkeypatch) -> None:
    calls = []

    async def run_prompt(*args):
        calls.append(("prompt", args))

    prepare_main(
        monkeypatch,
        ["-p", "hello", "--remote", "--tui", "--output-format", "stream-json"],
    )
    monkeypatch.setattr("lantu.__main__._run_prompt", run_prompt)
    monkeypatch.setattr(
        "lantu.__main__.run_interactive", lambda *args: calls.append(("interactive", args))
    )
    monkeypatch.setattr(sys, "stdin", TTYStream(False))
    monkeypatch.setattr(sys, "stdout", TTYStream(False))

    main()

    assert [name for name, _args in calls] == ["prompt"]
    assert calls[0][1][3:] == ("hello", "stream-json")


def test_remote_mode_has_priority_over_tui_and_does_not_require_tty(monkeypatch) -> None:
    calls = []
    remote_module = ModuleType("lantu.remote")

    class RemoteServer:
        def __init__(self, **kwargs) -> None:
            calls.append(("remote_init", kwargs))

        async def run(self) -> None:
            calls.append(("remote_run", None))

    remote_module.RemoteServer = RemoteServer
    prepare_main(monkeypatch, ["--remote", "--tui"])
    monkeypatch.setitem(sys.modules, "lantu.remote", remote_module)
    monkeypatch.setattr(
        "lantu.__main__.run_interactive", lambda *args: calls.append(("interactive", args))
    )
    monkeypatch.setattr(sys, "stdin", TTYStream(False))
    monkeypatch.setattr(sys, "stdout", TTYStream(False))

    main()

    assert [name for name, _value in calls] == ["remote_init", "remote_run"]


@pytest.mark.parametrize("non_tty_stream", ["stdin", "stdout"])
@pytest.mark.parametrize("argv", [[], ["--tui"]])
def test_interactive_modes_require_stdin_and_stdout_tty(
    monkeypatch, non_tty_stream, argv
) -> None:
    prepare_main(monkeypatch, argv)
    stdin = TTYStream(True)
    stdout = TTYStream(True)
    stderr = TTYStream(True)
    monkeypatch.setattr(sys, "stdin", stdin)
    monkeypatch.setattr(sys, "stdout", stdout)
    monkeypatch.setattr(sys, "stderr", stderr)
    monkeypatch.setattr(sys, non_tty_stream, TTYStream(False))

    with pytest.raises(SystemExit) as exc_info:
        main()

    assert exc_info.value.code == 1
    assert stderr.getvalue() == TTY_ERROR


@pytest.mark.parametrize("mode", ["prompt", "remote", "interactive"])
def test_top_level_keyboard_interrupt_exits_with_130(monkeypatch, mode) -> None:
    argv = {"prompt": ["-p", "hello"], "remote": ["--remote"]}.get(mode, [])
    prepare_main(monkeypatch, argv)
    stderr = TTYStream(True)
    monkeypatch.setattr(sys, "stdin", TTYStream(True))
    monkeypatch.setattr(sys, "stdout", TTYStream(True))
    monkeypatch.setattr(sys, "stderr", stderr)

    if mode == "prompt":
        async def interrupt_prompt(*_args) -> None:
            raise KeyboardInterrupt

        monkeypatch.setattr("lantu.__main__._run_prompt", interrupt_prompt)
    elif mode == "remote":
        remote_module = ModuleType("lantu.remote")

        class RemoteServer:
            def __init__(self, **_kwargs) -> None:
                pass

            async def run(self) -> None:
                raise KeyboardInterrupt

        remote_module.RemoteServer = RemoteServer
        monkeypatch.setitem(sys.modules, "lantu.remote", remote_module)
    else:
        monkeypatch.setattr(
            "lantu.__main__.run_interactive",
            lambda *_args: (_ for _ in ()).throw(KeyboardInterrupt),
        )

    with pytest.raises(SystemExit) as exc_info:
        main()

    assert exc_info.value.code == 130
    assert stderr.getvalue() == ""
