from collections.abc import Awaitable, Callable
import logging

from lantu.agent import (
    AgentEvent,
    CompactNotification,
    ErrorEvent,
    HookEvent,
    LoopComplete,
    PermissionRequest,
    RetryEvent,
    StreamText,
    ThinkingText,
    ToolResultEvent,
    ToolUseEvent,
    TurnComplete,
    UsageEvent,
)
from lantu.ui.inline.live import LiveRenderer
from lantu.ui.inline.transcript import TranscriptRenderer
from lantu.ui.shared.models import LiveViewState, ToolStatus, ToolViewState


log = logging.getLogger(__name__)

PermissionHandler = Callable[[PermissionRequest], Awaitable[None]]


class InlineEventHandler:
    def __init__(
        self,
        live: LiveRenderer,
        transcript: TranscriptRenderer,
        permission_handler: PermissionHandler | None = None,
    ) -> None:
        self.live = live
        self.transcript = transcript
        self.permission_handler = permission_handler
        self.state = LiveViewState(status_text="正在思考")
        self.last_tool: ToolViewState | None = None
        self._completed_tool_ids: set[str] = set()

    def _commit_assistant(self) -> None:
        content = self.state.assistant_text.strip()
        if content:
            self.live.stop()
            self.transcript.assistant_message(content)
        self.state.assistant_text = ""

    async def handle(self, event: AgentEvent) -> None:
        if isinstance(event, StreamText):
            self.state.assistant_text += event.text
        elif isinstance(event, ThinkingText):
            self.state.thinking_text += event.text
        elif isinstance(event, ToolUseEvent):
            self._commit_assistant()
            self._completed_tool_ids.discard(event.tool_id)
            self.state.tools[event.tool_id] = ToolViewState(
                tool_id=event.tool_id,
                name=event.tool_name,
                arguments=event.arguments,
            )
        elif isinstance(event, ToolResultEvent):
            if event.tool_id not in self._completed_tool_ids:
                tool = self.state.tools.pop(
                    event.tool_id,
                    ToolViewState(event.tool_id, event.tool_name, {}),
                )
                tool.status = ToolStatus.ERROR if event.is_error else ToolStatus.SUCCESS
                tool.output = event.output
                tool.elapsed = event.elapsed
                self.last_tool = tool
                self._completed_tool_ids.add(event.tool_id)
                self.live.stop()
                self.transcript.tool(tool)
        elif isinstance(event, UsageEvent):
            self.state.input_tokens = event.input_tokens
            self.state.output_tokens = event.output_tokens
        elif isinstance(event, RetryEvent):
            self.live.stop()
            self.transcript.system_message(f"↻ Retrying: {event.reason}")
        elif isinstance(event, HookEvent):
            symbol = "✓" if event.success else "✗"
            self.live.stop()
            self.transcript.system_message(
                f"Hook [{event.hook_id}] {symbol} {event.output}"
            )
        elif isinstance(event, CompactNotification):
            self.live.stop()
            self.transcript.system_message(event.message)
        elif isinstance(event, ErrorEvent):
            self._commit_assistant()
            self.live.stop()
            self.transcript.error_message(event.message)
        elif isinstance(event, PermissionRequest):
            if self.permission_handler is None:
                raise RuntimeError("Permission handler is not configured")
            self.live.stop()
            await self.permission_handler(event)
        elif isinstance(event, LoopComplete):
            self.finish()
            return
        elif isinstance(event, TurnComplete):
            return
        else:
            log.debug("Unknown agent event: %r", event)

        if (
            self.state.assistant_text
            or self.state.thinking_text
            or self.state.tools
        ):
            self.live.update(self.state)

    def finish(self) -> None:
        self._commit_assistant()
        self.state.thinking_text = ""
        self.state.tools.clear()
        self.live.stop()
