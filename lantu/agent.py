from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, AsyncIterator, Callable

from pydantic import ValidationError

from lantu.client import LLMClient
from lantu.context import (
    CompactBoundary,
    CompactCircuitBreaker,
    CompactEvent,
    RecoveryState,
    auto_compact,
    ensure_session_dir,
    prepare_tool_results_with_metadata,
)
from lantu.conversation import ConversationManager, ToolResultBlock, ToolUseBlock
from lantu.conversation import ThinkingBlock as ConvThinkingBlock
from lantu.context.repo_map import RepoMap, RepoMapSnapshot
from lantu.memory.auto_memory import MemoryManager
from lantu.memory.file_ledger import FileLedger
from lantu.permissions import (
    Decision,
    PermissionChecker,
    PermissionMode,
)
from lantu.hooks import HookContext, HookEngine, ToolRejectedError
from lantu.hooks.engine import HookNotification
from lantu.prompts import build_environment_context, build_plan_mode_reminder, build_system_prompt
from lantu.tools import ToolRegistry
from lantu.tools.base import (
    StreamEnd,
    StreamEvent,
    TextDelta,
    ThinkingComplete,
    ThinkingDelta,
    ToolCallComplete,
    ToolCallDelta,
    ToolCallStart,
    ToolResult,
)

log = logging.getLogger(__name__)

MEMORY_EXTRACTION_INTERVAL = 5
MAX_TOKENS_CEILING = 64000
MAX_OUTPUT_TOKENS_RECOVERIES = 3
TOOL_SCHEMA_NOTICE_THRESHOLD = 20_000


# ---------------------------------------------------------------------------
# AgentEvent 事件类型
# ---------------------------------------------------------------------------

@dataclass
class StreamText:
    text: str


@dataclass
class ThinkingText:
    text: str


@dataclass
class RetryEvent:
    reason: str
    wait: float = 0.0


@dataclass
class ToolUseEvent:
    tool_name: str
    tool_id: str
    arguments: dict[str, Any]


@dataclass
class ToolResultEvent:
    tool_id: str
    tool_name: str
    output: str
    is_error: bool
    elapsed: float


@dataclass
class TurnComplete:
    turn: int


@dataclass
class LoopComplete:
    total_turns: int


@dataclass
class UsageEvent:
    input_tokens: int
    output_tokens: int


@dataclass
class ErrorEvent:
    message: str


@dataclass
class CompactNotification:
    before_tokens: int
    message: str
    # 结构化 boundary（摘要 + 原文保留尾部），UI/session 层用它持久化 compact_boundary 记录。
    # 失败路径下为 None。
    boundary: "CompactBoundary | None" = None


@dataclass
class HookEvent:
    hook_id: str
    event: str
    output: str
    success: bool


class PermissionResponse(Enum):
    ALLOW = "allow"
    DENY = "deny"
    ALLOW_ALWAYS = "allow_always"


@dataclass
class PermissionRequest:
    tool_name: str
    description: str
    future: asyncio.Future[PermissionResponse]


AgentEvent = (
    StreamText
    | ThinkingText
    | RetryEvent
    | ToolUseEvent
    | ToolResultEvent
    | TurnComplete
    | LoopComplete
    | UsageEvent
    | ErrorEvent
    | PermissionRequest
    | CompactNotification
    | HookEvent
)


# ---------------------------------------------------------------------------
# LLM 响应收集器
# ---------------------------------------------------------------------------

@dataclass
class ThinkingBlock:
    thinking: str
    signature: str


@dataclass
class LLMResponse:
    text: str = ""
    tool_calls: list[ToolCallComplete] = field(default_factory=list)
    thinking_blocks: list[ThinkingBlock] = field(default_factory=list)
    stop_reason: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read: int = 0
    cache_creation: int = 0


class StreamCollector:
    def __init__(self) -> None:
        self.response = LLMResponse()

    async def consume(
        self, stream: AsyncIterator[StreamEvent]
    ) -> AsyncIterator[AgentEvent]:
        async for event in stream:
            if isinstance(event, TextDelta):
                self.response.text += event.text
                yield StreamText(text=event.text)
            elif isinstance(event, ThinkingDelta):
                yield ThinkingText(text=event.text)
            elif isinstance(event, ThinkingComplete):
                self.response.thinking_blocks.append(
                    ThinkingBlock(thinking=event.thinking, signature=event.signature)
                )
            elif isinstance(event, ToolCallStart):
                pass
            elif isinstance(event, ToolCallDelta):
                pass
            elif isinstance(event, ToolCallComplete):
                self.response.tool_calls.append(event)
                yield ToolUseEvent(
                    tool_name=event.tool_name,
                    tool_id=event.tool_id,
                    arguments=event.arguments,
                )
            elif isinstance(event, StreamEnd):
                self.response.stop_reason = event.stop_reason
                self.response.input_tokens = event.input_tokens
                self.response.output_tokens = event.output_tokens
                self.response.cache_read = event.cache_read
                self.response.cache_creation = event.cache_creation


# ---------------------------------------------------------------------------
# tool 批量执行
# ---------------------------------------------------------------------------

@dataclass
class ToolBatch:
    concurrent: bool
    calls: list[ToolCallComplete]


def partition_tool_calls(
    tool_calls: list[ToolCallComplete],
    registry: ToolRegistry,
) -> list[ToolBatch]:
    batches: list[ToolBatch] = []
    for tc in tool_calls:
        tool = registry.get(tc.tool_name)
        safe = tool is not None and tool.is_concurrency_safe and registry.is_model_visible(tc.tool_name)

        if safe and batches and batches[-1].concurrent:
            batches[-1].calls.append(tc)
        else:
            batches.append(ToolBatch(concurrent=safe, calls=[tc]))
    return batches


# ---------------------------------------------------------------------------
# streaming 执行器 — 在 LLM streaming 期间启动 tool 执行
# ---------------------------------------------------------------------------

@dataclass
class _ToolExecResult:
    tool_id: str
    tool_name: str
    result: ToolResult
    elapsed: float
    is_unknown: bool


class StreamingExecutor:
    def __init__(self) -> None:
        self._tasks: list[tuple[int, asyncio.Task[_ToolExecResult]]] = []
        self._order = 0

    def submit(
        self,
        coro: Any,
    ) -> None:
        task = asyncio.create_task(coro)
        self._tasks.append((self._order, task))
        self._order += 1

    async def collect_results(self) -> list[_ToolExecResult]:
        if not self._tasks:
            return []
        tasks = [t for _, t in sorted(self._tasks, key=lambda x: x[0])]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        out: list[_ToolExecResult] = []
        for r in results:
            if isinstance(r, Exception):
                out.append(_ToolExecResult(
                    tool_id="",
                    tool_name="",
                    result=ToolResult(output=f"Tool execution error: {r}", is_error=True),
                    elapsed=0.0,
                    is_unknown=False,
                ))
            else:
                out.append(r)
        return out


# ---------------------------------------------------------------------------
# Agent 主循环
# ---------------------------------------------------------------------------

class Agent:
    def __init__(
        self,
        client: LLMClient,
        registry: ToolRegistry,
        protocol: str,
        work_dir: str = ".",
        max_iterations: int = 0,
        permission_checker: PermissionChecker | None = None,
        context_window: int = 200_000,
        instructions_content: str = "",
        memory_manager: MemoryManager | None = None,
        hook_engine: HookEngine | None = None,
        session: Any | None = None,
        repo_map: RepoMap | None = None,
    ) -> None:
        self.client = client
        self.registry = registry
        self.protocol = protocol
        self.work_dir = work_dir
        self.max_iterations = max_iterations
        self.permission_checker = permission_checker
        self.permission_mode: PermissionMode = (
            permission_checker.mode if permission_checker else PermissionMode.DEFAULT
        )
        self.context_window = context_window
        self.session_dir = ensure_session_dir(work_dir)
        self.compact_breaker = CompactCircuitBreaker()
        # 保存重建工作上下文所需的快照，在 Layer 2 压缩对话后使用：
        # 最近的文件读取和 skill 调用。每次 ReadFile / skill 调用时记录，
        # auto_compact 触发阈值时消费。
        self.recovery_state: RecoveryState = RecoveryState()
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self.instructions_content = instructions_content
        self.memory_manager = memory_manager
        self.hook_engine = hook_engine
        self.session = session
        self.repo_map = repo_map
        self.file_ledger = getattr(session, "file_ledger", None) or FileLedger()
        session_epoch = getattr(session, "schema_epoch", None)
        self._schema_epoch_id = (
            session_epoch.get("epoch_id")
            if isinstance(session_epoch, dict)
            else None
        )
        self._loop_count = 0
        # 记忆提取合并策略（对齐 Go 版 inProgress + pendingContext）：
        # _extracting: 标记是否有提取正在进行
        # _pending_extraction: 提取期间又触发了新请求，标记需要尾随提取
        self._extracting = False
        self._pending_extraction = False
        self.session_id: str = ""
        self.active_skills: dict[str, str] = {}
        self._skill_catalog: str = ""
        self._agent_catalog: str = ""
        self._agent_catalog_list: list[tuple[str, str]] = []
        self.agent_id: str = uuid.uuid4().hex[:12]
        self.parent_id: str | None = None
        self.trace_id: str | None = None
        self.coordinator_mode: bool = False
        self.team_name: str = ""
        self._team_manager: Any = None
        self.notification_fn: Callable[[], list[str]] | None = None
        self.file_history: Any = None
        self._stable_system_prompt: str | None = None
        self.tool_loading_mode_locked = False
        self._tool_schema_notice_shown = False

        # 非阻塞 memory recall：prefetch task 与主 LLM 调用并行，工具执行后注入
        self.memory_recall_task: Any | None = None
        self._memory_recall_consumed: bool = False

    @property
    def _transcript_path(self) -> str:
        if self.session_id:
            return str(Path(self.work_dir) / ".lantu" / "sessions" / f"{self.session_id}.jsonl")
        return ""

    @property
    def plan_mode(self) -> bool:
        return self.permission_mode == PermissionMode.PLAN

    _plan_path_cache: Path | None = None

    def _get_plan_path(self) -> Path:
        if self._plan_path_cache is not None:
            return self._plan_path_cache
        import random
        import datetime
        _ADJECTIVES = ["bold", "bright", "calm", "cool", "deep", "fair", "fast", "fine",
                       "glad", "keen", "kind", "lean", "mild", "neat", "pure", "safe",
                       "slim", "soft", "tall", "warm", "wise", "grand", "swift", "vivid"]
        _NOUNS = ["sketch", "draft", "spark", "bloom", "trail", "ridge", "creek", "grove",
                  "cliff", "cloud", "field", "forge", "frost", "haven", "pearl", "stone",
                  "storm", "river", "tower", "delta", "flame", "orbit", "pulse", "shore"]
        plans_dir = Path(self.work_dir) / ".lantu" / "plans"
        plans_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.datetime.now().strftime("%m%d-%H%M")
        slug = f"{random.choice(_ADJECTIVES)}-{random.choice(_NOUNS)}-{ts}"
        self._plan_path_cache = plans_dir / f"{slug}.md"
        return self._plan_path_cache

    def set_permission_mode(self, mode: PermissionMode) -> None:
        self.permission_mode = mode
        if self.permission_checker:
            self.permission_checker.mode = mode

    def set_tool_loading_mode(self, mode: str) -> None:
        if self.tool_loading_mode_locked:
            raise RuntimeError(
                "tool loading mode is locked after the first user message"
            )
        self.registry.set_loading_mode(mode)
        self._tool_schema_notice_shown = False

    def lock_tool_loading_mode(self) -> None:
        self.tool_loading_mode_locked = True

    def unlock_tool_loading_mode(self) -> None:
        self.tool_loading_mode_locked = False

    def tool_loading_notice(self) -> str | None:
        """Return a one-time user-facing hint for a large standard tool list."""
        if self._tool_schema_notice_shown:
            return None
        if self.registry.loading_mode != "standard":
            return None
        self._tool_schema_notice_shown = True
        size = self.registry.schema_size(self.protocol)
        if size <= TOOL_SCHEMA_NOTICE_THRESHOLD:
            return None
        return (
            f"当前工具定义较多（约 {size:,} 字符），可能占用较多上下文。\n"
            "如需按需加载工具，请在发送消息前使用：/tools mode progressive"
        )

    def activate_skill(self, name: str, prompt_body: str) -> None:
        self.active_skills[name] = prompt_body

    def clear_active_skills(self) -> None:
        self.active_skills.clear()

    def set_skill_catalog(self, catalog: str) -> None:
        self._skill_catalog = catalog


    def set_agent_catalog(self, catalog: str, catalog_list: list[tuple[str, str]] | None = None) -> None:
        self._agent_catalog = catalog
        if catalog_list is not None:
            self._agent_catalog_list = catalog_list

    def _get_system_prompt(self) -> str:
        if self._stable_system_prompt is None:
            self._stable_system_prompt = build_system_prompt(
                coordinator_mode=self.coordinator_mode,
                agent_catalog=self._agent_catalog_list or None,
            )
            if self.repo_map is not None:
                repo_map_section = self.repo_map.prompt_section()
                if repo_map_section:
                    self._stable_system_prompt += f"\n\n{repo_map_section}"
        return self._stable_system_prompt

    def refresh_repo_map(self) -> RepoMapSnapshot:
        if self.repo_map is None:
            raise RuntimeError("RepoMap is disabled in context.repo_map.enabled")
        snapshot = self.repo_map.refresh()
        self._stable_system_prompt = None
        return snapshot

    def retarget_repo_map(self, work_dir: str) -> RepoMapSnapshot | None:
        if self.repo_map is None:
            return None
        snapshot = self.repo_map.retarget(work_dir)
        self._stable_system_prompt = None
        return snapshot

    def _append_hook_prompts(self, conversation: ConversationManager) -> None:
        if not self.hook_engine:
            return
        for prompt in self.hook_engine.drain_prompt_messages():
            conversation.add_system_reminder(
                f"Hook injected context:\n{prompt.content}",
                reminder_key=f"hook:{prompt.hook_id}:{prompt.event}",
            )

    def _build_hook_context(self, event: str, **kwargs: str | dict) -> HookContext:
        return HookContext(
            event_name=event,
            tool_name=str(kwargs.get("tool_name", "")),
            tool_args=kwargs.get("tool_args", {}),
            file_path=str(kwargs.get("file_path", "")),
            message=str(kwargs.get("message", "")),
            error=str(kwargs.get("error", "")),
        )

    def _infer_file_path(self, args: dict) -> str:
        return str(args.get("file_path", args.get("path", "")))

    def _drain_hook_events(self) -> list[HookEvent]:
        if not self.hook_engine:
            return []
        return [
            HookEvent(
                hook_id=n.hook_id,
                event=n.event,
                output=n.output,
                success=n.success,
            )
            for n in self.hook_engine.drain_notifications()
        ]

    async def _run_core(
        self, conversation: ConversationManager
    ) -> AsyncIterator[AgentEvent]:
        self._current_conversation = conversation
        env_context = build_environment_context(
            self.work_dir, self.active_skills, self._skill_catalog, self._agent_catalog
        )
        conversation.inject_environment(env_context)

        memory_content = self.memory_manager.load() if self.memory_manager else ""
        conversation.inject_long_term_memory(self.instructions_content, memory_content)

        if self.hook_engine:
            ctx = self._build_hook_context("session_start")
            await self.hook_engine.run_hooks("session_start", ctx)
            for he in self._drain_hook_events():
                yield he

        iteration = 0
        consecutive_unknown = 0
        max_tokens_escalated = False
        output_recoveries = 0

        while True:
            iteration += 1

            if self.max_iterations > 0 and iteration > self.max_iterations:
                yield ErrorEvent(
                    message=f"Agent reached maximum iterations ({self.max_iterations})"
                )
                break

            if self.hook_engine:
                ctx = self._build_hook_context("turn_start")
                await self.hook_engine.run_hooks("turn_start", ctx)
                for he in self._drain_hook_events():
                    yield he

            self._consume_mailbox(conversation)
            if self.notification_fn:
                for note in self.notification_fn():
                    conversation.add_system_reminder(note)

            if self.hook_engine:
                ctx = self._build_hook_context("pre_send")
                await self.hook_engine.run_hooks("pre_send", ctx)
                for he in self._drain_hook_events():
                    yield he

            self._append_hook_prompts(conversation)
            system = self._get_system_prompt()

            if self.plan_mode:
                plan_path = str(self._get_plan_path())
                if self.permission_checker:
                    self.permission_checker.plan_file_path = plan_path
                plan_exists = self._get_plan_path().exists()
                plan_reminder = build_plan_mode_reminder(
                    plan_path, plan_exists, iteration
                )
                conversation.add_system_reminder(
                    plan_reminder,
                    reminder_key="plan_mode",
                )

            if self.hook_engine:
                for note in self.hook_engine.drain_notifications():
                    conversation.add_system_reminder(
                        f"Hook [{note.hook_id}] {note.event}: {note.output}"
                    )

            deferred_names = self.registry.get_deferred_tool_names()
            if deferred_names:
                conversation.add_system_reminder(
                    "The following deferred tools are available via ToolSearch. "
                    "Their schemas are NOT loaded - use ToolSearch with "
                    'query "select:<name>[,<name>...]" to load tool schemas before calling them:\n'
                    + "\n".join(deferred_names),
                    reminder_key="deferred_tools",
                )

            tools = self.registry.get_all_schemas(self.protocol)

            # 接近 context window 上限时自动 compact
            compact_result = await auto_compact(
                conversation,
                self.client,
                self.context_window,
                self.session_dir,
                protocol=self.protocol,
                breaker=self.compact_breaker,
                recovery=self.recovery_state,
                system_prompt=system,
                tool_schemas=tools,
                transcript_path=self._transcript_path,
            )
            if isinstance(compact_result, CompactEvent):
                yield CompactNotification(
                    before_tokens=compact_result.before_tokens,
                    message=f"上下文已压缩（压缩前 {compact_result.before_tokens:,} tokens）",
                    boundary=compact_result.boundary,
                )
                self.file_ledger.clear_visible()
                conversation.inject_environment(env_context)
                mem = self.memory_manager.load() if self.memory_manager else ""
                conversation.inject_long_term_memory(
                    self.instructions_content, mem
                )
            elif isinstance(compact_result, str):
                yield ErrorEvent(message=compact_result)

            collector = StreamCollector()
            self._sync_schema_epoch()
            model_call_id = uuid.uuid4().hex
            model_started = time.monotonic()
            self._record_model_event(
                "model.request.started",
                model_call_id,
                {"provider": self.protocol, "model": getattr(self.client, "model", "")},
            )
            try:
                from lantu.client import model_call_context

                session_id = self.session.session_id if self.session is not None else None
                with model_call_context(model_call_id, session_id):
                    llm_stream = self.client.stream(conversation, system=system, tools=tools)
                    async for event in collector.consume(llm_stream):
                        yield event
            except asyncio.CancelledError:
                self._record_model_event(
                    "model.request.interrupted",
                    model_call_id,
                    {
                        "reason": "cancelled",
                        "result_known": False,
                        "elapsed_ms": int((time.monotonic() - model_started) * 1000),
                    },
                )
                raise
            except Exception as exc:
                self._record_model_event(
                    "model.request.failed",
                    model_call_id,
                    {
                        "error": {"type": type(exc).__name__, "message": str(exc)},
                        "elapsed_ms": int((time.monotonic() - model_started) * 1000),
                    },
                )
                raise

            response = collector.response
            self._record_model_event(
                "model.request.completed",
                model_call_id,
                {
                    "response_id": getattr(response, "response_id", None),
                    "elapsed_ms": int((time.monotonic() - model_started) * 1000),
                },
            )

            if self.hook_engine:
                ctx = self._build_hook_context("post_receive", message=response.text)
                await self.hook_engine.run_hooks("post_receive", ctx)
                for he in self._drain_hook_events():
                    yield he

            yield self._record_usage(response)

            conv_thinking = [
                ConvThinkingBlock(thinking=tb.thinking, signature=tb.signature)
                for tb in response.thinking_blocks
            ]

            if response.stop_reason == "max_tokens":
                if not max_tokens_escalated:
                    self.client.set_max_output_tokens(MAX_TOKENS_CEILING)
                    max_tokens_escalated = True
                    if response.text:
                        conversation.add_assistant_message(
                            response.text, thinking_blocks=conv_thinking
                        )
                        conversation.add_user_message(
                            "Output token limit hit. Resume directly from where you stopped. "
                            "Do not apologize or repeat previous content. Pick up mid-thought if needed."
                        )
                    yield RetryEvent(reason="max_tokens escalation")
                    continue
                elif output_recoveries < MAX_OUTPUT_TOKENS_RECOVERIES:
                    output_recoveries += 1
                    conversation.add_assistant_message(
                        response.text, thinking_blocks=conv_thinking
                    )
                    conversation.add_user_message(
                        "Output token limit hit. Resume directly from where you stopped. "
                        "Break remaining work into smaller pieces."
                    )
                    yield RetryEvent(
                        reason=f"max_tokens recovery {output_recoveries}/{MAX_OUTPUT_TOKENS_RECOVERIES}"
                    )
                    continue
            else:
                output_recoveries = 0

            if not response.tool_calls:
                conversation.add_assistant_message(
                    response.text, thinking_blocks=conv_thinking
                )
                self._loop_count += 1
                if (
                    self._loop_count % MEMORY_EXTRACTION_INTERVAL == 0
                    and self.memory_manager
                ):
                    asyncio.ensure_future(self._extract_memories(conversation))
                if self.hook_engine:
                    ctx = self._build_hook_context("turn_end")
                    await self.hook_engine.run_hooks("turn_end", ctx)
                    ctx = self._build_hook_context("session_end")
                    await self.hook_engine.run_hooks("session_end", ctx)
                    for he in self._drain_hook_events():
                        yield he
                if self.file_history is not None:
                    summary = response.text[:60] + "..." if len(response.text) > 60 else response.text
                    self.file_history.make_snapshot(len(conversation.history), summary)
                yield LoopComplete(total_turns=iteration)
                break

            tool_uses = [
                ToolUseBlock(
                    tool_use_id=tc.tool_id,
                    tool_name=tc.tool_name,
                    arguments=tc.arguments,
                )
                for tc in response.tool_calls
            ]
            conversation.add_assistant_message(
                response.text, tool_uses, thinking_blocks=conv_thinking
            )
            # 在 assistant 回复加入历史后锚定实际用量：基线（input + cache + output）
            # 覆盖到当前位置，因此下一轮迭代顶部的 auto-compact 检查只需对
            # 接下来追加的 tool results 做字符估算。
            conversation.record_usage_anchor(
                response.input_tokens,
                response.output_tokens,
                response.cache_read,
                response.cache_creation,
            )

            tool_results: list[ToolResultBlock] = []
            batches = partition_tool_calls(response.tool_calls, self.registry)

            for batch in batches:
                if batch.concurrent and len(batch.calls) > 1:
                    batch_results = await self._execute_batch_parallel(batch.calls)
                    for br in batch_results:
                        if br.is_unknown:
                            consecutive_unknown += 1
                        else:
                            consecutive_unknown = 0
                        tool_results.append(
                            ToolResultBlock(
                                tool_use_id=br.tool_id,
                                content=br.result.output,
                                is_error=br.result.is_error,
                            )
                        )
                        yield ToolResultEvent(
                            tool_id=br.tool_id,
                            tool_name=br.tool_name,
                            output=br.result.output,
                            is_error=br.result.is_error,
                            elapsed=br.elapsed,
                        )
                else:
                    for tc in batch.calls:
                        result: ToolResult | None = None
                        elapsed = 0.0
                        is_unknown = False

                        if self.hook_engine:
                            file_path = self._infer_file_path(tc.arguments)
                            hook_ctx = self._build_hook_context(
                                "pre_tool_use",
                                tool_name=tc.tool_name,
                                tool_args=tc.arguments,
                                file_path=file_path,
                            )
                            rejection = await self.hook_engine.run_pre_tool_hooks(hook_ctx)
                            for he in self._drain_hook_events():
                                yield he
                            if rejection is not None:
                                result = ToolResult(
                                    output=f"Hook rejected: {rejection.reason}",
                                    is_error=True,
                                )
                                tool_results.append(
                                    ToolResultBlock(
                                        tool_use_id=tc.tool_id,
                                        content=result.output,
                                        is_error=True,
                                    )
                                )
                                yield ToolResultEvent(
                                    tool_id=tc.tool_id,
                                    tool_name=tc.tool_name,
                                    output=result.output,
                                    is_error=True,
                                    elapsed=0.0,
                                )
                                continue

                        async for item in self._execute_tool(tc):
                            if isinstance(item, PermissionRequest):
                                yield item
                            else:
                                result, elapsed, is_unknown = item

                        if result is None:
                            result = ToolResult(output="Error: no result from tool", is_error=True)

                        if is_unknown:
                            consecutive_unknown += 1
                        else:
                            consecutive_unknown = 0

                        if self.hook_engine:
                            file_path = self._infer_file_path(tc.arguments)
                            hook_ctx = self._build_hook_context(
                                "post_tool_use",
                                tool_name=tc.tool_name,
                                tool_args=tc.arguments,
                                file_path=file_path,
                            )
                            await self.hook_engine.run_hooks("post_tool_use", hook_ctx)
                            for he in self._drain_hook_events():
                                yield he

                        tool_results.append(
                            ToolResultBlock(
                                tool_use_id=tc.tool_id,
                                content=result.output,
                                is_error=result.is_error,
                            )
                        )
                        yield ToolResultEvent(
                            tool_id=tc.tool_id,
                            tool_name=tc.tool_name,
                            output=result.output,
                            is_error=result.is_error,
                            elapsed=elapsed,
                        )

            if consecutive_unknown >= 3:
                yield ErrorEvent(
                    message="Agent terminated: too many consecutive unknown tool calls"
                )
                break

            exit_plan_called = any(
                tc.tool_name == "ExitPlanMode" for tc in response.tool_calls
            )
            prepared = self._prepare_tool_results(tool_results)
            conversation.add_tool_results_message(prepared)

            # 非阻塞 memory recall：工具执行完后检查 prefetch 是否就绪
            if self.memory_recall_task and not self._memory_recall_consumed:
                if self.memory_recall_task.done():
                    try:
                        recall = self.memory_recall_task.result()
                        if recall:
                            conversation.add_system_reminder(recall)
                    except Exception:
                        pass
                    self._memory_recall_consumed = True

            if exit_plan_called:
                yield TurnComplete(turn=iteration)
                yield LoopComplete(total_turns=iteration)
                break

            if self.hook_engine:
                ctx = self._build_hook_context("turn_end")
                await self.hook_engine.run_hooks("turn_end", ctx)
                for he in self._drain_hook_events():
                    yield he
            yield TurnComplete(turn=iteration)


    async def run(self, conversation: ConversationManager) -> AsyncIterator[AgentEvent]:
        """Stream the shared agent loop as events for interactive callers."""
        async for event in self._run_core(conversation):
            yield event


    def _consume_mailbox(self, conversation: ConversationManager) -> None:
        if not self.team_name or not self._team_manager:
            return
        try:
            mailbox = self._team_manager.get_mailbox(self.team_name)
            if mailbox is None:
                return
            messages = mailbox.consume(self.agent_id)
            for msg in messages:
                prefix = f"[Message from {msg.from_agent}]"
                if msg.message_type != "text":
                    prefix = f"[{msg.message_type} from {msg.from_agent}]"
                content = f"{prefix} {msg.content}"
                conversation.add_user_message(content)
        except Exception as e:
            log.debug("Mailbox consumption failed: %s", e)

    def _build_permission_description(self, tc: ToolCallComplete) -> str:
        """为 HITL 权限确认生成人类可读的操作描述。"""
        return PermissionChecker.describe_tool_action(tc.tool_name, tc.arguments)

    async def _execute_single_tool_direct(
        self, tc: ToolCallComplete
    ) -> _ToolExecResult:
        tool = self.registry.get(tc.tool_name)
        start = time.monotonic()
        self._record_tool_started(tc)

        if tool is None:
            result = _ToolExecResult(
                tool_id=tc.tool_id,
                tool_name=tc.tool_name,
                result=ToolResult(output=f"Error: unknown tool '{tc.tool_name}'", is_error=True),
                elapsed=time.monotonic() - start,
                is_unknown=True,
            )
            self._record_tool_finished(tc, result.result, result.elapsed)
            return result

        if not self.registry.is_enabled(tc.tool_name):
            result = _ToolExecResult(
                tool_id=tc.tool_id,
                tool_name=tc.tool_name,
                result=ToolResult(output=f"Error: tool '{tc.tool_name}' is disabled", is_error=True),
                elapsed=time.monotonic() - start,
                is_unknown=False,
            )
            self._record_tool_finished(tc, result.result, result.elapsed)
            return result

        if not self.registry.is_model_visible(tc.tool_name):
            result = _ToolExecResult(
                tool_id=tc.tool_id,
                tool_name=tc.tool_name,
                result=ToolResult(
                    output=(
                        f"Error: tool '{tc.tool_name}' is not loaded. "
                        "Use ToolSearch before calling it."
                    ),
                    is_error=True,
                ),
                elapsed=time.monotonic() - start,
                is_unknown=False,
            )
            self._record_tool_finished(tc, result.result, result.elapsed)
            return result

        try:
            params = tool.params_model.model_validate(tc.arguments)
            result = await tool.execute(params)
            self._record_loaded_tool_schemas(tc, result)
            self._record_file_ledger_event(tc, result)
        except ValidationError as e:
            result = ToolResult(output=f"Parameter validation error: {e}", is_error=True)
        except Exception as e:
            result = ToolResult(output=f"Tool execution error: {e}", is_error=True)

        self._snapshot_for_recovery(tc, result)

        tool_result = _ToolExecResult(
            tool_id=tc.tool_id,
            tool_name=tc.tool_name,
            result=result,
            elapsed=time.monotonic() - start,
            is_unknown=False,
        )
        self._record_tool_finished(tc, tool_result.result, tool_result.elapsed)
        return tool_result


    async def _execute_batch_parallel(
        self, calls: list[ToolCallComplete]
    ) -> list[_ToolExecResult]:
        tasks = [self._execute_single_tool_direct(tc) for tc in calls]
        return list(await asyncio.gather(*tasks))

    async def _execute_tool(
        self, tc: ToolCallComplete
    ) -> AsyncIterator[tuple[ToolResult, float, bool]]:
        tool = self.registry.get(tc.tool_name)
        start = time.monotonic()
        is_unknown = False
        self._record_tool_started(tc)

        if tool is None:
            result = ToolResult(
                output=f"Error: unknown tool '{tc.tool_name}'", is_error=True
            )
            is_unknown = True
            elapsed = time.monotonic() - start
            self._record_tool_finished(tc, result, elapsed)
            yield result, elapsed, is_unknown
            return

        if not self.registry.is_enabled(tc.tool_name):
            result = ToolResult(
                output=f"Error: tool '{tc.tool_name}' is disabled in current mode",
                is_error=True,
            )
            elapsed = time.monotonic() - start
            self._record_tool_finished(tc, result, elapsed)
            yield result, elapsed, is_unknown
            return

        if not self.registry.is_model_visible(tc.tool_name):
            result = ToolResult(
                output=(
                    f"Error: tool '{tc.tool_name}' is not loaded. "
                    "Use ToolSearch before calling it."
                ),
                is_error=True,
            )
            elapsed = time.monotonic() - start
            self._record_tool_finished(tc, result, elapsed)
            yield result, elapsed, is_unknown
            return

        # 权限检查
        if self.permission_checker:
            decision = self.permission_checker.check(tool, tc.arguments)
            self._record_permission_decision(tc, decision.effect, decision.reason)

            if decision.effect == "deny":
                result = ToolResult(
                    output=f"Permission denied: {decision.reason}",
                    is_error=True,
                )
                elapsed = time.monotonic() - start
                self._record_tool_finished(tc, result, elapsed)
                yield result, elapsed, is_unknown
                return

            if decision.effect == "ask":
                loop = asyncio.get_running_loop()
                future: asyncio.Future[PermissionResponse] = loop.create_future()
                desc = self._build_permission_description(tc)
                # 向调用方 yield 权限请求事件，由调用方处理
                yield PermissionRequest(
                    tool_name=tc.tool_name,
                    description=desc,
                    future=future,
                )
                response = await future
                self._record_permission_decision(
                    tc, response.value, "user_response"
                )

                if response == PermissionResponse.DENY:
                    result = ToolResult(
                        output="Permission denied: 用户拒绝了此操作",
                        is_error=True,
                    )
                    elapsed = time.monotonic() - start
                    self._record_tool_finished(tc, result, elapsed)
                    yield result, elapsed, is_unknown
                    return

                if response == PermissionResponse.ALLOW_ALWAYS:
                    from lantu.permissions.rules import Rule, extract_content
                    content = extract_content(tc.tool_name, tc.arguments)
                    pattern = f"{content[:60]}*" if len(content) > 60 else f"{content}*"
                    # 持久化规则写入本地文件
                    rule = Rule(tool_name=tc.tool_name, pattern=pattern, effect="allow")
                    self.permission_checker.rule_engine.append_local_rule(rule)
                    # 同时加入会话级放行集合，本轮立即生效无需磁盘读取
                    self.permission_checker.add_session_allow(tc.tool_name, content)

        try:
            params = tool.params_model.model_validate(tc.arguments)
            result = await tool.execute(params)
            self._record_loaded_tool_schemas(tc, result)
            self._record_file_ledger_event(tc, result)
        except ValidationError as e:
            result = ToolResult(
                output=f"Parameter validation error: {e}", is_error=True
            )
        except Exception as e:
            result = ToolResult(
                output=f"Tool execution error: {e}", is_error=True
            )

        self._snapshot_for_recovery(tc, result)

        elapsed = time.monotonic() - start
        self._record_tool_finished(tc, result, elapsed)
        yield result, elapsed, is_unknown

    def _record_model_event(
        self, event_type: str, model_call_id: str, payload: dict[str, Any]
    ) -> None:
        if self.session is None:
            return
        from lantu.memory.session import ExecutionEvent

        event_payload = {"model_call_id": model_call_id, **payload}
        if self._schema_epoch_id is not None:
            event_payload.setdefault("schema_epoch_id", self._schema_epoch_id)
        self.session.record(
            ExecutionEvent(
                event_type,
                event_payload,
            )
        )

    def _sync_schema_epoch(self) -> None:
        """Record a new tool-schema view before the next provider request."""
        epoch = self.registry.schema_epoch(self.protocol)
        if epoch.epoch_id == self._schema_epoch_id:
            return

        previous = getattr(self.session, "schema_epoch", None)
        previous_id = previous.get("epoch_id") if isinstance(previous, dict) else None
        payload = epoch.to_payload()
        payload["previous_epoch_id"] = previous_id
        if previous_id is None:
            reason = "initial"
        elif previous.get("loading_mode") != epoch.loading_mode:
            reason = "mode_changed"
        elif set(epoch.visible_tools) > set(previous.get("visible_tools", [])):
            reason = "tool_loaded"
        else:
            reason = "schema_changed"
        payload["reason"] = reason

        if self.session is not None:
            from lantu.memory.session import ExecutionEvent

            self.session.record(
                ExecutionEvent("tool.schema.epoch.changed", payload)
            )
        self._schema_epoch_id = epoch.epoch_id

    def _record_loaded_tool_schemas(
        self, tc: ToolCallComplete, result: ToolResult
    ) -> None:
        """Persist a successful ToolSearch activation before the next request."""
        if tc.tool_name != "ToolSearch" or not result.meta or self.session is None:
            return
        states = result.meta.get("loaded_tools")
        if not isinstance(states, list) or not states:
            return
        try:
            from lantu.memory.session import ExecutionEvent

            self.session.record(
                ExecutionEvent(
                    "tool.schema.loaded",
                    {"tools": states},
                )
            )
        except Exception:
            names = [
                state["name"]
                for state in states
                if isinstance(state, dict) and isinstance(state.get("name"), str)
            ]
            self.registry.forget_discovered(names)
            raise

    def _record_usage(self, response: LLMResponse) -> UsageEvent:
        self.total_input_tokens += response.input_tokens
        self.total_output_tokens += response.output_tokens

        if self.session is not None:
            from lantu.memory.session import ExecutionEvent

            self.session.record(
                ExecutionEvent(
                    "usage.recorded",
                    {
                        "provider": self.protocol,
                        "model": getattr(self.client, "model", ""),
                        "input_tokens": response.input_tokens,
                        "output_tokens": response.output_tokens,
                        "cache_read_tokens": response.cache_read,
                        "cache_creation_tokens": response.cache_creation,
                    },
                )
            )

        return UsageEvent(
            input_tokens=self.total_input_tokens,
            output_tokens=self.total_output_tokens,
        )

    def _record_tool_started(self, tc: ToolCallComplete) -> None:
        if self.session is None:
            return
        from lantu.memory.session import ExecutionEvent

        self.session.record(
            ExecutionEvent(
                "tool.started",
                {
                    "tool_call_id": tc.tool_id,
                    "tool_name": tc.tool_name,
                    "arguments": tc.arguments,
                },
            )
        )

    def _record_permission_decision(
        self, tc: ToolCallComplete, decision: str, reason: str = ""
    ) -> None:
        if self.session is None:
            return
        from lantu.memory.session import ExecutionEvent

        self.session.record(
            ExecutionEvent(
                "permission.decided",
                {
                    "tool_call_id": tc.tool_id,
                    "tool_name": tc.tool_name,
                    "decision": decision,
                    "reason": reason,
                },
            )
        )

    def _record_tool_finished(
        self, tc: ToolCallComplete, result: ToolResult, elapsed: float
    ) -> None:
        if self.session is None:
            return
        from lantu.memory.session import ExecutionEvent

        if result.is_error:
            event_type = "tool.failed"
            payload: dict[str, Any] = {
                "tool_call_id": tc.tool_id,
                "tool_name": tc.tool_name,
                "error": {"type": "tool_error", "message": result.output},
                "output": result.output,
                "elapsed_ms": int(elapsed * 1000),
            }
        else:
            event_type = "tool.completed"
            payload = {
                "tool_call_id": tc.tool_id,
                "tool_name": tc.tool_name,
                "output": result.output,
                "elapsed_ms": int(elapsed * 1000),
            }
        self.session.record(ExecutionEvent(event_type, payload))

    def _snapshot_for_recovery(
        self, tc: ToolCallComplete, result: ToolResult
    ) -> None:
        """捕获 ReadFile 刚交给模型的内容，以便 Layer 2 压缩对话后
        auto_compact 能重新附加这些数据。每次 ReadFile 多一次磁盘读取，
        比从 tool 输出中反向解析行号要划算。
        """
        if result.is_error or tc.tool_name != "ReadFile":
            return
        path = tc.arguments.get("file_path") if isinstance(tc.arguments, dict) else None
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                content = fh.read()
        except OSError:
            return
        self.recovery_state.record_file_read(path, content)

    def _prepare_tool_results(
        self, results: list[ToolResultBlock]
    ) -> list[ToolResultBlock]:
        """Deduplicate visible file ranges, then record what reached history."""
        deduplicated: list[ToolResultBlock] = []
        deduplicated_ids: set[str] = set()
        for result in results:
            observation = self.file_ledger.observation(result.tool_use_id)
            if (
                observation is not None
                and self.file_ledger.is_visible(
                    observation.path,
                    observation.content_hash,
                    observation.range_start,
                    observation.range_end,
                )
            ):
                deduplicated.append(
                    ToolResultBlock(
                        tool_use_id=result.tool_use_id,
                        content=(
                            "The requested lines of this file version are already "
                            "visible in the conversation; the content is unchanged."
                        ),
                        is_error=result.is_error,
                    )
                )
                deduplicated_ids.add(result.tool_use_id)
            else:
                deduplicated.append(result)

        prepared = prepare_tool_results_with_metadata(
            deduplicated,
            self.session_dir,
            file_read_ids={
                result.tool_use_id
                for result in deduplicated
                if self.file_ledger.observation(result.tool_use_id) is not None
            },
        )
        for result in deduplicated:
            observation = self.file_ledger.observation(result.tool_use_id)
            if observation is None:
                continue
            presentation = prepared.presentations.get(result.tool_use_id)
            if presentation is None:
                continue
            if result.tool_use_id in deduplicated_ids:
                continue
            if presentation.visible_line_ranges:
                for range_start, range_end in presentation.visible_line_ranges:
                    self.file_ledger.mark_visible(
                        observation.path,
                        observation.content_hash,
                        range_start,
                        range_end,
                    )
            elif presentation.mode == "inline":
                self.file_ledger.mark_visible(
                    observation.path,
                    observation.content_hash,
                    observation.range_start,
                    observation.range_end,
                )
            elif presentation.preview_line_count > 0:
                self.file_ledger.mark_visible(
                    observation.path,
                    observation.content_hash,
                    observation.range_start,
                    observation.range_start + presentation.preview_line_count - 1,
                )
        return prepared.results

    def _record_file_ledger_event(
        self, tc: ToolCallComplete, result: ToolResult
    ) -> None:
        """Persist the latest file version after a successful file operation."""
        if result.is_error or self.session is None:
            return
        if tc.tool_name not in {"ReadFile", "EditFile", "WriteFile"}:
            return
        ledger = self.file_ledger
        raw_path = tc.arguments.get("file_path")
        if not isinstance(raw_path, str) or not raw_path:
            return
        tool = self.registry.get(tc.tool_name)
        if tool is None:
            return
        path = tool.resolve_path(raw_path)
        try:
            content = path.read_text(encoding="utf-8")
            mtime_ns = path.stat().st_mtime_ns
        except OSError:
            return

        arguments = tc.arguments
        operation = {
            "ReadFile": "read",
            "EditFile": "edit",
            "WriteFile": "write",
        }[tc.tool_name]
        offset = arguments.get("offset", 0) if tc.tool_name == "ReadFile" else 0
        limit = arguments.get("limit") if tc.tool_name == "ReadFile" else None
        try:
            offset_value = int(offset)
            limit_value = None if limit is None else int(limit)
        except (TypeError, ValueError):
            offset_value = 0
            limit_value = None

        from lantu.memory.session import ExecutionEvent

        if operation != "read":
            ledger.invalidate(path)

        entry = FileLedger.build_entry(
            path,
            content,
            operation=operation,
            offset=offset_value,
            limit=limit_value,
            mtime_ns=mtime_ns,
            tool_call_id=tc.tool_id,
        )
        event_type = "file.observed" if operation == "read" else "file.updated"
        self.session.record(ExecutionEvent(event_type, entry.to_payload()))
        ledger.apply(entry)

    async def _extract_memories(
        self, conversation: ConversationManager
    ) -> None:
        """触发记忆提取，对齐 Go 版 inProgress + pendingContext 合并策略。

        当提取正在进行时，新的触发不会启动并发提取，而是标记 _pending_extraction。
        当前提取完成后检查该标志，如果有 pending 则立即执行一次尾随提取，
        防止多个触发器同时执行导致重复提取。
        """
        if not self.memory_manager:
            return

        # 合并策略：正在提取时暂存新请求，等当前提取完成后尾随执行
        if self._extracting:
            log.debug("[extractMemories] extraction in progress — stashing for trailing run")
            self._pending_extraction = True
            return

        self._extracting = True
        try:
            await self.memory_manager.extract(
                self.client, conversation, self.protocol
            )
        except Exception as e:
            log.debug("Memory extraction failed: %s", e)
        finally:
            self._extracting = False
            # 检查是否有尾随提取请求
            if self._pending_extraction:
                self._pending_extraction = False
                log.debug("[extractMemories] running trailing extraction for stashed context")
                # 递归调用自身处理尾随请求
                await self._extract_memories(conversation)

    async def manual_compact(
        self, conversation: ConversationManager
    ) -> CompactNotification | ErrorEvent:
        # auto_compact 会用摘要替换 conversation.history，旧 Epoch 的工具结果
        # 不会再直接发送给模型。
        result = await auto_compact(
            conversation,
            self.client,
            self.context_window,
            self.session_dir,
            protocol=self.protocol,
            manual=True,
            breaker=self.compact_breaker,
            recovery=self.recovery_state,
            system_prompt=self._get_system_prompt(),
            tool_schemas=self.registry.get_all_schemas(self.protocol),
            transcript_path=self._transcript_path,
        )
        if isinstance(result, CompactEvent):
            self.file_ledger.clear_visible()
            env_context = build_environment_context(
            self.work_dir, self.active_skills, self._skill_catalog, self._agent_catalog
        )
            conversation.inject_environment(env_context)
            memory_content = self.memory_manager.load() if self.memory_manager else ""
            conversation.inject_long_term_memory(
                self.instructions_content, memory_content
            )
            return CompactNotification(
                before_tokens=result.before_tokens,
                message=f"上下文已压缩（压缩前 {result.before_tokens:,} tokens）",
                boundary=result.boundary,
            )
        return ErrorEvent(message=result or "压缩失败：对话历史为空或未达到压缩条件")

    async def run_to_completion(
        self,
        task: str,
        conversation: ConversationManager | None = None,
        event_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> str:
        """Run the shared event loop and adapt it for autonomous callers."""
        if conversation is None:
            conversation = ConversationManager()
        if task:
            conversation.add_user_message(task)

        result_parts: list[str] = []
        callback_parts: list[str] = []
        pending_tool_uses: list[ToolUseEvent] = []

        def flush_text_callback() -> None:
            if not callback_parts:
                return
            if event_callback:
                event_callback({
                    "type": "stream_text",
                    "text": "".join(callback_parts),
                })
            callback_parts.clear()

        async for event in self._run_core(conversation):
            if isinstance(event, StreamText):
                result_parts.append(event.text)
                callback_parts.append(event.text)
            elif isinstance(event, ToolUseEvent):
                pending_tool_uses.append(event)
            elif isinstance(event, UsageEvent):
                if event_callback:
                    event_callback({
                        "type": "usage",
                        "usage": {
                            "inputTokens": event.input_tokens,
                            "outputTokens": event.output_tokens,
                        },
                    })
                flush_text_callback()
                if event_callback:
                    for tool_use in pending_tool_uses:
                        event_callback({
                            "type": "tool_use",
                            "toolName": tool_use.tool_name,
                            "args": tool_use.arguments,
                        })
                pending_tool_uses.clear()
            elif isinstance(event, PermissionRequest):
                response = (
                    PermissionResponse.ALLOW
                    if self.permission_mode == PermissionMode.BYPASS
                    else PermissionResponse.DENY
                )
                if not event.future.done():
                    event.future.set_result(response)
            elif isinstance(event, RetryEvent):
                flush_text_callback()
            elif isinstance(event, TurnComplete):
                flush_text_callback()
                result_parts.clear()
            elif isinstance(event, LoopComplete):
                flush_text_callback()
                return "".join(result_parts)

        flush_text_callback()
        return "".join(result_parts)
