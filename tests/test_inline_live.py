import asyncio
from copy import deepcopy
import logging

import pytest

from lantu.agent import (
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
from lantu.ui.inline.event_handler import InlineEventHandler
from lantu.ui.inline.live import LiveRenderer
from lantu.ui.shared.models import LiveViewState, ToolStatus


class FakeRichLive:
    def __init__(self, renderable, **kwargs):
        self.renderable = renderable
        self.kwargs = kwargs
        self.start_calls = []
        self.update_calls = []
        self.stop_calls = 0
        self.start_error = None
        self.update_error = None
        self.stop_error = None

    def start(self, *, refresh):
        self.start_calls.append(refresh)
        if self.start_error is not None:
            raise self.start_error

    def update(self, renderable, *, refresh):
        self.update_calls.append((renderable, refresh))
        if self.update_error is not None:
            raise self.update_error

    def stop(self):
        self.stop_calls += 1
        if self.stop_error is not None:
            raise self.stop_error


class FakeLiveFactory:
    def __init__(self):
        self.instances = []

    def __call__(self, renderable, **kwargs):
        instance = FakeRichLive(renderable, **kwargs)
        self.instances.append(instance)
        return instance


class ConfiguredLiveFactory(FakeLiveFactory):
    def __init__(self, *configs):
        super().__init__()
        self.configs = configs

    def __call__(self, renderable, **kwargs):
        instance = super().__call__(renderable, **kwargs)
        config = self.configs[len(self.instances) - 1]
        instance.start_error = config.get("start_error")
        instance.update_error = config.get("update_error")
        instance.stop_error = config.get("stop_error")
        return instance


class FakeLiveRenderer:
    def __init__(self, events=None):
        self.states = []
        self.stop_calls = 0
        self.events = events

    def update(self, state):
        self.states.append(deepcopy(state))
        if self.events is not None:
            self.events.append("live.update")

    def stop(self):
        self.stop_calls += 1
        if self.events is not None:
            self.events.append("live.stop")


class FakeTranscript:
    def __init__(self, events=None):
        self.assistant = []
        self.tools = []
        self.system = []
        self.errors = []
        self.events = events

    def assistant_message(self, content):
        self.assistant.append(content)
        if self.events is not None:
            self.events.append(f"assistant:{content}")

    def tool(self, state):
        self.tools.append(state)
        if self.events is not None:
            self.events.append(f"tool:{state.tool_id}")

    def system_message(self, content):
        self.system.append(content)
        if self.events is not None:
            self.events.append(f"system:{content}")

    def error_message(self, content):
        self.errors.append(content)
        if self.events is not None:
            self.events.append(f"error:{content}")


def test_live_renderer_is_lazy_and_stop_is_idempotent():
    console = object()
    factory = FakeLiveFactory()
    renderer = LiveRenderer(console, live_factory=factory)

    assert factory.instances == []

    renderer.update(LiveViewState(assistant_text="第一段"))

    assert len(factory.instances) == 1
    first = factory.instances[0]
    assert first.kwargs == {
        "console": console,
        "refresh_per_second": 12,
        "transient": True,
    }
    assert first.start_calls == [True]
    assert first.update_calls == []

    renderer.update(LiveViewState(assistant_text="第二段"))
    assert len(factory.instances) == 1
    assert first.update_calls[0][1] is True

    renderer.stop()
    renderer.stop()
    assert first.stop_calls == 1

    renderer.update(LiveViewState(thinking_text="重新开始"))
    assert len(factory.instances) == 2


def test_live_renderer_recovers_after_start_and_cleanup_failures():
    factory = ConfiguredLiveFactory(
        {
            "start_error": RuntimeError("start failed"),
            "stop_error": RuntimeError("cleanup failed"),
        },
        {},
    )
    renderer = LiveRenderer(object(), live_factory=factory)

    with pytest.raises(RuntimeError, match="start failed"):
        renderer.update(LiveViewState(assistant_text="失败"))

    assert renderer._live is None
    assert factory.instances[0].stop_calls == 1

    renderer.update(LiveViewState(assistant_text="恢复"))
    assert renderer._live is factory.instances[1]
    assert factory.instances[1].start_calls == [True]


def test_live_renderer_cleans_candidate_when_start_is_interrupted():
    factory = ConfiguredLiveFactory(
        {"start_error": KeyboardInterrupt("start interrupted")},
    )
    renderer = LiveRenderer(object(), live_factory=factory)

    with pytest.raises(KeyboardInterrupt, match="start interrupted"):
        renderer.update(LiveViewState(assistant_text="失败"))

    assert renderer._live is None
    assert factory.instances[0].stop_calls == 1


def test_live_renderer_recovers_after_existing_live_update_is_interrupted():
    factory = ConfiguredLiveFactory(
        {"update_error": KeyboardInterrupt("update interrupted")},
        {},
    )
    renderer = LiveRenderer(object(), live_factory=factory)
    renderer.update(LiveViewState(assistant_text="第一段"))

    with pytest.raises(KeyboardInterrupt, match="update interrupted"):
        renderer.update(LiveViewState(assistant_text="第二段"))

    assert renderer._live is None
    assert factory.instances[0].stop_calls == 1

    renderer.update(LiveViewState(assistant_text="第三段"))
    assert renderer._live is factory.instances[1]
    assert factory.instances[1].start_calls == [True]


def test_live_renderer_cleanup_interrupt_does_not_replace_original_error():
    original = RuntimeError("start failed")
    factory = ConfiguredLiveFactory(
        {
            "start_error": original,
            "stop_error": KeyboardInterrupt("cleanup interrupted"),
        },
    )
    renderer = LiveRenderer(object(), live_factory=factory)

    with pytest.raises(RuntimeError, match="start failed") as captured:
        renderer.update(LiveViewState(assistant_text="失败"))

    assert captured.value is original
    assert renderer._live is None
    assert factory.instances[0].stop_calls == 1


def test_live_renderer_recovers_after_existing_live_update_failure():
    factory = ConfiguredLiveFactory(
        {"update_error": RuntimeError("update failed")},
        {},
    )
    renderer = LiveRenderer(object(), live_factory=factory)
    renderer.update(LiveViewState(assistant_text="第一段"))

    with pytest.raises(RuntimeError, match="update failed"):
        renderer.update(LiveViewState(assistant_text="第二段"))

    assert renderer._live is None
    assert factory.instances[0].stop_calls == 1

    renderer.update(LiveViewState(assistant_text="第三段"))
    assert renderer._live is factory.instances[1]
    assert factory.instances[1].start_calls == [True]


def test_live_renderer_recovers_after_stop_failure():
    factory = ConfiguredLiveFactory(
        {"stop_error": RuntimeError("stop failed")},
        {},
    )
    renderer = LiveRenderer(object(), live_factory=factory)
    renderer.update(LiveViewState(assistant_text="第一段"))

    with pytest.raises(RuntimeError, match="stop failed"):
        renderer.stop()

    assert renderer._live is None

    renderer.update(LiveViewState(assistant_text="第二段"))
    assert renderer._live is factory.instances[1]
    assert factory.instances[1].start_calls == [True]


@pytest.mark.asyncio
async def test_tool_use_commits_buffered_assistant_and_starts_running_tool():
    live = FakeLiveRenderer()
    transcript = FakeTranscript()
    handler = InlineEventHandler(live, transcript)

    await handler.handle(StreamText("先读取文件"))
    await handler.handle(ToolUseEvent("ReadFile", "t1", {"file_path": "a.py"}))

    assert transcript.assistant == ["先读取文件"]
    assert handler.state.assistant_text == ""
    assert handler.state.tools["t1"].status is ToolStatus.RUNNING
    assert live.states[-1].tools["t1"].status is ToolStatus.RUNNING


@pytest.mark.asyncio
async def test_tool_result_is_committed_once_and_loop_complete_finishes():
    live = FakeLiveRenderer()
    transcript = FakeTranscript()
    handler = InlineEventHandler(live, transcript)

    await handler.handle(ToolUseEvent("ReadFile", "t1", {"file_path": "a.py"}))
    await handler.handle(ToolResultEvent("t1", "ReadFile", "a\nb\n", False, 0.1))
    await handler.handle(LoopComplete(1))

    assert len(transcript.tools) == 1
    tool = transcript.tools[0]
    assert tool.status is ToolStatus.SUCCESS
    assert tool.output == "a\nb\n"
    assert tool.elapsed == 0.1
    assert handler.last_tool is tool
    assert handler.state.tools == {}
    assert live.stop_calls >= 1


@pytest.mark.asyncio
async def test_tool_phase_does_not_restore_completed_thinking():
    live = FakeLiveRenderer()
    handler = InlineEventHandler(live, FakeTranscript())

    await handler.handle(ThinkingText("已完成推理"))
    await handler.handle(ToolUseEvent("ReadFile", "t1", {"file_path": "a.py"}))

    assert handler.state.thinking_text == ""
    assert live.states[-1].thinking_text == ""
    update_count = len(live.states)

    await handler.handle(ToolResultEvent("t1", "ReadFile", "ok", False, 0.1))

    assert handler.state.thinking_text == ""
    assert len(live.states) == update_count


@pytest.mark.asyncio
async def test_stream_thinking_and_usage_update_live_state_snapshots():
    live = FakeLiveRenderer()
    handler = InlineEventHandler(live, FakeTranscript())

    await handler.handle(StreamText("答"))
    await handler.handle(ThinkingText("思"))
    await handler.handle(UsageEvent(120, 45))

    assert [state.assistant_text for state in live.states[:2]] == ["答", "答"]
    assert live.states[0].thinking_text == ""
    assert live.states[1].thinking_text == "思"
    assert live.states[-1].input_tokens == 120
    assert live.states[-1].output_tokens == 45


@pytest.mark.asyncio
async def test_usage_without_dynamic_content_does_not_create_live_region():
    live = FakeLiveRenderer()
    handler = InlineEventHandler(live, FakeTranscript())

    await handler.handle(UsageEvent(10, 3))

    assert handler.state.input_tokens == 10
    assert handler.state.output_tokens == 3
    assert live.states == []


@pytest.mark.asyncio
async def test_static_system_events_stop_live_before_writing_transcript():
    events = []
    live = FakeLiveRenderer(events)
    transcript = FakeTranscript(events)
    handler = InlineEventHandler(live, transcript)

    await handler.handle(RetryEvent("网络繁忙"))
    await handler.handle(HookEvent("format", "post_tool", "完成", True))
    await handler.handle(HookEvent("lint", "post_tool", "失败", False))
    await handler.handle(CompactNotification(1000, "上下文已压缩"))

    assert transcript.system == [
        "↻ Retrying: 网络繁忙",
        "Hook [format] ✓ 完成",
        "Hook [lint] ✗ 失败",
        "上下文已压缩",
    ]
    assert events == [
        "live.stop",
        "system:↻ Retrying: 网络繁忙",
        "live.stop",
        "system:Hook [format] ✓ 完成",
        "live.stop",
        "system:Hook [lint] ✗ 失败",
        "live.stop",
        "system:上下文已压缩",
    ]


@pytest.mark.asyncio
async def test_error_commits_assistant_and_turn_complete_does_not_repeat_messages():
    events = []
    live = FakeLiveRenderer(events)
    transcript = FakeTranscript(events)
    handler = InlineEventHandler(live, transcript)

    await handler.handle(StreamText("  部分回答  "))
    await handler.handle(ErrorEvent("请求失败"))
    await handler.handle(TurnComplete(1))

    assert transcript.assistant == ["部分回答"]
    assert transcript.errors == ["请求失败"]
    assert events[-5:] == [
        "live.stop",
        "assistant:部分回答",
        "live.stop",
        "error:请求失败",
        "live.stop",
    ]


@pytest.mark.asyncio
async def test_error_clears_dynamic_state_without_restarting_live():
    events = []
    live = FakeLiveRenderer(events)
    transcript = FakeTranscript(events)
    handler = InlineEventHandler(live, transcript)

    await handler.handle(ToolUseEvent("Bash", "t1", {"command": "pwd"}))
    await handler.handle(ThinkingText("过期推理"))
    await handler.handle(StreamText("  部分回答  "))
    events.clear()
    update_count = len(live.states)

    await handler.handle(ErrorEvent("请求失败"))

    assert transcript.assistant == ["部分回答"]
    assert transcript.errors == ["请求失败"]
    assert handler.state.assistant_text == ""
    assert handler.state.thinking_text == ""
    assert handler.state.tools == {}
    assert len(live.states) == update_count
    assert events == [
        "live.stop",
        "assistant:部分回答",
        "live.stop",
        "error:请求失败",
    ]


@pytest.mark.asyncio
async def test_turn_complete_clears_thinking_and_stops_expired_live():
    live = FakeLiveRenderer()
    transcript = FakeTranscript()
    handler = InlineEventHandler(live, transcript)

    await handler.handle(ThinkingText("本轮推理"))
    stop_count = live.stop_calls

    await handler.handle(TurnComplete(1))

    assert handler.state.thinking_text == ""
    assert transcript.assistant == []
    assert live.stop_calls == stop_count + 1


@pytest.mark.asyncio
async def test_turn_complete_does_not_submit_an_active_assistant_buffer():
    live = FakeLiveRenderer()
    transcript = FakeTranscript()
    handler = InlineEventHandler(live, transcript)

    await handler.handle(StreamText("尚未结束"))
    await handler.handle(ThinkingText("本轮推理"))
    update_count = len(live.states)
    stop_count = live.stop_calls
    await handler.handle(TurnComplete(1))

    assert transcript.assistant == []
    assert handler.state.assistant_text == "尚未结束"
    assert handler.state.thinking_text == ""
    assert live.stop_calls == stop_count + 1
    assert len(live.states) == update_count + 1
    assert live.states[-1].assistant_text == "尚未结束"
    assert live.states[-1].thinking_text == ""


@pytest.mark.asyncio
async def test_permission_requires_handler():
    events = []
    request = PermissionRequest("Bash", "运行命令", asyncio.get_running_loop().create_future())
    handler = InlineEventHandler(FakeLiveRenderer(events), FakeTranscript(events))

    with pytest.raises(RuntimeError, match="Permission handler is not configured"):
        await handler.handle(request)

    assert events == ["live.stop"]


@pytest.mark.asyncio
async def test_permission_stops_live_before_awaiting_callback():
    events = []
    request = PermissionRequest("Bash", "运行命令", asyncio.get_running_loop().create_future())

    async def allow(received):
        assert received is request
        events.append("permission")

    handler = InlineEventHandler(
        FakeLiveRenderer(events),
        FakeTranscript(events),
        permission_handler=allow,
    )
    await handler.handle(StreamText("等待授权"))
    events.clear()

    await handler.handle(request)

    assert events == ["live.stop", "permission", "live.update"]


@pytest.mark.asyncio
async def test_unknown_event_is_logged_without_interrupting(caplog):
    handler = InlineEventHandler(FakeLiveRenderer(), FakeTranscript())

    with caplog.at_level(logging.DEBUG, logger="lantu.ui.inline.event_handler"):
        await handler.handle(object())

    assert "Unknown agent event" in caplog.text


@pytest.mark.asyncio
async def test_duplicate_tool_result_is_ignored_but_reused_id_is_a_new_call():
    live = FakeLiveRenderer()
    transcript = FakeTranscript()
    handler = InlineEventHandler(live, transcript)

    await handler.handle(ToolUseEvent("ReadFile", "t1", {"file_path": "a.py"}))
    first_result = ToolResultEvent("t1", "ReadFile", "first", False, 0.1)
    await handler.handle(first_result)
    await handler.handle(first_result)

    assert len(transcript.tools) == 1
    first_tool = transcript.tools[0]

    await handler.handle(ToolUseEvent("ReadFile", "t1", {"file_path": "b.py"}))
    await handler.handle(ToolResultEvent("t1", "ReadFile", "second", True, 0.2))

    assert len(transcript.tools) == 2
    second_tool = transcript.tools[1]
    assert first_tool is not second_tool
    assert first_tool.arguments == {"file_path": "a.py"}
    assert second_tool.arguments == {"file_path": "b.py"}
    assert second_tool.status is ToolStatus.ERROR
    assert handler.last_tool is second_tool


@pytest.mark.asyncio
async def test_unknown_tool_result_uses_fallback_and_is_only_submitted_once():
    transcript = FakeTranscript()
    handler = InlineEventHandler(FakeLiveRenderer(), transcript)
    result = ToolResultEvent("missing", "Search", "found", False, 0.3)

    await handler.handle(result)
    await handler.handle(result)

    assert len(transcript.tools) == 1
    tool = transcript.tools[0]
    assert tool.tool_id == "missing"
    assert tool.name == "Search"
    assert tool.arguments == {}
    assert tool.status is ToolStatus.SUCCESS


@pytest.mark.asyncio
async def test_finish_commits_assistant_and_clears_dynamic_state():
    live = FakeLiveRenderer()
    transcript = FakeTranscript()
    handler = InlineEventHandler(live, transcript)

    await handler.handle(StreamText("  最终回答  "))
    await handler.handle(ThinkingText("内部思考"))
    await handler.handle(ToolUseEvent("Bash", "t1", {"command": "pwd"}))
    await handler.handle(StreamText("  工具后回答  "))

    handler.finish()

    assert transcript.assistant == ["最终回答", "工具后回答"]
    assert handler.state.assistant_text == ""
    assert handler.state.thinking_text == ""
    assert handler.state.tools == {}
    assert live.stop_calls >= 2


@pytest.mark.asyncio
async def test_finish_resets_completed_tool_ids_for_the_next_user_round():
    transcript = FakeTranscript()
    handler = InlineEventHandler(FakeLiveRenderer(), transcript)
    result = ToolResultEvent("shared", "Search", "found", False, 0.3)

    await handler.handle(result)
    handler.finish()
    await handler.handle(result)

    assert len(transcript.tools) == 2
