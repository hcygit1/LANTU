from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from prompt_toolkit.document import Document
from prompt_toolkit.formatted_text import to_formatted_text
from prompt_toolkit.history import FileHistory
from prompt_toolkit.keys import Keys

from lantu.commands.handlers import register_all_commands
from lantu.commands.registry import Command, CommandRegistry, CommandType
from lantu.config import ProviderConfig
from lantu.ui.inline import session as session_module
from lantu.ui.inline.session import InlineCompleter, InlinePromptSession, select_provider
from lantu.ui.shared import references as references_module
from lantu.ui.shared.references import MAX_AT_REF_BYTES, expand_at_refs, scan_files


def _completions_at(completer: InlineCompleter, text: str, cursor_position: int):
    document = Document(text, cursor_position=cursor_position)
    return list(completer.get_completions(document, None))


def _completions(completer: InlineCompleter, text: str):
    return _completions_at(completer, text, len(text))


def _provider(name: str) -> ProviderConfig:
    return ProviderConfig(
        name=name,
        protocol="openai",
        base_url="https://example.test",
        model="test-model",
    )


def _binding_handler(bindings: Any, *keys: Keys) -> Callable[..., Any]:
    matches = bindings.get_bindings_for_keys(keys)
    assert matches
    return matches[-1].handler


class FakePromptSession:
    def __init__(self, responses: list[str], calls: list[dict[str, Any]]) -> None:
        self.responses = responses
        self.calls = calls

    async def prompt_async(self, *args: Any, **kwargs: Any) -> str:
        self.calls.append({"args": args, "kwargs": kwargs})
        return self.responses.pop(0)


def test_command_registry_completion_contains_help(tmp_path: Path) -> None:
    registry = CommandRegistry()
    register_all_commands(registry)

    completions = _completions(InlineCompleter(registry, str(tmp_path)), "/he")

    assert "/help" in [completion.text for completion in completions]


def test_command_completion_replaces_the_whole_token(tmp_path: Path) -> None:
    registry = CommandRegistry()
    register_all_commands(registry)

    completion = _completions(InlineCompleter(registry, str(tmp_path)), "/he")[0]

    assert completion.start_position == -3


def test_command_completion_ignores_commands_with_arguments(tmp_path: Path) -> None:
    registry = CommandRegistry()
    register_all_commands(registry)

    assert _completions(InlineCompleter(registry, str(tmp_path)), "/help now") == []


def test_command_completion_ignores_cursor_in_middle_of_token(tmp_path: Path) -> None:
    registry = CommandRegistry()
    register_all_commands(registry)
    completer = InlineCompleter(registry, str(tmp_path))

    assert _completions_at(completer, "/help", len("/he")) == []


def test_command_completion_allows_whitespace_after_cursor(tmp_path: Path) -> None:
    registry = CommandRegistry()
    register_all_commands(registry)
    completer = InlineCompleter(registry, str(tmp_path))

    completions = _completions_at(completer, "/he next", len("/he"))

    assert "/help" in [completion.text for completion in completions]


def test_command_completion_sanitizes_external_display(tmp_path: Path) -> None:
    registry = CommandRegistry()
    registry.register_sync(
        Command(
            name="unsafe",
            description="clear\x1b[2Jscreen\nforged",
            type=CommandType.LOCAL,
            handler=lambda _context: None,  # type: ignore[arg-type]
        )
    )

    completion = _completions(InlineCompleter(registry, str(tmp_path)), "/un")[0]
    display = "".join(fragment[1] for fragment in to_formatted_text(completion.display))

    assert "\x1b" not in display
    assert "\n" not in display


def test_scan_files_is_sorted_and_skips_internal_directories(tmp_path: Path) -> None:
    (tmp_path / "main.py").write_text("print('ok')", encoding="utf-8")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text("secret", encoding="utf-8")

    assert scan_files(str(tmp_path), "ma") == ["main.py"]


def test_scan_files_sorts_candidates_deterministically(tmp_path: Path) -> None:
    (tmp_path / "b.py").write_text("", encoding="utf-8")
    (tmp_path / "a.py").write_text("", encoding="utf-8")

    assert scan_files(str(tmp_path), "") == ["a.py", "b.py"]


def test_scan_files_marks_directories_and_completes_nested_paths(tmp_path: Path) -> None:
    source = tmp_path / "src"
    source.mkdir()
    (source / "main.py").write_text("", encoding="utf-8")

    assert scan_files(str(tmp_path), "s") == ["src/"]
    assert scan_files(str(tmp_path), "src/ma") == ["src/main.py"]


def test_scan_files_rejects_parent_and_symlink_escape(tmp_path: Path) -> None:
    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("secret", encoding="utf-8")
    (tmp_path / "linked").symlink_to(outside, target_is_directory=True)

    try:
        assert scan_files(str(tmp_path), "../") == []
        assert scan_files(str(tmp_path), "linked/sec") == []
    finally:
        (outside / "secret.txt").unlink()
        outside.rmdir()


def test_scan_files_rejects_symlinked_base_into_hidden_directory(tmp_path: Path) -> None:
    hidden = tmp_path / ".hidden"
    hidden.mkdir()
    (hidden / "secret.txt").write_text("secret", encoding="utf-8")
    (tmp_path / "visible").symlink_to(hidden, target_is_directory=True)

    assert scan_files(str(tmp_path), "visible/") == []


def test_scan_files_rejects_entry_resolving_through_skipped_directory(
    tmp_path: Path,
) -> None:
    dependencies = tmp_path / "node_modules"
    dependencies.mkdir()
    target = dependencies / "internal.py"
    target.write_text("secret", encoding="utf-8")
    (tmp_path / "visible.py").symlink_to(target)

    assert scan_files(str(tmp_path), "vis") == []


def test_scan_files_returns_empty_when_iterdir_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail_iterdir(_path: Path):
        raise OSError("directory unavailable")

    monkeypatch.setattr(Path, "iterdir", fail_iterdir)

    assert scan_files(str(tmp_path), "") == []


def test_scan_files_keeps_existing_results_when_entry_resolve_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "a.py").write_text("", encoding="utf-8")
    (tmp_path / "b.py").write_text("", encoding="utf-8")
    original_resolve = Path.resolve

    def fail_one_resolve(path: Path, strict: bool = False) -> Path:
        if path.name == "b.py":
            raise OSError("entry unavailable")
        return original_resolve(path, strict=strict)

    monkeypatch.setattr(Path, "resolve", fail_one_resolve)

    assert scan_files(str(tmp_path), "") == ["a.py"]


def test_scan_files_skips_terminal_control_characters(tmp_path: Path) -> None:
    (tmp_path / "safe.txt").write_text("", encoding="utf-8")
    (tmp_path / "bad\x1b[2J.txt").write_text("", encoding="utf-8")
    (tmp_path / "bad\nforged.txt").write_text("", encoding="utf-8")

    assert scan_files(str(tmp_path), "") == ["safe.txt"]


def test_scan_files_skips_names_outside_shared_reference_syntax(tmp_path: Path) -> None:
    for name in ["note.txt", "has space.txt", "plus+file.txt", "semi;file.txt", "at@file.txt"]:
        (tmp_path / name).write_text("", encoding="utf-8")

    assert scan_files(str(tmp_path), "") == ["note.txt"]


def test_at_completion_replaces_reference_token(tmp_path: Path) -> None:
    (tmp_path / "main.py").write_text("", encoding="utf-8")
    completer = InlineCompleter(CommandRegistry(), str(tmp_path))

    completion = _completions(completer, "查看 @ma")[0]

    assert completion.text == "@main.py"
    assert completion.start_position == -3


def test_at_completion_ignores_cursor_in_middle_of_token(tmp_path: Path) -> None:
    (tmp_path / "main.py").write_text("", encoding="utf-8")
    completer = InlineCompleter(CommandRegistry(), str(tmp_path))
    text = "查看 @main.py"

    assert _completions_at(completer, text, len("查看 @ma")) == []


def test_at_completion_allows_whitespace_after_cursor(tmp_path: Path) -> None:
    (tmp_path / "main.py").write_text("", encoding="utf-8")
    completer = InlineCompleter(CommandRegistry(), str(tmp_path))
    text = "查看 @ma 后续"

    completions = _completions_at(completer, text, len("查看 @ma"))

    assert "@main.py" in [completion.text for completion in completions]


def test_at_completion_stops_after_whitespace(tmp_path: Path) -> None:
    (tmp_path / "main.py").write_text("", encoding="utf-8")
    completer = InlineCompleter(CommandRegistry(), str(tmp_path))

    assert _completions(completer, "查看 @ma 后续") == []


def test_expand_at_refs_includes_file_content(tmp_path: Path) -> None:
    (tmp_path / "note.txt").write_text("hello", encoding="utf-8")

    expanded = expand_at_refs("查看 @note.txt", str(tmp_path))

    assert "[File: note.txt]" in expanded
    assert "hello" in expanded


def test_expand_at_refs_preserves_sentence_period(tmp_path: Path) -> None:
    (tmp_path / "note.txt").write_text("hello", encoding="utf-8")

    expanded = expand_at_refs("查看 @note.txt.", str(tmp_path))

    assert "[File: note.txt]" in expanded
    assert "hello" in expanded
    assert expanded.endswith("```.")


@pytest.mark.parametrize(
    "text",
    [
        "@foo+bar.txt",
        "@foo@example.com",
    ],
)
def test_expand_at_refs_rejects_unsupported_token_continuation(
    tmp_path: Path, text: str
) -> None:
    (tmp_path / "foo").write_text("must not expand", encoding="utf-8")

    assert expand_at_refs(text, str(tmp_path)) == text


@pytest.mark.parametrize(
    "terminator",
    [
        ",",
        "!",
        "?",
        ";",
        ":",
        ")",
        "]",
        "}",
        "。",
        "，",
        "！",
        "？",
        "；",
        "：",
        "、",
        "）",
        "】",
        "》",
        "」",
        "』",
        "”",
        "’",
    ],
)
def test_expand_at_refs_accepts_sentence_punctuation_and_closing_brackets(
    tmp_path: Path, terminator: str
) -> None:
    (tmp_path / "note.txt").write_text("hello", encoding="utf-8")

    expanded = expand_at_refs(f"@note.txt{terminator}", str(tmp_path))

    assert "[File: note.txt]" in expanded
    assert expanded.endswith(f"```{terminator}")


def test_expand_at_refs_keeps_missing_and_escaped_references(tmp_path: Path) -> None:
    outside = tmp_path.parent / f"{tmp_path.name}-secret.txt"
    outside.write_text("secret", encoding="utf-8")
    (tmp_path / "linked.txt").symlink_to(outside)

    try:
        text = "@missing.txt @../secret.txt @linked.txt"
        assert expand_at_refs(text, str(tmp_path)) == text
    finally:
        outside.unlink()


def test_expand_at_refs_reads_at_most_max_bytes(tmp_path: Path) -> None:
    (tmp_path / "large.txt").write_bytes(b"a" * (MAX_AT_REF_BYTES + 20))

    expanded = expand_at_refs("@large.txt", str(tmp_path))
    content = expanded.split("```\n", 1)[1].rsplit("\n```", 1)[0]

    assert len(content.encode("utf-8")) == MAX_AT_REF_BYTES


def test_expand_at_refs_does_not_load_the_whole_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "large.txt").write_bytes(b"a" * (MAX_AT_REF_BYTES + 20))

    def reject_unbounded_read(_path: Path) -> bytes:
        raise AssertionError("read_bytes loads the whole file")

    monkeypatch.setattr(Path, "read_bytes", reject_unbounded_read)

    assert "[File: large.txt]" in expand_at_refs("@large.txt", str(tmp_path))


def test_expand_at_refs_keeps_reference_when_open_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    note = tmp_path / "note.txt"
    note.write_text("secret", encoding="utf-8")
    original_open = Path.open

    def fail_note_open(path: Path, *args: Any, **kwargs: Any):
        if path == note.resolve():
            raise OSError("file unreadable")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", fail_note_open)

    assert expand_at_refs("查看 @note.txt", str(tmp_path)) == "查看 @note.txt"


def test_expand_at_refs_rejects_fd_path_identity_mismatch_and_closes_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    note = tmp_path / "note.txt"
    note.write_text("secret", encoding="utf-8")
    original_open = Path.open
    real_fstat = references_module.os.fstat
    state = {"closed": False, "read": False}

    class TrackingFile:
        def __init__(self, wrapped: Any) -> None:
            self.wrapped = wrapped

        def __enter__(self) -> "TrackingFile":
            return self

        def __exit__(self, *_args: Any) -> None:
            self.wrapped.close()
            state["closed"] = True

        def fileno(self) -> int:
            return self.wrapped.fileno()

        def read(self, size: int) -> bytes:
            state["read"] = True
            return self.wrapped.read(size)

    def tracking_open(path: Path, *args: Any, **kwargs: Any) -> Any:
        opened = original_open(path, *args, **kwargs)
        return TrackingFile(opened) if path == note.resolve() else opened

    def mismatched_fstat(fd: int) -> Any:
        stat = real_fstat(fd)
        return SimpleNamespace(st_dev=stat.st_dev, st_ino=stat.st_ino + 1)

    monkeypatch.setattr(Path, "open", tracking_open)
    monkeypatch.setattr(references_module.os, "fstat", mismatched_fstat)

    assert expand_at_refs("@note.txt", str(tmp_path)) == "@note.txt"
    assert state == {"closed": True, "read": False}


def test_expand_at_refs_rejects_directory_swap_to_external_symlink(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    directory = tmp_path / "docs"
    directory.mkdir()
    canonical_note = directory / "note.txt"
    canonical_note.write_text("safe", encoding="utf-8")
    moved_directory = tmp_path / "docs-safe"
    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    outside.mkdir()
    (outside / "note.txt").write_text("external secret", encoding="utf-8")
    original_open = Path.open
    swapped = False

    def swap_then_open(path: Path, *args: Any, **kwargs: Any) -> Any:
        nonlocal swapped
        if path == canonical_note and not swapped:
            swapped = True
            directory.rename(moved_directory)
            directory.symlink_to(outside, target_is_directory=True)
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", swap_then_open)

    try:
        assert expand_at_refs("@docs/note.txt", str(tmp_path)) == "@docs/note.txt"
    finally:
        if directory.is_symlink():
            directory.unlink()
        (outside / "note.txt").unlink()
        outside.rmdir()


def test_inline_prompt_session_configures_history_completer_and_bindings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, Any] = {}

    class CapturingPromptSession:
        def __init__(self, **kwargs: Any) -> None:
            captured.update(kwargs)

    monkeypatch.setattr(session_module, "PromptSession", CapturingPromptSession)
    history_path = tmp_path / "missing" / "history"

    InlinePromptSession(CommandRegistry(), str(tmp_path), str(history_path))

    assert history_path.parent.is_dir()
    assert isinstance(captured["history"], FileHistory)
    assert isinstance(captured["completer"], InlineCompleter)
    assert captured["complete_while_typing"] is True
    assert captured["multiline"] is True
    assert _binding_handler(captured["key_bindings"], Keys.ControlM)
    assert _binding_handler(captured["key_bindings"], Keys.Escape, Keys.ControlM)


@pytest.mark.asyncio
async def test_inline_prompt_key_bindings_submit_newline_and_toggle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, Any] = {}
    callback_calls: list[str] = []
    terminal_calls: list[Callable[[], Any]] = []
    terminal_tasks: list[asyncio.Task[None]] = []

    class CapturingPromptSession:
        def __init__(self, **kwargs: Any) -> None:
            captured.update(kwargs)

    def fake_run_in_terminal(callback: Callable[[], Any]) -> asyncio.Task[None]:
        terminal_calls.append(callback)

        async def run_callback() -> None:
            callback()

        task = asyncio.create_task(run_callback())
        terminal_tasks.append(task)
        return task

    def reject_second_schedule(_pending: object) -> None:
        raise AssertionError("run_in_terminal already schedules its task")

    monkeypatch.setattr(session_module, "PromptSession", CapturingPromptSession)
    monkeypatch.setattr(session_module, "run_in_terminal", fake_run_in_terminal)
    InlinePromptSession(
        CommandRegistry(),
        str(tmp_path),
        str(tmp_path / "history"),
        on_toggle_details=lambda: callback_calls.append("toggle"),
    )
    bindings = captured["key_bindings"]
    buffer = SimpleNamespace(
        validate_and_handle=lambda: callback_calls.append("submit"),
        insert_text=lambda text: callback_calls.append(text),
    )
    event = SimpleNamespace(
        current_buffer=buffer,
        app=SimpleNamespace(create_background_task=reject_second_schedule),
    )

    _binding_handler(bindings, Keys.ControlM)(event)
    _binding_handler(bindings, Keys.Escape, Keys.ControlM)(event)
    _binding_handler(bindings, Keys.ControlO)(event)
    await terminal_tasks[0]

    assert callback_calls == ["submit", "\n", "toggle"]
    assert terminal_calls


@pytest.mark.asyncio
async def test_inline_prompt_and_text_facade_trim_and_sanitize(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[dict[str, Any]] = []
    fake = FakePromptSession(["  message  ", "  answer  "], calls)
    facade = InlinePromptSession(CommandRegistry(), str(tmp_path), str(tmp_path / "history"))
    facade._session = fake

    prompt_result = await facade.prompt("ready\x1b[2J\nforged")
    answer = await facade.ask_text("Name\x1b[2J")

    assert prompt_result == "  message  "
    assert answer == "answer"
    prompt_fragments = to_formatted_text(calls[0]["args"][0])
    toolbar_fragments = to_formatted_text(calls[0]["kwargs"]["bottom_toolbar"])
    assert prompt_fragments == [("class:cyan", "❯ ")]
    assert "\x1b" not in "".join(fragment[1] for fragment in toolbar_fragments)
    assert "\n" not in "".join(fragment[1] for fragment in toolbar_fragments)
    assert calls[0]["kwargs"]["completer"] is facade._chat_completer
    assert calls[0]["kwargs"]["complete_while_typing"] is True
    assert calls[0]["kwargs"]["multiline"] is True
    assert calls[1]["kwargs"]["multiline"] is False


@pytest.mark.asyncio
async def test_prompt_restores_real_prompt_session_state_after_choose(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    facade = InlinePromptSession(CommandRegistry(), str(tmp_path), str(tmp_path / "history"))
    prompt_session = facade._session
    responses = iter(["one", "message"])

    async def fake_run_async(**_kwargs: Any) -> str:
        return next(responses)

    monkeypatch.setattr(prompt_session.app, "run_async", fake_run_async)
    prompt_session._output = object()

    assert await facade.choose("Provider", ["one", "two"]) == "one"
    assert prompt_session.completer is not facade._chat_completer
    assert prompt_session.multiline is False

    assert await facade.prompt("ready") == "message"
    assert prompt_session.completer is facade._chat_completer
    assert prompt_session.complete_while_typing is True
    assert prompt_session.multiline is True


@pytest.mark.asyncio
async def test_choose_requires_case_sensitive_exact_match(
    tmp_path: Path,
) -> None:
    calls: list[dict[str, Any]] = []
    facade = InlinePromptSession(CommandRegistry(), str(tmp_path), str(tmp_path / "history"))
    facade._session = FakePromptSession([" ONE ", " two "], calls)

    selected = await facade.choose("Provider", ["one", "two"])

    assert selected == "two"
    assert len(calls) == 2
    assert calls[0]["kwargs"]["multiline"] is False


@pytest.mark.asyncio
async def test_choose_sanitizes_external_choice_display(tmp_path: Path) -> None:
    calls: list[dict[str, Any]] = []
    facade = InlinePromptSession(CommandRegistry(), str(tmp_path), str(tmp_path / "history"))
    facade._session = FakePromptSession(["safe"], calls)

    await facade.choose("Provider", ["safe", "bad\x1b[2Jname"])

    completer = calls[0]["kwargs"]["completer"]
    displays = [
        "".join(fragment[1] for fragment in to_formatted_text(completion.display))
        for completion in completer.get_completions(Document("", 0), None)
    ]
    assert all("\x1b" not in display for display in displays)


@pytest.mark.asyncio
async def test_choose_many_validates_all_values_and_preserves_case(
    tmp_path: Path,
) -> None:
    calls: list[dict[str, Any]] = []
    facade = InlinePromptSession(CommandRegistry(), str(tmp_path), str(tmp_path / "history"))
    facade._session = FakePromptSession([" ", "one,missing", " one, Two "], calls)

    selected = await facade.choose_many("Tools", ["one", "Two"])

    assert selected == ["one", "Two"]
    assert len(calls) == 3


@pytest.mark.asyncio
async def test_select_provider_uses_injected_async_chooser() -> None:
    providers = [_provider("one"), _provider("two")]
    received: list[list[str]] = []

    async def chooser(choices: list[str]) -> str:
        received.append(choices)
        return "two"

    selected = await select_provider(providers, chooser)

    assert received == [["one", "two"]]
    assert selected is providers[1]


@pytest.mark.asyncio
async def test_select_provider_rejects_empty_list() -> None:
    with pytest.raises(ValueError, match="provider"):
        await select_provider([])


@pytest.mark.asyncio
async def test_select_provider_returns_single_provider_without_prompting() -> None:
    provider = _provider("only")

    async def chooser(_choices: list[str]) -> str:
        raise AssertionError("chooser should not be called")

    assert await select_provider([provider], chooser) is provider


@pytest.mark.asyncio
async def test_select_provider_default_prompt_retries_until_exact_match(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []
    fake = FakePromptSession(["ONE", "two"], calls)
    monkeypatch.setattr(session_module, "PromptSession", lambda: fake)
    providers = [_provider("one"), _provider("two")]

    selected = await select_provider(providers)

    assert selected is providers[1]
    assert len(calls) == 2
    assert calls[0]["kwargs"]["multiline"] is False
