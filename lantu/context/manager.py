from __future__ import annotations

import os
import re
import shutil
import threading
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Mapping

from lantu.conversation import (
    ConversationManager,
    Message,
    ToolResultBlock,
    estimate_tokens,
)

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

TOOL_RESULT_INLINE_CHAR_LIMIT = 8_192
AGGREGATE_CHAR_LIMIT = 64_000
TOOL_RESULT_HEAD_CHARS = 4_096
TOOL_RESULT_TAIL_CHARS = 1_024
AGGREGATE_PREVIEW_HEAD_CHARS = 512
AGGREGATE_PREVIEW_TAIL_CHARS = 128

# File reads use the same size policy, while their preview also records the
# visible source-line ranges in File Ledger.
FILE_READ_PREVIEW_CHAR_LIMIT = TOOL_RESULT_INLINE_CHAR_LIMIT
FILE_READ_HEAD_CHARS = TOOL_RESULT_HEAD_CHARS
FILE_READ_TAIL_CHARS = TOOL_RESULT_TAIL_CHARS

SUMMARY_OUTPUT_RESERVE = 20_000
# 软触发安全边距：effectiveWindow − 13K 为自动压缩触发线，走熔断器保护
AUTO_COMPACT_SAFETY_MARGIN = 13_000
# 硬触发安全边距：effectiveWindow − 3K 为强制压缩触发线，绕过熔断器
MANUAL_COMPACT_SAFETY_MARGIN = 3_000

# Layer 2 "保留近期原文"窗口（对应 Claude Code compact.ts 的
# buildPostCompactMessages messagesToKeep）。压缩时，尾部消息按 token 累计不超过
# KEEP_RECENT_TOKENS、或消息数不少于 MIN_KEEP_MESSAGES（取先满足的条件保底）保留原文，
# 不纳入摘要。累计超过 KEEP_MAX_TOKENS 时停止，防止单条超大消息吞掉整个窗口。
KEEP_RECENT_TOKENS = 10_000
MIN_KEEP_MESSAGES = 5
KEEP_MAX_TOKENS = 40_000
ROLLOVER_KEEP_MAX_TOKENS = 4_000

# 前缀 token 数低于此阈值时不值得做摘要——摘要往返的开销比回收的空间还大，
# 退化为不压缩、保留原始历史（避免「压了个寂寞」）。
MIN_SUMMARIZE_PREFIX_TOKENS = 2_000

PERSISTED_TAG = "<persisted-output>"
TRUNCATED_FILE_LINE_MARKER = "(line truncated to "

SESSION_SUBDIR = ".lantu/session/tool-results"


# ---------------------------------------------------------------------------
# 事件
# ---------------------------------------------------------------------------


@dataclass
class CompactBoundary:
    """Layer 2 压缩的结构化结果，上交给 session 层处理。

    `summary` 是大模型对被摘要前缀生成的摘要；`keep` 是 auto_compact 原样保留、
    未做改动的近期尾部消息。session 层（持有 sessionId / 文件句柄）会把二者一起
    内联进一条 compact_boundary 记录，这样 resume 时就能重建压缩后的状态。
    用这种方式把写操作解耦出去，能让 auto_compact 保持纯粹、不依赖任何 session。
    """

    summary: str
    keep: list[Message]
    artifact_refs: list[str] = field(default_factory=list)


@dataclass
class CompactEvent:
    before_tokens: int
    # 摘要成功时填充，调用方可据此持久化 compact_boundary 记录。
    # 未产出摘要时为 None。
    boundary: CompactBoundary | None = None
    action: str = "structured_summary"
    tool_result_replacements: list[dict[str, str]] = field(default_factory=list)
    # 压缩前的上下文压力快照。第一阶段只提供诊断依据，不改变压缩行为。
    pressure: ContextPressureReport | None = None


@dataclass(frozen=True)
class ContextPressureReport:
    """Describe what currently occupies the context window.

    This is deliberately a pure diagnostic value. ``auto_compact`` still uses
    its existing full-summary path; a later policy layer can consume
    ``recommended_action`` without repeating the accounting rules.
    """

    current_tokens: int
    context_window: int
    usage_ratio: float
    pressure_level: str  # ``none``, ``soft`` or ``hard``
    keep_start: int
    stale_tool_tokens: int
    old_conversation_tokens: int
    recent_tokens: int
    existing_summary_tokens: int
    dominant_source: str
    recommended_action: str


class CompactionPolicy:
    """Choose the least disruptive response to a pressure snapshot.

    The policy is intentionally pure.  It does not rewrite messages; callers
    decide when and how to execute the returned action.
    """

    NONE = "none"
    DEFER = "defer"
    COMPACT_STALE_TOOLS = "compact_stale_tools"
    STRUCTURED_SUMMARY = "structured_summary"
    WINDOW_ROLLOVER = "window_rollover"

    @classmethod
    def decide(
        cls,
        report: ContextPressureReport,
        *,
        at_user_boundary: bool = True,
        cache_healthy: bool = True,
    ) -> str:
        """Return an action without mutating the conversation.

        Soft pressure is delayed while the prefix cache is healthy.  A local
        stale-tool eviction is safe to do at a user boundary; in the middle of
        a tool chain it is deferred.  Hard pressure requires a full summary,
        unless the working view already contains a previous summary, in which
        case the caller should consider a new window.
        """
        if report.pressure_level == "none":
            return cls.NONE

        if not at_user_boundary:
            return cls.DEFER

        if report.pressure_level == "hard" and report.existing_summary_tokens > 0:
            return cls.WINDOW_ROLLOVER

        if report.recommended_action == cls.COMPACT_STALE_TOOLS:
            return cls.COMPACT_STALE_TOOLS

        if report.pressure_level == "soft" and cache_healthy:
            return cls.DEFER

        return cls.STRUCTURED_SUMMARY


def compact_stale_tool_results(
    messages: list[Message],
    keep_start: int,
    session_dir: Path,
) -> list[Message]:
    """Evict old tool previews while keeping their full artifacts recoverable.

    Only messages before ``keep_start`` are eligible. Existing persisted
    previews are reduced to a reference; an unexpectedly large raw result is
    persisted first and then reduced. Small inline results and all recent
    messages are left byte-for-byte untouched.
    """
    if keep_start <= 0:
        return list(messages)

    path_pattern = re.compile(r"完整内容已保存到：\n([^\n]+)")
    compacted: list[Message] = []
    changed_any = False
    for index, message in enumerate(messages):
        if index >= keep_start or not message.tool_results:
            compacted.append(message)
            continue

        changed = False
        results: list[ToolResultBlock] = []
        for result in message.tool_results:
            content = result.content
            artifact_path: Path | None = None
            if PERSISTED_TAG in content:
                match = path_pattern.search(content)
                if match:
                    candidate = Path(match.group(1).strip())
                    if candidate.is_file():
                        artifact_path = candidate
            elif len(content) > TOOL_RESULT_INLINE_CHAR_LIMIT:
                candidate = persist_tool_result(result.tool_use_id, content, session_dir)
                if candidate.is_file():
                    artifact_path = candidate

            if artifact_path is not None:
                reference = make_persisted_reference(artifact_path)
                if reference != content:
                    content = reference
                    changed = True
            results.append(
                ToolResultBlock(
                    tool_use_id=result.tool_use_id,
                    content=content,
                    is_error=result.is_error,
                )
            )

        if changed:
            compacted.append(replace(message, tool_results=results))
            changed_any = True
        else:
            compacted.append(message)

    return compacted if changed_any else list(messages)


@dataclass(frozen=True)
class ToolResultPresentation:
    """How much of one tool result was actually placed in the conversation."""

    tool_use_id: str
    mode: str  # ``inline`` or ``artifact``
    artifact_path: str | None = None
    preview_line_count: int = 0
    visible_line_ranges: tuple[tuple[int, int], ...] = ()


@dataclass
class PreparedToolResults:
    results: list[ToolResultBlock]
    presentations: dict[str, ToolResultPresentation]


# ---------------------------------------------------------------------------
# Session 目录管理
# ---------------------------------------------------------------------------

def ensure_session_dir(work_dir: str) -> Path:
    session_dir = Path(work_dir) / SESSION_SUBDIR
    session_dir.mkdir(parents=True, exist_ok=True)
    return session_dir


def cleanup_tool_results(
    session_dir: Path,
    keep_messages: list[Message] | None = None,
) -> None:
    """Delete unreferenced tool-result artifacts.

    With no messages this keeps the old "clear everything" behavior used by
    callers that explicitly reset a result directory. Compaction passes the
    rebuilt messages so paths still visible to the model survive.
    """
    if not session_dir.exists():
        session_dir.mkdir(parents=True, exist_ok=True)
        return
    if keep_messages is None:
        shutil.rmtree(session_dir)
        session_dir.mkdir(parents=True, exist_ok=True)
        return

    referenced_text = "\n".join(
        [message.content for message in keep_messages]
        + [
            result.content
            for message in keep_messages
            for result in message.tool_results
        ]
    )
    for artifact in session_dir.glob("*.txt"):
        if str(artifact) not in referenced_text:
            try:
                artifact.unlink()
            except OSError:
                pass


# ---------------------------------------------------------------------------
# Layer 1：大型工具结果落盘
# ---------------------------------------------------------------------------

def persist_tool_result(tool_use_id: str, content: str, session_dir: Path) -> Path:
    file_path = session_dir / f"{tool_use_id}.txt"
    try:
        fd = os.open(str(file_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
    except FileExistsError:
        pass
    return file_path


def make_persisted_preview(
    content: str,
    file_path: Path,
    *,
    head_chars: int = TOOL_RESULT_HEAD_CHARS,
    tail_chars: int = TOOL_RESULT_TAIL_CHARS,
) -> str:
    size_kb = len(content.encode("utf-8")) // 1024
    preview = make_head_tail_preview(
        content,
        head_chars=head_chars,
        tail_chars=tail_chars,
    )
    if (
        head_chars == TOOL_RESULT_HEAD_CHARS
        and tail_chars == TOOL_RESULT_TAIL_CHARS
    ):
        preview_label = "前 4KB + 后 1KB"
    else:
        preview_label = f"前 {head_chars} 字符 + 后 {tail_chars} 字符"
    return (
        f"{PERSISTED_TAG}\n"
        f"输出太大（{size_kb}KB），完整内容已保存到：\n"
        f"{file_path}\n"
        f"\n"
        f"预览（{preview_label}）：\n"
        f"{preview}\n"
        f"</persisted-output>"
    )


def make_persisted_reference(file_path: Path) -> str:
    return (
        f"{PERSISTED_TAG}\n"
        "完整内容已保存到：\n"
        f"{file_path}\n"
        "</persisted-output>"
    )


def make_head_tail_preview(
    content: str,
    *,
    head_chars: int = TOOL_RESULT_HEAD_CHARS,
    tail_chars: int = TOOL_RESULT_TAIL_CHARS,
) -> str:
    """Keep the beginning and end of an oversized non-file tool result."""
    if len(content) <= head_chars + tail_chars:
        return content
    return (
        f"{content[:head_chars]}\n"
        "... [middle content omitted] ...\n"
        f"{content[-tail_chars:]}"
    )


def _line_number(line: str) -> int | None:
    prefix, separator, _ = line.partition("\t")
    if not separator:
        return None
    try:
        return int(prefix)
    except ValueError:
        return None


def _ranges_from_line_indexes(
    lines: list[str], indexes: list[int]
) -> tuple[tuple[int, int], ...]:
    numbers = sorted(
        number
        for index in indexes
        if 0 <= index < len(lines)
        and TRUNCATED_FILE_LINE_MARKER not in lines[index]
        for number in [_line_number(lines[index])]
        if number is not None
    )
    ranges: list[tuple[int, int]] = []
    for number in numbers:
        if not ranges or number > ranges[-1][1] + 1:
            ranges.append((number, number))
        else:
            ranges[-1] = (ranges[-1][0], number)
    return tuple(ranges)


def make_file_read_preview(
    content: str,
    *,
    head_chars: int = FILE_READ_HEAD_CHARS,
    tail_chars: int = FILE_READ_TAIL_CHARS,
) -> tuple[str, tuple[tuple[int, int], ...]]:
    """Keep complete numbered lines at both ends and report visible ranges."""
    if len(content) <= FILE_READ_PREVIEW_CHAR_LIMIT:
        lines = content.splitlines()
        return content, _ranges_from_line_indexes(lines, list(range(len(lines))))

    lines = content.splitlines()
    if not lines:
        return content, ()

    head_indexes: list[int] = []
    used = 0
    for index, line in enumerate(lines):
        cost = len(line) + (1 if head_indexes else 0)
        if head_indexes and used + cost > head_chars:
            break
        head_indexes.append(index)
        used += cost

    tail_indexes: list[int] = []
    used = 0
    for index in range(len(lines) - 1, -1, -1):
        if index in head_indexes:
            break
        line = lines[index]
        cost = len(line) + (1 if tail_indexes else 0)
        if tail_indexes and used + cost > tail_chars:
            break
        tail_indexes.append(index)
        used += cost
    tail_indexes.reverse()

    selected = head_indexes + tail_indexes
    if not tail_indexes:
        return "\n".join(lines[index] for index in head_indexes), _ranges_from_line_indexes(lines, selected)

    first_omitted = head_indexes[-1] + 1
    last_omitted = tail_indexes[0] - 1
    omitted_numbers = [
        _line_number(lines[index])
        for index in (first_omitted, last_omitted)
        if 0 <= index < len(lines)
    ]
    if len(omitted_numbers) == 2:
        marker = f"... [middle lines omitted: {omitted_numbers[0]}-{omitted_numbers[1]}] ..."
    else:
        marker = "... [middle lines omitted] ..."
    preview = "\n".join(
        [*(lines[index] for index in head_indexes), marker, *(lines[index] for index in tail_indexes)]
    )
    return preview, _ranges_from_line_indexes(lines, selected)


def prepare_tool_results_with_metadata(
    results: list[ToolResultBlock],
    session_dir: Path,
    *,
    file_read_ids: set[str] | None = None,
    already_persisted_ids: set[str] | None = None,
) -> PreparedToolResults:
    """Finalize one turn's tool results before adding them to conversation history."""
    prepared: list[ToolResultBlock] = []
    file_read_ids = file_read_ids or set()
    already_persisted_ids = already_persisted_ids or set()
    presentations: dict[str, ToolResultPresentation] = {}
    for result in results:
        content = result.content
        presentations[result.tool_use_id] = ToolResultPresentation(
            tool_use_id=result.tool_use_id,
            mode="inline",
        )
        if result.tool_use_id in file_read_ids:
            content, visible_ranges = make_file_read_preview(content)
            presentations[result.tool_use_id] = ToolResultPresentation(
                tool_use_id=result.tool_use_id,
                mode="file",
                visible_line_ranges=visible_ranges,
            )
        elif (
            result.tool_use_id not in file_read_ids
            and result.tool_use_id not in already_persisted_ids
            and len(content) > TOOL_RESULT_INLINE_CHAR_LIMIT
        ):
            path = persist_tool_result(result.tool_use_id, content, session_dir)
            content = make_persisted_preview(content, path)
            presentations[result.tool_use_id] = ToolResultPresentation(
                tool_use_id=result.tool_use_id,
                mode="artifact",
                artifact_path=str(path),
                preview_line_count=0,
            )
        prepared.append(
            ToolResultBlock(
                tool_use_id=result.tool_use_id,
                content=content,
                is_error=result.is_error,
            )
        )

    raw_by_id = {result.tool_use_id: result.content for result in results}
    total = sum(
        len(result.content)
        for result in prepared
        if result.tool_use_id not in file_read_ids
    )
    candidates = sorted(
        (
            (index, result)
            for index, result in enumerate(prepared)
            if (
                result.tool_use_id not in file_read_ids
                and result.tool_use_id not in already_persisted_ids
            )
        ),
        key=lambda item: len(item[1].content),
        reverse=True,
    )
    for index, result in candidates:
        if total <= AGGREGATE_CHAR_LIMIT:
            break
        raw_content = raw_by_id[result.tool_use_id]
        path = persist_tool_result(result.tool_use_id, raw_content, session_dir)
        preview = make_persisted_preview(
            raw_content,
            path,
            head_chars=AGGREGATE_PREVIEW_HEAD_CHARS,
            tail_chars=AGGREGATE_PREVIEW_TAIL_CHARS,
        )
        if len(preview) >= len(result.content):
            preview = make_persisted_reference(path)
        if len(preview) >= len(result.content):
            continue
        prepared[index] = ToolResultBlock(
            tool_use_id=result.tool_use_id,
            content=preview,
            is_error=result.is_error,
        )
        previous = presentations.get(result.tool_use_id)
        presentations[result.tool_use_id] = ToolResultPresentation(
            tool_use_id=result.tool_use_id,
            mode="artifact",
            artifact_path=str(path),
            preview_line_count=0,
            visible_line_ranges=previous.visible_line_ranges if previous else (),
        )
        total += len(preview) - len(result.content)

    if total > AGGREGATE_CHAR_LIMIT:
        artifact_candidates = sorted(
            (
                (index, result, presentations[result.tool_use_id])
                for index, result in enumerate(prepared)
                if result.tool_use_id not in file_read_ids
                and presentations[result.tool_use_id].artifact_path is not None
            ),
            key=lambda item: len(item[1].content),
            reverse=True,
        )
        for index, result, presentation in artifact_candidates:
            if total <= AGGREGATE_CHAR_LIMIT:
                break
            reference = make_persisted_reference(Path(presentation.artifact_path))
            if len(reference) >= len(result.content):
                continue
            prepared[index] = ToolResultBlock(
                tool_use_id=result.tool_use_id,
                content=reference,
                is_error=result.is_error,
            )
            total += len(reference) - len(result.content)

    return PreparedToolResults(prepared, presentations)


def prepare_tool_results(
    results: list[ToolResultBlock],
    session_dir: Path,
) -> list[ToolResultBlock]:
    """Backward-compatible wrapper returning only conversation blocks."""
    return prepare_tool_results_with_metadata(results, session_dir).results


# ---------------------------------------------------------------------------
# Layer 2：全对话摘要（Auto-Compact）
# ---------------------------------------------------------------------------

def compute_compact_threshold(context_window: int, manual: bool = False) -> int:
    effective = context_window - SUMMARY_OUTPUT_RESERVE
    margin = MANUAL_COMPACT_SAFETY_MARGIN if manual else AUTO_COMPACT_SAFETY_MARGIN
    return effective - margin


def should_auto_compact(last_input_tokens: int, context_window: int) -> bool:
    return last_input_tokens >= compute_compact_threshold(context_window)


COMPACTION_INSTRUCTION = """\
Create a durable, technically precise summary of the conversation above. The older
messages will be removed after compaction, so the summary must preserve every fact
needed to continue the work without reopening the full transcript.

Output rules:
- Return only the final summary. Do not output analysis, reasoning, preamble, or
  <analysis>/<summary> tags, and do not call tools.
- Use exactly the Markdown structure below. Keep the headings and field labels, but
  write field values in the main language of the conversation.
- Record only facts established by the conversation. If a field has no known facts,
  write "None". Never invent progress, decisions, causes, test results, or tasks.
- Preserve exact technical identifiers where they matter: repository-relative file
  paths, class/function/config names, commands, parameter values, thresholds, error
  messages, commit IDs, and measured test results.
- Distinguish confirmed facts from hypotheses or unresolved questions. Mark the
  latter explicitly instead of presenting them as conclusions.
- Summarize code rather than copying large snippets. Include a short exact snippet
  only when its precise text is required to continue safely.
- Do not reproduce the conversation chronologically, list every user message, or
  infer a next action. Outstanding work must come only from explicit user requests
  or work that was started but not completed.
- Give extra weight to the user's corrections and rejected approaches so they are
  not repeated after recovery.

Required output structure:

## Long-term goal
- Final objective: the user's durable end goal.
- Current scope: how the summarized work contributes to that goal.

## Constraints and confirmed decisions
- Requirements: explicit constraints, preferences, and acceptance criteria.
- Confirmed decisions: agreed architecture, behavior, interfaces, and trade-offs.
- Corrections and rejected approaches: user corrections and approaches that must not
  be repeated, including why when known.

## Completed work
- Implementation: completed changes, with relevant files and symbols.
- Verification: commands, tests, measurements, and their exact outcomes.
- Established findings: conclusions confirmed by inspection or execution.

## Outstanding work
- Explicit unfinished requests: requested work that is not complete.
- In-progress state: partially completed work and its exact stopping state.
- Blockers or unresolved questions: known obstacles, hypotheses, and missing evidence.

## Key files and code state
- Files and symbols: each important path plus the role and current state of its key
  classes, functions, configuration, or data structures.
- Runtime and data state: relevant modes, environment, persisted state, sessions,
  branches, commits, and uncommitted changes.
- Critical values: exact settings, limits, commands, errors, or small code fragments
  that must survive compaction.

## Historical problems and resolutions
- Problem: observable symptom or error.
- Confirmed cause: established root cause, or "Unconfirmed".
- Resolution and evidence: applied fix and verification result, or current status if
  unresolved.
"""


def extract_summary(llm_output: str) -> str:
    start = llm_output.find("<summary>")
    end = llm_output.find("</summary>")
    if start == -1 or end == -1:
        return llm_output
    return llm_output[start + len("<summary>"):end].strip()


def build_compact_messages(
    summary: str,
    attachment: str = "",
    has_keep_tail: bool = False,
    transcript_path: str = "",
) -> list[Message]:
    content = "本次会话延续自之前的对话，因上下文空间不足进行了压缩。以下是早期对话的摘要：\n\n" + summary
    if has_keep_tail:
        content += "\n\n近期消息已原样保留。"
    if transcript_path:
        content += f"\n\n如果你需要压缩前的具体细节（代码片段、报错信息等），请用 ReadFile 读取完整会话记录：{transcript_path}"
    if attachment:
        content += "\n\n---\n\n" + attachment
    return [
        Message(role="user", content=content),
    ]


def build_window_rollover_messages(
    summary: str,
    *,
    artifact_refs: list[str] | None = None,
    attachment: str = "",
    has_keep_tail: bool = False,
    transcript_path: str = "",
) -> list[Message]:
    """Build the first durable message of a new Conversation window."""
    content = (
        "本次会话已在同一个 Session 内切换到新的上下文窗口。"
        "旧窗口仍完整保存在 Journal 中，以下是继续当前任务所需的交接摘要：\n\n"
        + summary
    )
    if artifact_refs:
        content += (
            "\n\n## 历史工具结果索引\n"
            "以下文件仍由 Session 保留；需要原始结果时请重新使用读取或搜索工具：\n"
            + "\n".join(f"- {path}" for path in artifact_refs)
        )
    if has_keep_tail:
        content += "\n\n当前窗口的近期消息已原样保留。"
    if transcript_path:
        content += (
            "\n\n如需核对旧窗口中的用户原话或工具调用链，请用 ReadFile 读取完整会话记录："
            + transcript_path
        )
    if attachment:
        content += "\n\n---\n\n" + attachment
    return [Message(role="user", content=content)]


def _persisted_artifact_refs(messages: list[Message]) -> list[str]:
    """Collect stable artifact paths without retaining tool-result bodies."""
    pattern = re.compile(r"完整内容已保存到：\n([^\n]+)")
    refs: list[str] = []
    seen: set[str] = set()
    for message in messages:
        values = [message.content, *(result.content for result in message.tool_results)]
        for value in values:
            for match in pattern.finditer(value):
                path = match.group(1).strip()
                if path and path not in seen:
                    seen.add(path)
                    refs.append(path)
    return refs


# ---------------------------------------------------------------------------
# 压缩后恢复状态
# ---------------------------------------------------------------------------

# 追加到摘要 user 消息的恢复附件限制。compact 会清空工作对话；
# 没有这些快照，模型会忘记刚读过哪些文件、正在执行哪个 skill 的 SOP。
RECOVERY_FILE_LIMIT = 5
RECOVERY_TOKENS_PER_FILE = 5_000
RECOVERY_SKILLS_BUDGET = 25_000
RECOVERY_TOKENS_PER_SKILL = 5_000
_RECOVERY_CHARS_PER_TOKEN = 3.5


@dataclass
class FileReadRecord:
    path: str
    content: str
    timestamp: float


@dataclass
class SkillInvocationRecord:
    name: str
    body: str
    timestamp: float


class RecoveryState:
    """能在 Layer 2 压缩中存活下来的 per-agent 快照。

    记录 ReadFile 返回的字节内容，以及各个 skill 被调用时附带的 SOP 正文。
    这些记录会被重新附加到摘要的 user 消息上，这样即便对话记录被压缩清空，
    模型仍然保有可用的工作上下文。
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._files: dict[str, FileReadRecord] = {}
        self._skills: dict[str, SkillInvocationRecord] = {}

    def record_file_read(self, path: str, content: str) -> None:
        if not path:
            return
        with self._lock:
            self._files[path] = FileReadRecord(
                path=path, content=content, timestamp=time.time()
            )

    def record_skill_invocation(self, name: str, body: str) -> None:
        if not name:
            return
        with self._lock:
            self._skills[name] = SkillInvocationRecord(
                name=name, body=body, timestamp=time.time()
            )

    def snapshot_files(self, limit: int) -> list[FileReadRecord]:
        with self._lock:
            records = list(self._files.values())
        records.sort(key=lambda r: r.timestamp, reverse=True)
        if limit > 0:
            records = records[:limit]
        return records

    def snapshot_skills(self) -> list[SkillInvocationRecord]:
        with self._lock:
            records = list(self._skills.values())
        records.sort(key=lambda r: r.timestamp, reverse=True)
        return records


def _approx_tokens(s: str) -> int:
    if not s:
        return 0
    return int(len(s) / _RECOVERY_CHARS_PER_TOKEN)


def _truncate_by_tokens(s: str, token_budget: int) -> str:
    if token_budget <= 0 or not s:
        return s
    if _approx_tokens(s) <= token_budget:
        return s
    max_chars = int(token_budget * _RECOVERY_CHARS_PER_TOKEN)
    if max_chars <= 0 or max_chars >= len(s):
        return s
    return s[:max_chars] + "\n… (内容已截断)"


def _first_line(s: str) -> str:
    for line in s.split("\n"):
        stripped = line.strip()
        if stripped:
            return stripped
    return ""


def build_recovery_attachment(
    state: RecoveryState | None,
    tool_schemas: list[Mapping[str, Any]] | None,
) -> str:
    """渲染压缩后附件的四个小节。

    没有任何值得附加的内容时返回 ""，让调用方保持摘要消息干净。
    `tool_schemas` 应当是 agent 在下一次请求中将要发送的 schema —— 这里用其中的
    名称和描述来提醒模型当前都接入了哪些工具。
    """
    sections: list[str] = []

    if state is not None:
        files = state.snapshot_files(RECOVERY_FILE_LIMIT)
        if files:
            buf = ["## 最近读过的文件\n",
                   "以下快照是文件读取工具上次返回的内容。如需当前字节请重新读取。\n"]
            for rec in files:
                content = _truncate_by_tokens(rec.content, RECOVERY_TOKENS_PER_FILE)
                ts = time.strftime(
                    "%Y-%m-%dT%H:%M:%SZ", time.gmtime(rec.timestamp)
                )
                buf.append(f"### {rec.path}  (read {ts})\n")
                buf.append("```\n")
                buf.append(content)
                if not content.endswith("\n"):
                    buf.append("\n")
                buf.append("```\n")
            sections.append("".join(buf))

        skills = state.snapshot_skills()
        if skills:
            buf = ["## 已激活的技能\n",
                   "下列技能在本会话中被调用过，其触发条件仍然适用。\n"]
            used = 0
            emitted = False
            for sk in skills:
                body = _truncate_by_tokens(sk.body, RECOVERY_TOKENS_PER_SKILL)
                tokens = _approx_tokens(body) + _approx_tokens(sk.name) + 8
                if used + tokens > RECOVERY_SKILLS_BUDGET:
                    break
                used += tokens
                buf.append(f"### {sk.name}\n\n{body}\n")
                emitted = True
            if emitted:
                sections.append("".join(buf))

    if tool_schemas:
        buf = ["## 可用工具\n",
               "你仍然可以调用以下工具，需要时直接发起调用即可：\n"]
        for t in tool_schemas:
            name = t.get("name") if isinstance(t, Mapping) else None
            if not name:
                continue
            desc = t.get("description", "") if isinstance(t, Mapping) else ""
            desc = _first_line(desc or "")
            if desc:
                buf.append(f"- {name} — {desc}\n")
            else:
                buf.append(f"- {name}\n")
        sections.append("".join(buf))

    if not sections:
        return ""

    sections.append(
        "## 提示\n\n以上恢复的上下文是重建的。若需要原文代码、错误信息或用户原话，"
        "请用文件读取工具重新读取，不要根据摘要猜测细节。\n"
    )
    return "\n".join(sections)


def _group_messages_by_turn(messages: list[Message]) -> list[list[Message]]:
    groups: list[list[Message]] = []
    current: list[Message] = []
    for msg in messages:
        current.append(msg)
        if msg.role == "assistant" and not msg.tool_uses:
            groups.append(current)
            current = []
    if current:
        groups.append(current)
    return groups


def _message_tokens(msg: Message) -> int:
    """估算单条消息的 token 数，复用共享的字符数启发式算法。"""
    return estimate_tokens([msg])


def _compute_keep_start_index(messages: list[Message]) -> int:
    """决定压缩时尾部要原样保留多少条消息。

    从尾部向头部遍历 `messages`，逐条累加 token 估算值。只要还有任一保底条件
    未满足——累计 token 尚未达到 KEEP_RECENT_TOKENS，或保留的消息数仍少于
    MIN_KEEP_MESSAGES——当前消息就会被纳入保留窗口；但一旦纳入下一条消息会使
    保留总量超过 KEEP_MAX_TOKENS，遍历立即停止（这样单条超大的尾部消息就不会把
    整个 history 都拖进窗口）。

    返回第一条被保留消息的下标（keepStartIndex）。原始遍历结束后，必要时会把这个
    下标往前挪，确保被保留的 tool_result 不会和它对应的 tool_use 被拆散——
    参见 `_align_keep_start_to_tool_pair`。
    """
    n = len(messages)
    if n == 0:
        return 0

    kept_tokens = 0
    kept_count = 0
    keep_start = n  # 尚未保留任何消息

    for i in range(n - 1, -1, -1):
        tok = _message_tokens(messages[i])

        # 在已经保留了至少一条消息的前提下，如果纳入当前消息会突破硬上限则停止
        # （但绝不拒绝保留最后一条消息，即使它单独就超限）。
        if kept_count > 0 and kept_tokens + tok > KEEP_MAX_TOKENS:
            break

        kept_tokens += tok
        kept_count += 1
        keep_start = i

        # 保底条件已满足（token 下限或消息条数下限达到其一）：
        # 近期原文保留足够了，停止回溯。
        if kept_tokens >= KEEP_RECENT_TOKENS or kept_count >= MIN_KEEP_MESSAGES:
            break

    return _align_keep_start_to_tool_pair(messages, keep_start)


def _align_keep_start_to_tool_pair(messages: list[Message], keep_start: int) -> int:
    """把 keep_start 往前挪，确保我们绝不会保留一个孤立的 tool_result。

    携带 tool_results 的 user 消息，会和它前面那条发起对应 tool_uses 的 assistant
    消息配成一对。如果 keep_start 正好落在这样一条 user 消息上，就把它往前回退到
    （至少）配对的那条 assistant 消息，让 tool_use 与 tool_result 的配对关系保持完整。
    宁可多保留一对，也不要只保留半对（一个模型无法归属到任何调用的悬空 tool_result）。
    """
    while 0 < keep_start < len(messages):
        msg = messages[keep_start]
        if msg.role == "user" and msg.tool_results:
            prev = messages[keep_start - 1]
            if prev.role == "assistant" and prev.tool_uses:
                keep_start -= 1
                continue
        break
    return keep_start


def _compute_rollover_keep_tail(messages: list[Message]) -> list[Message]:
    """Keep only the final complete turn when starting a new context window."""
    if not messages:
        return []
    groups = _group_messages_by_turn(messages)
    tail = groups[-1] if groups else messages[-1:]
    while len(tail) > 1 and sum(_message_tokens(msg) for msg in tail) > ROLLOVER_KEEP_MAX_TOKENS:
        tail = tail[1:]
    return list(tail)


def _prefix_too_small_to_compact(prefix: list[Message]) -> bool:
    """当摘要 `prefix` 能回收的空间太少、不值得做时返回 True。"""
    if not prefix:
        return True
    return estimate_tokens(prefix) < MIN_SUMMARIZE_PREFIX_TOKENS


_COMPACT_SUMMARY_MARKER = "本次会话延续自之前的对话，因上下文空间不足进行了压缩。"


def _tool_result_tokens(messages: list[Message]) -> int:
    """Estimate only tool-result payloads, excluding their containing messages."""
    result_only_messages = [
        Message(role="user", content="", tool_results=list(message.tool_results))
        for message in messages
        if message.tool_results
    ]
    return estimate_tokens(result_only_messages)


def analyze_context_pressure(
    messages: list[Message],
    current_tokens: int,
    context_window: int,
) -> ContextPressureReport:
    """Classify context pressure without mutating conversation history."""
    keep_start = _compute_keep_start_index(messages)
    old_messages = messages[:keep_start]
    recent_messages = messages[keep_start:]

    old_tokens = estimate_tokens(old_messages)
    stale_tool_tokens = _tool_result_tokens(old_messages)
    existing_summary_tokens = sum(
        _message_tokens(message)
        for message in messages
        if _COMPACT_SUMMARY_MARKER in message.content
    )
    old_summary_tokens = sum(
        _message_tokens(message)
        for message in old_messages
        if _COMPACT_SUMMARY_MARKER in message.content
    )
    old_conversation_tokens = max(
        0,
        old_tokens - stale_tool_tokens - old_summary_tokens,
    )
    recent_tokens = estimate_tokens(recent_messages)

    soft_threshold = compute_compact_threshold(context_window, manual=False)
    hard_threshold = compute_compact_threshold(context_window, manual=True)
    if current_tokens < soft_threshold:
        pressure_level = "none"
    elif current_tokens < hard_threshold:
        pressure_level = "soft"
    else:
        pressure_level = "hard"

    sources = {
        "stale_tool_results": stale_tool_tokens,
        "old_conversation": old_conversation_tokens,
        "existing_summary": existing_summary_tokens,
    }
    dominant_source = (
        "none" if pressure_level == "none" else max(sources, key=sources.get)
    )

    if pressure_level == "none":
        recommended_action = "none"
    elif pressure_level == "hard" and existing_summary_tokens > 0:
        # A previously compacted working view has filled again. The first
        # phase only marks it as a rollover candidate; it does not split it.
        recommended_action = "window_rollover_candidate"
    elif old_tokens > 0 and stale_tool_tokens * 2 >= old_tokens:
        recommended_action = "compact_stale_tools"
    else:
        recommended_action = "structured_summary"

    return ContextPressureReport(
        current_tokens=current_tokens,
        context_window=context_window,
        usage_ratio=(current_tokens / context_window) if context_window > 0 else 0.0,
        pressure_level=pressure_level,
        keep_start=keep_start,
        stale_tool_tokens=stale_tool_tokens,
        old_conversation_tokens=old_conversation_tokens,
        recent_tokens=recent_tokens,
        existing_summary_tokens=existing_summary_tokens,
        dominant_source=dominant_source,
        recommended_action=recommended_action,
    )


# ---------------------------------------------------------------------------
# 熔断器
# ---------------------------------------------------------------------------


@dataclass
class CompactCircuitBreaker:
    max_failures: int = 3
    consecutive_failures: int = field(default=0, init=False)

    def record_failure(self) -> None:
        self.consecutive_failures += 1

    def record_success(self) -> None:
        self.consecutive_failures = 0


    def is_open(self) -> bool:
        return self.consecutive_failures >= self.max_failures


# ---------------------------------------------------------------------------
# UsageAnchor — 真实 API 用量锚点（独立类型）
# ---------------------------------------------------------------------------


@dataclass
class UsageAnchor:
    """记录上一次真实 API 用量和当时的对话长度。

    baseline_tokens 是 input + cache_read + cache_creation + output 的合计值；
    anchor_count 是记录该数值时的 conversation.history 长度。锚点之后新增的消息
    没有真实用量数据，仅做字符估算。has_usage 为 False 时表示尚未收到任何 API
    用量报告（冷启动），此时退化为对整个 history 做字符估算。
    """

    baseline_tokens: int = 0
    anchor_count: int = 0
    has_usage: bool = False

    @staticmethod
    def from_api_usage(
        input_tokens: int,
        output_tokens: int = 0,
        cache_read: int = 0,
        cache_creation: int = 0,
        msg_count: int = 0,
    ) -> UsageAnchor:
        """根据一次 API 响应构造锚点。"""
        return UsageAnchor(
            baseline_tokens=input_tokens + cache_read + cache_creation + output_tokens,
            anchor_count=msg_count,
            has_usage=True,
        )


# ---------------------------------------------------------------------------
# Auto-compact 编排器
# ---------------------------------------------------------------------------

async def auto_compact(
    conversation: ConversationManager,
    client: Any,
    context_window: int,
    session_dir: Path,
    protocol: str = "anthropic",
    manual: bool = False,
    breaker: CompactCircuitBreaker | None = None,
    recovery: RecoveryState | None = None,
    tool_schemas: list[Mapping[str, Any]] | None = None,
    transcript_path: str = "",
    budget_messages: list[Message] | None = None,
    system_prompt: str = "",
    request_recorder: Any | None = None,
    at_user_boundary: bool = True,
    cache_healthy: bool = True,
) -> CompactEvent | str | None:
    # 以真实 API 用量为锚点做阈值判断：current_tokens() 返回上次计费基准
    # （input + cache_read + cache_creation + output）加上锚点之后新增消息的
    # 字符估算。冷启动或刚压缩清空锚点时，退化为对整个 history 做字符估算。
    current = conversation.current_tokens()
    pressure = analyze_context_pressure(
        conversation.history,
        current_tokens=current,
        context_window=context_window,
    )

    if manual:
        # 手动压缩（/compact）：直接走压缩流程，不检查阈值
        pass
    else:
        # 双阈值判断，对齐 Go ManageContext 逻辑：
        # 1) 软触发线（auto margin 13K）：低于此线不需要压缩
        soft_threshold = compute_compact_threshold(context_window, manual=False)
        if current < soft_threshold:
            return None

        # 2) 硬触发线（manual margin 3K）：超过此线强制压缩，绕过熔断器，
        #    因为上下文已经过于接近窗口上限，不能冒跳过的风险
        hard_threshold = compute_compact_threshold(context_window, manual=True)
        if current >= hard_threshold:
            # 强制压缩路径：不检查熔断器
            pass
        else:
            # 处于软硬阈值之间：走正常的熔断器保护逻辑
            if breaker is not None and breaker.is_open():
                return "自动压缩已熔断（连续失败 3 次），请手动处理或使用 /compact"

    policy_action = CompactionPolicy.decide(
        pressure,
        at_user_boundary=at_user_boundary,
        cache_healthy=cache_healthy,
    )
    if not manual and policy_action in {
        CompactionPolicy.NONE,
        CompactionPolicy.DEFER,
    }:
        return None

    if not manual and policy_action == CompactionPolicy.COMPACT_STALE_TOOLS:
        original_history = conversation.history
        compacted_history = compact_stale_tool_results(
            original_history,
            pressure.keep_start,
            session_dir,
        )
        if compacted_history != original_history:
            replacements: list[dict[str, str]] = []
            for old_message, new_message in zip(original_history, compacted_history):
                old_results = {
                    result.tool_use_id: result.content
                    for result in old_message.tool_results
                }
                for result in new_message.tool_results:
                    old_content = old_results.get(result.tool_use_id)
                    if old_content is not None and old_content != result.content:
                        replacements.append(
                            {
                                "tool_use_id": result.tool_use_id,
                                "content": result.content,
                            }
                        )
            conversation.replace_history_preserving_runtime(compacted_history)
            return CompactEvent(
                before_tokens=current,
                action=CompactionPolicy.COMPACT_STALE_TOOLS,
                pressure=pressure,
                tool_result_replacements=replacements,
            )

    before_tokens = current

    # 对齐 Claude Code：先应用 tool-result budget，再做 auto-compact
    # 当调用方提前对 conversation 做了 budget 替换，把替换后的消息列表传入
    # budget_messages，这样 keep_start 的计算和摘要构建都基于缩减后的 token
    # 估算，让阈值判断更准确。最终仍然重写 conversation.history（原始对话）。
    effective_history = budget_messages if budget_messages else conversation.history

    # 决定保留多少尾部消息原文。只有前缀 messages[:keep_start] 会被摘要；
    # messages[keep_start:] 原样保留，让模型看到近期原文而非靠有损摘要复述。
    keep_start = _compute_keep_start_index(effective_history)
    to_summarize = effective_history[:keep_start]
    keep_tail = effective_history[keep_start:]

    # 待摘要的前缀太小时退化为不压缩——要么全部消息都落在保留窗口内
    # （keep_start <= 0），要么摘要回收的 token 还不够摘要本身的开销。
    if keep_start <= 0 or _prefix_too_small_to_compact(to_summarize):
        return None

    summary_conv = ConversationManager()
    # 只摘要前缀；保留的尾部在下面重建时原样拼回。
    summary_conv.history = list(to_summarize)
    # 使用原始 system/tools 和原始历史前缀，只在末尾追加压缩指令，
    # 使摘要请求能够复用正常请求已经建立的前缀缓存。
    summary_conv.history.append(
        Message(role="user", content=COMPACTION_INSTRUCTION)
    )

    max_retries = 3
    llm_output: str | None = None

    for attempt in range(max_retries):
        try:
            from lantu.tools.base import StreamEnd, StreamEvent, TextDelta

            collected_text = ""
            stream = (
                request_recorder.stream(
                    client,
                    summary_conv,
                    system=system_prompt,
                    tools=tool_schemas,
                    call_kind="compaction",
                )
                if request_recorder is not None
                else client.stream(
                    summary_conv,
                    system=system_prompt,
                    tools=tool_schemas,
                )
            )
            async for event in stream:
                if isinstance(event, TextDelta):
                    collected_text += event.text
                elif isinstance(event, StreamEnd):
                    pass
            llm_output = collected_text
            break

        except Exception as e:
            err_msg = str(e).lower()
            if ("prompt" in err_msg and "long" in err_msg) or "too many" in err_msg:
                groups = _group_messages_by_turn(summary_conv.history[:-1])
                drop_count = max(1, len(groups) // 5)
                remaining = groups[drop_count:]
                summary_conv.history = (
                    [m for g in remaining for m in g]
                    + [summary_conv.history[-1]]
                )
                continue
            if breaker is not None:
                breaker.record_failure()
            return f"摘要生成失败: {e}"

    if llm_output is None:
        if breaker is not None:
            breaker.record_failure()
        return "摘要生成失败：多次重试后仍超出上下文限制"

    summary = extract_summary(llm_output)
    artifact_refs = _persisted_artifact_refs(effective_history)
    # 重建 = 交接摘要(user) + 尾部原文。窗口切换会显式保留 artifact
    # 索引；完整结果仍由 Session 持有，绝不复制回新窗口。
    if not manual and policy_action == CompactionPolicy.WINDOW_ROLLOVER:
        rollover_tail = _compute_rollover_keep_tail(effective_history)
        new_messages = build_window_rollover_messages(
            summary,
            artifact_refs=artifact_refs,
            has_keep_tail=bool(rollover_tail),
            transcript_path=transcript_path,
        )
        boundary_keep = rollover_tail
    else:
        attachment = build_recovery_attachment(recovery, tool_schemas)
        new_messages = build_compact_messages(
            summary,
            attachment=attachment,
            has_keep_tail=bool(keep_tail),
            transcript_path=transcript_path,
        )
        boundary_keep = keep_tail
    new_messages = new_messages + list(boundary_keep)

    # replace_history 替换为重建后的对话并将用量锚点清零
    # （baseline_tokens / anchor_count / last_input_tokens），这是必须的：
    # 旧的 anchor_count 对应压缩前的消息列表，现在已无意义，
    # 不清零会导致 current_tokens() 对增量的估算出错。
    # 下一次 API 响应会基于重建后的 history 重新锚定。
    conversation.replace_history(new_messages)
    # Artifact files are part of the Session's recoverable fact layer.  A
    # compacted Conversation may no longer reference them, but the Journal can
    # still point to the original tool result.  Session deletion owns cleanup.
    conversation.reset_appendix_baseline()

    if breaker is not None:
        breaker.record_success()

    # 将结构化的 boundary（摘要 + 保留的尾部原文）交给 session 层，
    # 由它持久化为一条 compact_boundary 记录。keep tail 就是拼回重建 history 的那段。
    return CompactEvent(
        before_tokens=before_tokens,
        boundary=CompactBoundary(
            summary=summary,
            keep=list(boundary_keep),
            artifact_refs=artifact_refs,
        ),
        action=(
            CompactionPolicy.WINDOW_ROLLOVER
            if not manual and policy_action == CompactionPolicy.WINDOW_ROLLOVER
            else CompactionPolicy.STRUCTURED_SUMMARY
        ),
        pressure=pressure,
    )
