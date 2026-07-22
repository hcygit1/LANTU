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
            candidate = self.live_factory(
                renderable,
                console=self.console,
                refresh_per_second=12,
                transient=True,
            )
            try:
                candidate.start(refresh=True)
            except Exception:
                self._best_effort_stop(candidate)
                raise
            self._live = candidate
            return

        live = self._live
        try:
            live.update(renderable, refresh=True)
        except Exception:
            self._live = None
            self._best_effort_stop(live)
            raise

    def stop(self) -> None:
        if self._live is None:
            return

        live = self._live
        self._live = None
        live.stop()

    @staticmethod
    def _best_effort_stop(live: Any) -> None:
        try:
            live.stop()
        except Exception:
            pass
