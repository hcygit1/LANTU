"""Agent Loop 的集成测试 —— 以编程方式逐项验证 checklist。"""
from __future__ import annotations

import asyncio
import os
from copy import deepcopy
from typing import Any, AsyncIterator

import pytest
from pydantic import BaseModel

from lantu.agent import (
    Agent,
    ErrorEvent,
    LoopComplete,
    PermissionRequest,
    PermissionResponse,
    StreamText,
    ToolResultEvent,
    ToolUseEvent,
    TurnComplete,
    UsageEvent,
    partition_tool_calls,
)
from lantu.prompts import build_environment_context, build_plan_mode_reminder, build_system_prompt
from lantu.client import LLMClient
from lantu.conversation import ConversationManager
from lantu.hooks import Action, Hook, HookEngine
from lantu.serialization import build_anthropic_messages
from lantu.tools import create_default_registry
from lantu.tools.base import (
    StreamEnd,
    StreamEvent,
    TextDelta,
    ThinkingComplete,
    Tool,
    ToolCallComplete,
    ToolResult,
)

# ---------------------------------------------------------------------------
# 返回预设脚本响应的 mock LLM 客户端
# ---------------------------------------------------------------------------

class MockLLMClient(LLMClient):
    def __init__(self, responses: list[list[StreamEvent]], yield_control: bool = False) -> None:
        self._responses = list(responses)
        self._call_index = 0
        self._yield_control = yield_control
        self.systems: list[str] = []
        self.message_snapshots: list[list] = []
        self.tool_snapshots: list[list[dict[str, Any]]] = []

    async def stream(
        self,
        conversation: ConversationManager,
        system: str = "",
        tools: list[dict[str, Any]] | None = None,
    ) -> AsyncIterator[StreamEvent]:
        self.systems.append(system)
        self.message_snapshots.append(deepcopy(conversation.get_messages()))
        self.tool_snapshots.append(deepcopy(tools or []))
        if self._call_index >= len(self._responses):
            yield TextDelta(text="No more responses")
            yield StreamEnd(stop_reason="end_turn", input_tokens=1, output_tokens=1)
            return
        events = self._responses[self._call_index]
        self._call_index += 1
        for e in events:
            if self._yield_control:
                await asyncio.sleep(0)
            yield e

def _collect(events: list) -> dict[str, list]:
    result: dict[str, list] = {
        "text": [], "tool_use": [], "tool_result": [],
        "turn": [], "loop": [], "usage": [], "error": [],
    }
    for e in events:
        if isinstance(e, StreamText):
            result["text"].append(e.text)
        elif isinstance(e, ToolUseEvent):
            result["tool_use"].append(e)
        elif isinstance(e, ToolResultEvent):
            result["tool_result"].append(e)
        elif isinstance(e, TurnComplete):
            result["turn"].append(e)
        elif isinstance(e, LoopComplete):
            result["loop"].append(e)
        elif isinstance(e, UsageEvent):
            result["usage"].append(e)
        elif isinstance(e, ErrorEvent):
            result["error"].append(e)
    return result

# ---------------------------------------------------------------------------
# 测试用例
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_single_step_tool_call():
    """Agent 调用一次 ReadFile，拿到结果后停止。"""
    client = MockLLMClient([
        # 第 1 轮：模型调用 ReadFile
        [
            TextDelta("Let me read the file."),
            ToolCallComplete("t1", "ReadFile", {"file_path": "LANTU.md"}),
            StreamEnd("end_turn", input_tokens=10, output_tokens=20),
        ],
        # 第 2 轮：模型给出最终答案
        [
            TextDelta("The file contains project info."),
            StreamEnd("end_turn", input_tokens=30, output_tokens=15),
        ],
    ])
    registry = create_default_registry()
    agent = Agent(client, registry, "anthropic", work_dir=".")
    conv = ConversationManager()
    conv.add_user_message("Read LANTU.md")

    events = []
    async for e in agent.run(conv):
        events.append(e)

    c = _collect(events)
    assert len(c["tool_use"]) == 1
    assert c["tool_use"][0].tool_name == "ReadFile"
    assert len(c["tool_result"]) == 1
    assert len(c["turn"]) == 1
    assert len(c["loop"]) == 1
    assert c["loop"][0].total_turns == 2


@pytest.mark.asyncio
async def test_system_prompt_is_stable_across_tool_loop():
    client = MockLLMClient([
        [
            ToolCallComplete("t1", "ReadFile", {"file_path": "LANTU.md"}),
            StreamEnd("end_turn", input_tokens=10, output_tokens=5),
        ],
        [
            TextDelta("done"),
            StreamEnd("end_turn", input_tokens=20, output_tokens=5),
        ],
    ])
    hook_engine = HookEngine([
        Hook(
            id="inject-context",
            event="pre_send",
            action=Action(type="prompt", message="Project info here"),
            once=True,
        )
    ])
    agent = Agent(
        client,
        create_default_registry(),
        "anthropic",
        hook_engine=hook_engine,
    )
    conversation = ConversationManager()
    conversation.add_user_message("Read LANTU.md")

    async for _ in agent.run(conversation):
        pass

    assert len(client.systems) == 2
    assert client.systems[0] == client.systems[1]
    assert "Project info here" not in client.systems[0]
    assert sum(
        "Project info here" in message.content
        for message in client.message_snapshots[-1]
    ) == 1


@pytest.mark.asyncio
async def test_tool_result_is_fixed_before_entering_conversation(tmp_path):
    class Params(BaseModel):
        pass

    class LargeOutputTool(Tool):
        name = "LargeOutput"
        description = "Return a large result"
        params_model = Params

        async def execute(self, params: Params) -> ToolResult:
            return ToolResult(output="x" * 60_000)

    class RecordingSession:
        session_id = "session_a"

        def __init__(self):
            self.events = []

        def record(self, event):
            self.events.append(event)

    client = MockLLMClient([
        [
            ToolCallComplete("t1", "LargeOutput", {}),
            StreamEnd("end_turn", input_tokens=5, output_tokens=2),
        ],
        [TextDelta("done"), StreamEnd("end_turn", input_tokens=10, output_tokens=2)],
    ])
    registry = create_default_registry(work_dir=str(tmp_path))
    registry.register(LargeOutputTool())
    session = RecordingSession()
    agent = Agent(
        client,
        registry,
        "anthropic",
        work_dir=str(tmp_path),
        session=session,
    )
    conversation = ConversationManager()
    conversation.add_user_message("run the tool")

    async for _ in agent.run(conversation):
        pass

    context_result = next(
        result
        for message in conversation.history
        for result in message.tool_results
    )
    completed = next(
        event for event in session.events if event.event_type == "tool.completed"
    )

    assert context_result.content.startswith("<persisted-output>")
    assert completed.payload["output"] == "x" * 60_000
    assert (tmp_path / ".lantu/session/tool-results/t1.txt").exists()


@pytest.mark.asyncio
async def test_system_prompt_is_stable_across_agent_runs():
    client = MockLLMClient([
        [TextDelta("first"), StreamEnd("end_turn", input_tokens=5, output_tokens=2)],
        [TextDelta("second"), StreamEnd("end_turn", input_tokens=8, output_tokens=2)],
    ])
    agent = Agent(client, create_default_registry(), "anthropic")
    conversation = ConversationManager()

    conversation.add_user_message("first request")
    async for _ in agent.run(conversation):
        pass
    conversation.add_user_message("second request")
    async for _ in agent.run(conversation):
        pass

    assert len(client.systems) == 2
    assert client.systems[0] == client.systems[1]


@pytest.mark.asyncio
async def test_run_to_completion_appends_hook_prompt_to_messages():
    client = MockLLMClient([
        [TextDelta("done"), StreamEnd("end_turn", input_tokens=5, output_tokens=2)],
    ])
    hook_engine = HookEngine([
        Hook(
            id="inject-context",
            event="turn_start",
            action=Action(type="prompt", message="Sub-agent context"),
            once=True,
        )
    ])
    agent = Agent(
        client,
        create_default_registry(),
        "anthropic",
        hook_engine=hook_engine,
    )

    await agent.run_to_completion("finish the task")

    assert "Sub-agent context" not in client.systems[0]
    assert any(
        "Sub-agent context" in message.content
        for message in client.message_snapshots[0]
    )


@pytest.mark.asyncio
async def test_run_to_completion_refreshes_tools_after_tool_search(tmp_path):
    class Params(BaseModel):
        value: str = ""

    class DeferredTool(Tool):
        name = "DeferredTool"
        description = "A deferred test tool"
        params_model = Params
        should_defer = True

        async def execute(self, params: Params) -> ToolResult:
            return ToolResult(output=params.value)

    client = MockLLMClient([
        [
            ToolCallComplete(
                "search-1",
                "ToolSearch",
                {"query": "select:DeferredTool"},
            ),
            StreamEnd("end_turn", input_tokens=5, output_tokens=2),
        ],
        [TextDelta("done"), StreamEnd("end_turn", input_tokens=8, output_tokens=2)],
    ])
    registry = create_default_registry(work_dir=str(tmp_path))
    registry.register(DeferredTool())
    from lantu.tools.impl.tool_search import ToolSearchTool

    registry.register(ToolSearchTool(registry, protocol="anthropic"))
    agent = Agent(
        client,
        registry,
        "anthropic",
        work_dir=str(tmp_path),
    )

    result = await agent.run_to_completion("load the deferred tool")

    assert result == "done"
    assert "DeferredTool" not in {tool["name"] for tool in client.tool_snapshots[0]}
    assert "DeferredTool" in {tool["name"] for tool in client.tool_snapshots[1]}


@pytest.mark.asyncio
async def test_run_to_completion_continues_after_output_limit(tmp_path):
    client = MockLLMClient([
        [
            TextDelta("first half "),
            StreamEnd("max_tokens", input_tokens=5, output_tokens=2),
        ],
        [
            TextDelta("second half"),
            StreamEnd("end_turn", input_tokens=8, output_tokens=2),
        ],
    ])
    agent = Agent(
        client,
        create_default_registry(work_dir=str(tmp_path)),
        "anthropic",
        work_dir=str(tmp_path),
    )

    result = await agent.run_to_completion("finish the response")

    assert result == "first half second half"
    assert len(client.message_snapshots) == 2


@pytest.mark.asyncio
async def test_run_to_completion_preserves_thinking_blocks(tmp_path):
    client = MockLLMClient([[
        ThinkingComplete("inspect the repository", "signature-1"),
        TextDelta("done"),
        StreamEnd("end_turn", input_tokens=5, output_tokens=2),
    ]])
    conversation = ConversationManager()
    agent = Agent(
        client,
        create_default_registry(work_dir=str(tmp_path)),
        "anthropic",
        work_dir=str(tmp_path),
    )

    await agent.run_to_completion("analyze", conversation)

    assistant = conversation.history[-1]
    assert assistant.content == "done"
    assert len(assistant.thinking_blocks) == 1
    assert assistant.thinking_blocks[0].thinking == "inspect the repository"
    assert assistant.thinking_blocks[0].signature == "signature-1"


@pytest.mark.asyncio
async def test_run_to_completion_runs_safe_tools_concurrently(tmp_path):
    class Params(BaseModel):
        pass

    active = 0
    max_active = 0

    class ConcurrentTool(Tool):
        name = "ConcurrentTool"
        description = "A concurrency-safe test tool"
        params_model = Params
        is_concurrency_safe = True

        async def execute(self, params: Params) -> ToolResult:
            nonlocal active, max_active
            active += 1
            max_active = max(max_active, active)
            await asyncio.sleep(0.02)
            active -= 1
            return ToolResult(output="done")

    client = MockLLMClient([
        [
            ToolCallComplete("tool-1", "ConcurrentTool", {}),
            ToolCallComplete("tool-2", "ConcurrentTool", {}),
            StreamEnd("end_turn", input_tokens=5, output_tokens=2),
        ],
        [TextDelta("done"), StreamEnd("end_turn", input_tokens=8, output_tokens=2)],
    ])
    registry = create_default_registry(work_dir=str(tmp_path))
    registry.register(ConcurrentTool())
    agent = Agent(
        client,
        registry,
        "anthropic",
        work_dir=str(tmp_path),
    )

    await agent.run_to_completion("run both tools")

    assert max_active == 2


@pytest.mark.asyncio
async def test_run_to_completion_preserves_callback_order(tmp_path):
    class Params(BaseModel):
        pass

    class EchoTool(Tool):
        name = "EchoTool"
        description = "Return a fixed result"
        params_model = Params

        async def execute(self, params: Params) -> ToolResult:
            return ToolResult(output="ok")

    client = MockLLMClient([
        [
            TextDelta("working"),
            ToolCallComplete("tool-1", "EchoTool", {}),
            StreamEnd("end_turn", input_tokens=5, output_tokens=2),
        ],
        [TextDelta("done"), StreamEnd("end_turn", input_tokens=8, output_tokens=2)],
    ])
    registry = create_default_registry(work_dir=str(tmp_path))
    registry.register(EchoTool())
    agent = Agent(
        client,
        registry,
        "anthropic",
        work_dir=str(tmp_path),
    )
    callback_events: list[dict[str, Any]] = []

    await agent.run_to_completion(
        "run the tool",
        event_callback=callback_events.append,
    )

    assert [event["type"] for event in callback_events] == [
        "usage",
        "stream_text",
        "tool_use",
        "usage",
        "stream_text",
    ]
    assert callback_events[1]["text"] == "working"
    assert callback_events[-1]["text"] == "done"


@pytest.mark.asyncio
async def test_run_to_completion_finalizes_tool_result_once(tmp_path):
    class Params(BaseModel):
        pass

    class LargeOutputTool(Tool):
        name = "LargeOutput"
        description = "Return a large result"
        params_model = Params

        async def execute(self, params: Params) -> ToolResult:
            return ToolResult(output="x" * 60_000)

    class RecordingSession:
        session_id = "session_a"

        def __init__(self):
            self.events = []

        def record(self, event):
            self.events.append(event)

    client = MockLLMClient([
        [
            ToolCallComplete("t1", "LargeOutput", {}),
            StreamEnd("end_turn", input_tokens=5, output_tokens=2),
        ],
        [TextDelta("done"), StreamEnd("end_turn", input_tokens=10, output_tokens=2)],
    ])
    registry = create_default_registry(work_dir=str(tmp_path))
    registry.register(LargeOutputTool())
    session = RecordingSession()
    agent = Agent(
        client,
        registry,
        "anthropic",
        work_dir=str(tmp_path),
        session=session,
    )

    await agent.run_to_completion("run the tool")

    context_result = next(
        result
        for message in client.message_snapshots[1]
        for result in message.tool_results
    )
    completed = next(
        event for event in session.events if event.event_type == "tool.completed"
    )

    assert context_result.content.startswith("<persisted-output>")
    assert completed.payload["output"] == "x" * 60_000
    assert (tmp_path / ".lantu/session/tool-results/t1.txt").exists()


@pytest.mark.asyncio
async def test_noninteractive_tool_execution_records_journal_events(tmp_path):
    class RecordingSession:
        session_id = "session_a"

        def __init__(self):
            self.events = []

        def record(self, event):
            self.events.append(event)

    target = tmp_path / "sample.txt"
    target.write_text("hello", encoding="utf-8")
    session = RecordingSession()
    client = MockLLMClient([
        [
            ToolCallComplete(
                "tool_1",
                "ReadFile",
                {"file_path": str(target)},
            ),
            StreamEnd("end_turn", input_tokens=5, output_tokens=2),
        ],
        [TextDelta("done"), StreamEnd("end_turn", input_tokens=8, output_tokens=2)],
    ])
    agent = Agent(
        client,
        create_default_registry(work_dir=str(tmp_path)),
        "anthropic",
        work_dir=str(tmp_path),
        session=session,
    )

    result = await agent.run_to_completion("read the file")

    assert result == "done"
    tool_events = [
        event for event in session.events if event.event_type.startswith("tool.")
    ]
    assert [event.event_type for event in tool_events] == [
        "tool.started",
        "tool.completed",
    ]
    assert "hello" in tool_events[-1].payload["output"]


@pytest.mark.asyncio
async def test_run_records_model_usage_in_session():
    class RecordingSession:
        session_id = "session_a"

        def __init__(self):
            self.events = []

        def record(self, event):
            self.events.append(event)

    session = RecordingSession()
    client = MockLLMClient(
        [[
            TextDelta("done"),
            StreamEnd(
                "end_turn",
                input_tokens=10,
                output_tokens=5,
                cache_read=3,
                cache_creation=2,
            ),
        ]]
    )
    agent = Agent(
        client,
        create_default_registry(),
        "openai-compat",
        work_dir=".",
        session=session,
    )
    conversation = ConversationManager()
    conversation.add_user_message("test")

    events = []
    async for event in agent.run(conversation):
        events.append(event)

    usage_events = [event for event in events if isinstance(event, UsageEvent)]
    assert usage_events == [UsageEvent(input_tokens=10, output_tokens=5)]

    recorded = [event for event in session.events if event.event_type == "usage.recorded"]
    assert len(recorded) == 1
    assert recorded[0].payload == {
        "provider": "openai-compat",
        "model": "",
        "input_tokens": 10,
        "output_tokens": 5,
        "cache_read_tokens": 3,
        "cache_creation_tokens": 2,
    }


@pytest.mark.asyncio
async def test_multi_step_autonomous():
    """Agent 先 WriteFile 再 ReadFile 然后停止 —— 端到端的多步流程。"""
    # 清理残留文件，避免 read-before-edit 拦截新文件创建
    test_file = "/tmp/lantu_test_hello.txt"
    if os.path.exists(test_file):
        os.remove(test_file)
    client = MockLLMClient([
        # 第 1 轮：WriteFile
        [
            TextDelta("Creating file."),
            ToolCallComplete("t1", "WriteFile", {"file_path": "/tmp/lantu_test_hello.txt", "content": "Hello World"}),
            StreamEnd("end_turn", input_tokens=10, output_tokens=20),
        ],
        # 第 2 轮：ReadFile 进行验证
        [
            TextDelta("Verifying content."),
            ToolCallComplete("t2", "ReadFile", {"file_path": "/tmp/lantu_test_hello.txt"}),
            StreamEnd("end_turn", input_tokens=40, output_tokens=25),
        ],
        # 第 3 轮：最终答案
        [
            TextDelta("File created and verified. Content is correct."),
            StreamEnd("end_turn", input_tokens=60, output_tokens=30),
        ],
    ])
    registry = create_default_registry()
    agent = Agent(client, registry, "anthropic", work_dir="/tmp")
    conv = ConversationManager()
    conv.add_user_message("Create hello.txt with Hello World, then verify")

    events = []
    async for e in agent.run(conv):
        events.append(e)

    c = _collect(events)
    assert len(c["tool_use"]) == 2
    assert c["tool_use"][0].tool_name == "WriteFile"
    assert c["tool_use"][1].tool_name == "ReadFile"
    assert len(c["turn"]) == 2
    assert len(c["loop"]) == 1
    assert c["loop"][0].total_turns == 3
    # 验证文件确实被创建了
    assert not c["tool_result"][0].is_error
    assert not c["tool_result"][1].is_error

@pytest.mark.asyncio
async def test_stop_end_turn():
    """模型以 end_turn 自然停止。"""
    client = MockLLMClient([
        [
            TextDelta("Hello! How can I help?"),
            StreamEnd("end_turn", input_tokens=5, output_tokens=10),
        ],
    ])
    registry = create_default_registry()
    agent = Agent(client, registry, "anthropic")
    conv = ConversationManager()
    conv.add_user_message("Hi")

    events = []
    async for e in agent.run(conv):
        events.append(e)

    c = _collect(events)
    assert len(c["loop"]) == 1
    assert c["loop"][0].total_turns == 1
    assert len(c["error"]) == 0

@pytest.mark.asyncio
async def test_stop_max_iterations():
    """Agent 在达到 max_iterations 后停止。"""
    # 每个响应都带有工具调用，因此循环永远不会自然结束
    responses = []
    for i in range(5):
        responses.append([
            TextDelta(f"Step {i}"),
            ToolCallComplete(f"t{i}", "ReadFile", {"file_path": "LANTU.md"}),
            StreamEnd("end_turn", input_tokens=10, output_tokens=10),
        ])

    client = MockLLMClient(responses)
    registry = create_default_registry()
    agent = Agent(client, registry, "anthropic", max_iterations=2)
    conv = ConversationManager()
    conv.add_user_message("Do something")

    events = []
    async for e in agent.run(conv):
        events.append(e)

    c = _collect(events)
    assert len(c["error"]) == 1
    assert "maximum iterations" in c["error"][0].message

@pytest.mark.asyncio
async def test_stop_cancel():
    """Agent 在收到 CancelledError 时干净地停止。"""

    class SlowMockClient(LLMClient):
        """在事件之间 sleep 的 mock 客户端，以便留出取消的时机。"""
        def __init__(self) -> None:
            self._call_count = 0

        async def stream(
            self,
            conversation: ConversationManager,
            system: str = "",
            tools: list[dict[str, Any]] | None = None,
        ) -> AsyncIterator[StreamEvent]:
            self._call_count += 1
            await asyncio.sleep(0.01)
            yield TextDelta(f"Step {self._call_count}")
            await asyncio.sleep(0.01)
            yield ToolCallComplete(f"t{self._call_count}", "ReadFile", {"file_path": "LANTU.md"})
            await asyncio.sleep(0.01)
            yield StreamEnd("end_turn", input_tokens=10, output_tokens=10)

    client = SlowMockClient()
    registry = create_default_registry()
    agent = Agent(client, registry, "anthropic")
    conv = ConversationManager()
    conv.add_user_message("Do something")

    events: list = []
    cancelled = False

    async def run_agent():
        async for e in agent.run(conv):
            events.append(e)

    task = asyncio.create_task(run_agent())
    await asyncio.sleep(0.15)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        cancelled = True

    assert cancelled
    c = _collect(events)
    assert len(c["turn"]) >= 1
    assert len(c["turn"]) < 50

@pytest.mark.asyncio
async def test_stop_consecutive_unknown_tools():
    """Agent 在连续 3 次调用未知工具后停止。"""
    responses = []
    for i in range(5):
        responses.append([
            TextDelta(f"Trying tool {i}"),
            ToolCallComplete(f"t{i}", "NonExistentTool", {"arg": "val"}),
            StreamEnd("end_turn", input_tokens=10, output_tokens=10),
        ])

    client = MockLLMClient(responses)
    registry = create_default_registry()
    agent = Agent(client, registry, "anthropic")
    conv = ConversationManager()
    conv.add_user_message("Do something")

    events = []
    async for e in agent.run(conv):
        events.append(e)

    c = _collect(events)
    assert len(c["error"]) == 1
    assert "unknown tool" in c["error"][0].message

@pytest.mark.asyncio
async def test_message_splicing():
    """assistant 消息包含 text + 多个 tool_use；对应的 tool_result 被打包在一起。"""
    client = MockLLMClient([
        # 第 1 轮：一个响应里包含两次工具调用
        [
            TextDelta("Reading two files."),
            ToolCallComplete("t1", "ReadFile", {"file_path": "LANTU.md"}),
            ToolCallComplete("t2", "ReadFile", {"file_path": "pyproject.toml"}),
            StreamEnd("end_turn", input_tokens=10, output_tokens=20),
        ],
        # 第 2 轮：最终响应
        [
            TextDelta("Done."),
            StreamEnd("end_turn", input_tokens=30, output_tokens=10),
        ],
    ])
    registry = create_default_registry()
    agent = Agent(client, registry, "anthropic", work_dir=".")
    conv = ConversationManager()
    conv.add_user_message("Read both files")

    events = []
    async for e in agent.run(conv):
        events.append(e)

    # 检查对话历史
    msgs = build_anthropic_messages(conv.get_messages())
    # env_context(user) 和 user_message 被合并为一条 → merged_user + assistant(text+2 个 tool_use) + user(2 个 tool_result) + assistant(最终响应)
    assert len(msgs) == 4
    assistant_msg = msgs[1]
    assert assistant_msg["role"] == "assistant"
    assert len(assistant_msg["content"]) == 3  # text + 2 个 tool_use
    tool_results_msg = msgs[2]
    assert tool_results_msg["role"] == "user"
    assert len(tool_results_msg["content"]) == 2  # 2 个 tool_result
    assert tool_results_msg["content"][0]["tool_use_id"] == "t1"
    assert tool_results_msg["content"][1]["tool_use_id"] == "t2"

@pytest.mark.asyncio
async def test_concurrent_batch_execution():
    """多个 ReadFile 调用并发执行（属于同一批次）。"""
    client = MockLLMClient([
        [
            ToolCallComplete("t1", "ReadFile", {"file_path": "LANTU.md"}),
            ToolCallComplete("t2", "ReadFile", {"file_path": "pyproject.toml"}),
            StreamEnd("end_turn", input_tokens=10, output_tokens=20),
        ],
        [
            TextDelta("Both files read."),
            StreamEnd("end_turn", input_tokens=30, output_tokens=10),
        ],
    ])
    registry = create_default_registry()
    agent = Agent(client, registry, "anthropic", work_dir=".")
    conv = ConversationManager()
    conv.add_user_message("Read both")

    events = []
    async for e in agent.run(conv):
        events.append(e)

    c = _collect(events)
    assert len(c["tool_result"]) == 2
    # 两个都应成功（这些文件在项目根目录下存在）
    assert all(not r.is_error for r in c["tool_result"])

@pytest.mark.asyncio
async def test_token_usage_accumulates():
    """Usage 事件展示的是累计的 token 数量。"""
    client = MockLLMClient([
        [
            TextDelta("Step 1"),
            ToolCallComplete("t1", "ReadFile", {"file_path": "LANTU.md"}),
            StreamEnd("end_turn", input_tokens=100, output_tokens=50),
        ],
        [
            TextDelta("Step 2"),
            ToolCallComplete("t2", "ReadFile", {"file_path": "LANTU.md"}),
            StreamEnd("end_turn", input_tokens=200, output_tokens=80),
        ],
        [
            TextDelta("Done."),
            StreamEnd("end_turn", input_tokens=300, output_tokens=100),
        ],
    ])
    registry = create_default_registry()
    agent = Agent(client, registry, "anthropic", work_dir=".")
    conv = ConversationManager()
    conv.add_user_message("Test")

    events = []
    async for e in agent.run(conv):
        events.append(e)

    c = _collect(events)
    assert len(c["usage"]) == 3
    assert c["usage"][0].input_tokens == 100
    assert c["usage"][0].output_tokens == 50
    assert c["usage"][1].input_tokens == 300
    assert c["usage"][1].output_tokens == 130
    assert c["usage"][2].input_tokens == 600
    assert c["usage"][2].output_tokens == 230

@pytest.mark.asyncio
async def test_plan_mode():
    """通过 permission_mode 切换 plan 模式。"""
    from lantu.permissions import PermissionMode

    registry = create_default_registry()
    agent = Agent(MockLLMClient([]), registry, "anthropic")

    agent.set_permission_mode(PermissionMode.PLAN)
    assert agent.plan_mode is True

    agent.set_permission_mode(PermissionMode.DEFAULT)
    assert agent.plan_mode is False
    schemas = registry.get_all_schemas()
    names = [s["name"] for s in schemas]
    assert "WriteFile" in names
    assert "EditFile" in names
    assert "Bash" in names

@pytest.mark.asyncio
async def test_plan_mode_denied_tool_returns_error():
    """在 plan 模式下，写入类工具需要审批（effect=ask）；当用户
    拒绝时，工具返回一个错误结果，而不会真正执行。"""
    from lantu.permissions import (
        DangerousCommandDetector,
        PathSandbox,
        PermissionChecker,
        PermissionMode,
        RuleEngine,
    )

    client = MockLLMClient([
        [
            TextDelta("Let me write..."),
            ToolCallComplete("t1", "WriteFile", {"file_path": "x.txt", "content": "hi"}),
            StreamEnd("end_turn", input_tokens=10, output_tokens=20),
        ],
        [
            TextDelta("OK, I can't write in plan mode."),
            StreamEnd("end_turn", input_tokens=30, output_tokens=15),
        ],
    ])
    registry = create_default_registry()
    checker = PermissionChecker(
        detector=DangerousCommandDetector(),
        sandbox=PathSandbox("."),
        rule_engine=RuleEngine(),
        mode=PermissionMode.PLAN,
    )
    agent = Agent(client, registry, "anthropic", permission_checker=checker)
    agent.set_permission_mode(PermissionMode.PLAN)
    conv = ConversationManager()
    conv.add_user_message("Write a file")

    events = []
    async for e in agent.run(conv):
        events.append(e)
        # plan 模式在写入前会询问；这里模拟用户拒绝。
        if isinstance(e, PermissionRequest):
            e.future.set_result(PermissionResponse.DENY)

    c = _collect(events)
    assert len(c["tool_result"]) == 1
    assert c["tool_result"][0].is_error
    assert "denied" in c["tool_result"][0].output.lower() or "拒绝" in c["tool_result"][0].output
    assert len(c["error"]) == 0

def test_partition_tool_calls():
    """分批逻辑会把可并发执行的调用归到同一组。"""
    from lantu.tools.base import ToolCallComplete

    calls = [
        ToolCallComplete("1", "ReadFile", {}),
        ToolCallComplete("2", "ReadFile", {}),
        ToolCallComplete("3", "EditFile", {}),
        ToolCallComplete("4", "ReadFile", {}),
        ToolCallComplete("5", "ReadFile", {}),
    ]
    registry = create_default_registry()
    batches = partition_tool_calls(calls, registry)
    assert len(batches) == 3
    assert batches[0].concurrent and len(batches[0].calls) == 2
    assert not batches[1].concurrent and len(batches[1].calls) == 1
    assert batches[2].concurrent and len(batches[2].calls) == 2

def test_system_prompt_normal():
    sp = build_system_prompt()
    assert "Lantu" in sp
    assert "Plan mode" not in sp

def test_system_prompt_plan():
    reminder = build_plan_mode_reminder("/tmp/plan.md", False, 1)
    assert "Plan mode" in reminder
    assert "MUST NOT" in reminder

def test_plan_mode_sparse_reminder():
    reminder = build_plan_mode_reminder("/tmp/plan.md", True, 8)
    assert "Plan mode still active" in reminder

def test_environment_context():
    ctx = build_environment_context("/home/user/project")
    assert "/home/user/project" in ctx
    assert "Operating system" in ctx
    assert "Current time" in ctx
