from rich.console import Console, RenderableType

from lantu.ui.inline.components.header import render_header
from lantu.ui.inline.components.message import (
    render_assistant_message,
    render_error_message,
    render_system_message,
    render_user_message,
)
from lantu.ui.inline.components.tool import render_tool, render_tool_details
from lantu.ui.shared.models import ToolViewState


class TranscriptRenderer:
    def __init__(self, console: Console) -> None:
        self.console = console

    def commit(self, renderable: RenderableType, *, blank_after: bool = True) -> None:
        self.console.print(renderable)
        if blank_after:
            self.console.print()

    def header(
        self,
        model: str,
        mode: str,
        work_dir: str,
        version: str | None = None,
    ) -> None:
        encoding = (getattr(self.console.file, "encoding", None) or "utf-8").lower()
        self.commit(
            render_header(
                model,
                mode,
                work_dir,
                version=version,
                width=self.console.size.width,
                unicode="utf" in encoding,
            )
        )

    def user_message(self, content: str) -> None:
        self.commit(render_user_message(content))

    def assistant_message(self, content: str) -> None:
        self.commit(render_assistant_message(content))

    def system_message(self, content: str) -> None:
        self.commit(render_system_message(content))

    def error_message(self, content: str) -> None:
        self.commit(render_error_message(content))

    def tool(self, state: ToolViewState) -> None:
        self.commit(render_tool(state))

    def tool_details(self, state: ToolViewState) -> None:
        self.commit(render_tool_details(state))

    def clear_boundary(self) -> None:
        self.console.rule("新会话", style="dim")
