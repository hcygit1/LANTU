from collections.abc import Callable
from typing import Any

from rich.console import Console
from rich.live import Live

from lantu.ui.inline.components.status import render_live_state
from lantu.ui.shared.models import LiveViewState


class LiveRenderer:
    def __init__(
        self,
        console: Console,
        live_factory: Callable[..., Any] = Live,
    ) -> None:
        self.console = console
        self.live_factory = live_factory
        self._live: Any | None = None

    def update(self, state: LiveViewState) -> None:
        renderable = render_live_state(state)
        if self._live is None:
            self._live = self.live_factory(
                renderable,
                console=self.console,
                refresh_per_second=12,
                transient=True,
            )
            self._live.start(refresh=True)
            return

        self._live.update(renderable, refresh=True)

    def stop(self) -> None:
        if self._live is None:
            return

        self._live.stop()
        self._live = None
