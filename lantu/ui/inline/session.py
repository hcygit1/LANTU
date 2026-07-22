from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable
from html import escape
from pathlib import Path
from typing import Any

from prompt_toolkit import HTML, PromptSession
from prompt_toolkit.application import run_in_terminal
from prompt_toolkit.completion import Completer, Completion, WordCompleter
from prompt_toolkit.document import Document
from prompt_toolkit.history import FileHistory
from prompt_toolkit.key_binding import KeyBindings

from lantu.commands.parser import complete
from lantu.commands.registry import CommandRegistry
from lantu.config import ProviderConfig
from lantu.ui.shared.formatting import sanitize_terminal_text
from lantu.ui.shared.references import scan_files


def _word_completer(choices: list[str]) -> WordCompleter:
    return WordCompleter(
        choices,
        display_dict={choice: sanitize_terminal_text(choice) for choice in choices},
    )


class InlineCompleter(Completer):
    def __init__(self, registry: CommandRegistry, work_dir: str) -> None:
        self.registry = registry
        self.work_dir = work_dir

    def get_completions(
        self, document: Document, complete_event: Any
    ) -> Iterable[Completion]:
        text = document.text_before_cursor
        if text.startswith("/") and not any(character.isspace() for character in text):
            for display, value in complete(self.registry, text):
                if sanitize_terminal_text(value) != value:
                    continue
                yield Completion(
                    value,
                    start_position=-len(text),
                    display=sanitize_terminal_text(display),
                )
            return

        at_index = text.rfind("@")
        if at_index < 0:
            return
        prefix = text[at_index + 1 :]
        if any(character.isspace() for character in prefix):
            return
        for path in scan_files(self.work_dir, prefix):
            yield Completion(
                "@" + path,
                start_position=-(len(prefix) + 1),
            )


class InlinePromptSession:
    def __init__(
        self,
        registry: CommandRegistry,
        work_dir: str,
        history_path: str,
        on_toggle_details: Callable[[], Any] | None = None,
    ) -> None:
        history_file = Path(history_path).expanduser()
        history_file.parent.mkdir(parents=True, exist_ok=True)

        bindings = KeyBindings()

        @bindings.add("enter")
        def submit(event: Any) -> None:
            event.current_buffer.validate_and_handle()

        @bindings.add("escape", "enter")
        def newline(event: Any) -> None:
            event.current_buffer.insert_text("\n")

        if on_toggle_details is not None:

            @bindings.add("c-o")
            def toggle_details(event: Any) -> None:
                pending = run_in_terminal(on_toggle_details)
                event.app.create_background_task(pending)

        self._session = PromptSession(
            history=FileHistory(str(history_file)),
            completer=InlineCompleter(registry, work_dir),
            complete_while_typing=True,
            multiline=True,
            key_bindings=bindings,
        )

    async def prompt(self, status: str) -> str:
        safe_status = escape(sanitize_terminal_text(status))
        return await self._session.prompt_async(
            HTML("<cyan>❯ </cyan>"),
            bottom_toolbar=HTML(f"<dim>{safe_status}</dim>"),
        )

    async def choose(self, label: str, choices: list[str]) -> str:
        if not choices:
            raise ValueError("choices must not be empty")
        completer = _word_completer(choices)
        prompt = sanitize_terminal_text(label) + ": "
        while True:
            answer = (
                await self._session.prompt_async(
                    prompt,
                    completer=completer,
                    complete_while_typing=True,
                    multiline=False,
                )
            ).strip()
            if answer in choices:
                return answer

    async def ask_text(self, label: str) -> str:
        answer = await self._session.prompt_async(
            sanitize_terminal_text(label) + ": ",
            multiline=False,
        )
        return answer.strip()

    async def choose_many(self, label: str, choices: list[str]) -> list[str]:
        if not choices:
            raise ValueError("choices must not be empty")
        completer = _word_completer(choices)
        prompt = sanitize_terminal_text(label) + ": "
        while True:
            answer = await self._session.prompt_async(
                prompt,
                completer=completer,
                complete_while_typing=True,
                multiline=False,
            )
            selected = [item.strip() for item in answer.split(",") if item.strip()]
            if selected and all(item in choices for item in selected):
                return selected


Chooser = Callable[[list[str]], Awaitable[str]]


async def select_provider(
    providers: list[ProviderConfig], chooser: Chooser | None = None
) -> ProviderConfig:
    if not providers:
        raise ValueError("at least one provider is required")
    if len(providers) == 1:
        return providers[0]

    names = [provider.name for provider in providers]
    if chooser is not None:
        selected = await chooser(names)
        for provider in providers:
            if provider.name == selected:
                return provider
        raise ValueError(f"unknown provider: {selected}")

    prompt_session = PromptSession()
    completer = _word_completer(names)
    while True:
        selected = (
            await prompt_session.prompt_async(
                "Provider: ",
                completer=completer,
                complete_while_typing=True,
                multiline=False,
            )
        ).strip()
        for provider in providers:
            if provider.name == selected:
                return provider
