# Lantu Inline Terminal Frontend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 `lantu` 默认交互界面改为 Claude Code 风格的终端内联会话，同时保留 `lantu --tui` 访问现有 Textual 前端。

**Architecture:** 新前端通过现有 `AgentEvent` 消费 Agent 输出，将完成内容永久写入终端 scrollback，只用一个 Rich Live 区域刷新当前回复和运行中的工具。输入由 `prompt_toolkit` 管理，运行时装配放入独立模块，具体渲染组件全部位于 `lantu/ui/`，不继续扩充旧 `lantu/app.py`。

**Tech Stack:** Python 3.11+、Rich、prompt_toolkit、现有 AgentEvent/CommandRegistry、pytest、pytest-asyncio、pexpect。

---

## 文件结构

新增和修改后的职责如下：

```text
lantu/
├── __main__.py                     # CLI 模式路由
├── runtime/
│   ├── __init__.py                 # 导出运行时 API
│   ├── models.py                   # InteractiveRuntime 数据模型
│   ├── builder.py                  # Agent、工具、Skill、Team、Worktree 装配
│   └── lifecycle.py                # MCP、后台任务和关闭流程
├── ui/
│   ├── __init__.py
│   ├── shared/
│   │   ├── __init__.py
│   │   ├── theme.py                # 颜色 token 和符号
│   │   ├── formatting.py           # 版本、路径、耗时、Token 格式化
│   │   ├── models.py               # 与框架无关的视图状态
│   │   └── references.py           # @ 文件扫描和展开
│   └── inline/
│       ├── __init__.py
│       ├── app.py                  # 输入循环与前端生命周期
│       ├── commands.py             # CommandContext 和命令分发适配
│       ├── event_handler.py        # AgentEvent -> 视图状态
│       ├── live.py                 # 唯一动态区域
│       ├── session.py              # PromptSession 和补全
│       ├── transcript.py           # 永久输出
│       └── components/
│           ├── __init__.py
│           ├── header.py
│           ├── message.py
│           ├── tool.py
│           ├── status.py
│           └── interaction.py
├── commands/handlers/exit.py       # /exit 与 /quit
└── app.py                          # 旧 Textual 前端，仅做兼容接线

tests/
├── test_inline_formatting.py
├── test_inline_components.py
├── test_inline_live.py
├── test_inline_session.py
├── test_interactive_runtime.py
├── test_inline_app.py
├── test_cli_modes.py
└── test_inline_pty.py
```

## Task 1: 共享主题、格式化与像素字标

**Files:**
- Modify: `pyproject.toml`
- Create: `lantu/ui/__init__.py`
- Create: `lantu/ui/shared/__init__.py`
- Create: `lantu/ui/shared/theme.py`
- Create: `lantu/ui/shared/formatting.py`
- Create: `lantu/ui/shared/models.py`
- Create: `lantu/ui/inline/components/__init__.py`
- Create: `lantu/ui/inline/components/header.py`
- Test: `tests/test_inline_formatting.py`
- Test: `tests/test_inline_components.py`

- [ ] **Step 1: 添加显式前端依赖和测试依赖**

在 `pyproject.toml` 的运行依赖中加入：

```toml
"rich>=13.9.0",
"prompt-toolkit>=3.0.48",
```

在 dev dependency group 中加入：

```toml
"pexpect>=4.9.0",
```

- [ ] **Step 2: 写格式化和标题组件的失败测试**

```python
from io import StringIO

from rich.console import Console

from lantu.ui.inline.components.header import render_header
from lantu.ui.shared.formatting import format_tokens, shorten_home


def render_text(renderable, width: int = 100) -> str:
    output = StringIO()
    Console(file=output, force_terminal=False, width=width).print(renderable)
    return output.getvalue()


def test_shorten_home(monkeypatch):
    monkeypatch.setenv("HOME", "/Users/demo")
    assert shorten_home("/Users/demo/work/repo") == "~/work/repo"


def test_format_tokens():
    assert format_tokens(18_200, 128_000) == "18.2k/128k"


def test_header_uses_pixel_wordmark_on_wide_terminal():
    text = render_text(
        render_header("deepseek-chat", "default", "/tmp/project", version="0.2.0"),
        width=100,
    )
    assert "█▀█" in text
    assert "0.2.0 · deepseek-chat · default" in text
    assert "/tmp/project" in text


def test_header_falls_back_on_narrow_terminal():
    text = render_text(
        render_header("deepseek-chat", "default", "/tmp/project", version="0.2.0", width=35),
        width=35,
    )
    assert "LANTU 0.2.0" in text
    assert "█▀█" not in text


def test_header_has_ascii_fallback():
    text = render_text(
        render_header(
            "deepseek-chat",
            "default",
            "/tmp/project",
            version="0.2.0",
            unicode=False,
        )
    )
    assert "LANTU 0.2.0" in text
    assert "█" not in text
```

- [ ] **Step 3: 运行测试并确认失败**

Run: `uv run pytest tests/test_inline_formatting.py tests/test_inline_components.py -q`

Expected: FAIL，提示 `lantu.ui` 或目标函数不存在。

- [ ] **Step 4: 实现共享主题、格式化和视图模型**

`lantu/ui/shared/theme.py` 定义语义样式，不写背景色：

```python
from dataclasses import dataclass


@dataclass(frozen=True)
class InlineTheme:
    accent: str = "bold cyan"
    text: str = "default"
    muted: str = "dim"
    success: str = "green"
    warning: str = "yellow"
    error: str = "bold red"
    user_marker: str = "bold cyan"


DEFAULT_THEME = InlineTheme()
```

`lantu/ui/shared/formatting.py` 提供稳定格式化 API：

```python
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path


def package_version() -> str:
    try:
        return version("lantu")
    except PackageNotFoundError:
        return "0.0.0"


def shorten_home(path: str) -> str:
    value = str(Path(path).expanduser())
    home = str(Path.home())
    return "~" + value[len(home):] if value == home or value.startswith(home + "/") else value


def format_tokens(used: int, limit: int) -> str:
    def compact(value: int) -> str:
        if value >= 1000:
            scaled = value / 1000
            return f"{scaled:.1f}k" if scaled < 100 else f"{scaled:.0f}k"
        return str(value)

    return f"{compact(used)}/{compact(limit)}"


def format_elapsed(seconds: float) -> str:
    return f"{seconds:.1f}s" if seconds < 60 else f"{int(seconds // 60)}m {int(seconds % 60)}s"
```

`lantu/ui/shared/models.py` 定义框架无关状态：

```python
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ToolStatus(str, Enum):
    RUNNING = "running"
    SUCCESS = "success"
    ERROR = "error"


@dataclass
class ToolViewState:
    tool_id: str
    name: str
    arguments: dict[str, Any]
    status: ToolStatus = ToolStatus.RUNNING
    output: str = ""
    elapsed: float = 0.0


@dataclass
class LiveViewState:
    assistant_text: str = ""
    thinking_text: str = ""
    tools: dict[str, ToolViewState] = field(default_factory=dict)
    status_text: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
```

- [ ] **Step 5: 实现响应式像素字标**

`render_header` 接受显式 `width` 以便测试；未传时读取 `Console.size.width` 的调用方值。

```python
from rich.console import Group, RenderableType
from rich.text import Text

from lantu.ui.shared.formatting import package_version, shorten_home
from lantu.ui.shared.theme import DEFAULT_THEME


def render_header(
    model: str,
    mode: str,
    work_dir: str,
    *,
    version: str | None = None,
    width: int = 80,
    unicode: bool = True,
) -> RenderableType:
    release = version or package_version()
    path = shorten_home(work_dir)
    if width < 48 or not unicode:
        return Group(
            Text(f"LANTU {release} · {model}", style=DEFAULT_THEME.accent),
            Text(path, style=DEFAULT_THEME.muted, overflow="ellipsis"),
        )

    first = Text("┃  █▀█ █▄░█ ▀█▀ █░█", style=DEFAULT_THEME.accent)
    second = Text("┃  █▀█ █░▀█  █  █▄█", style=DEFAULT_THEME.accent)
    third = Text("┗━━ ", style=DEFAULT_THEME.accent)
    third.append(f"{release} · {model} · {mode}", style=DEFAULT_THEME.muted)
    fourth = Text(f"    {path}", style=DEFAULT_THEME.muted, overflow="ellipsis")
    return Group(first, second, third, fourth)
```

- [ ] **Step 6: 运行测试并提交**

Run: `uv run pytest tests/test_inline_formatting.py tests/test_inline_components.py -q`

Expected: PASS。

```bash
git add pyproject.toml uv.lock lantu/ui tests/test_inline_formatting.py tests/test_inline_components.py
git commit -m "feat: 添加内联前端主题与启动标识"
```

## Task 2: 静态 Transcript 和消息工具组件

**Files:**
- Create: `lantu/ui/inline/components/message.py`
- Create: `lantu/ui/inline/components/tool.py`
- Create: `lantu/ui/inline/components/status.py`
- Create: `lantu/ui/inline/transcript.py`
- Modify: `tests/test_inline_components.py`

- [ ] **Step 1: 写消息和工具摘要失败测试**

```python
from lantu.ui.inline.components.message import (
    render_assistant_message,
    render_error_message,
    render_user_message,
)
from lantu.ui.inline.components.tool import (
    render_tool,
    render_tool_details,
    summarize_tool_output,
)
from lantu.ui.shared.models import ToolStatus, ToolViewState


def test_user_and_assistant_markers_are_distinct():
    assert "❯ 修改配置" in render_text(render_user_message("修改配置"))
    assert "● 已完成" in render_text(render_assistant_message("已完成"))


def test_error_has_symbol_without_relying_on_color():
    assert "✗ 网络错误" in render_text(render_error_message("网络错误"))


def test_read_tool_summary_is_compact():
    tool = ToolViewState(
        tool_id="t1",
        name="ReadFile",
        arguments={"file_path": "lantu/config.py"},
        status=ToolStatus.SUCCESS,
        output="line\n" * 12,
        elapsed=0.2,
    )
    text = render_text(render_tool(tool))
    assert "ReadFile lantu/config.py" in text
    assert "读取 12 行" in text
    assert "line\nline\nline" not in text


def test_long_output_summary_is_bounded():
    summary = summarize_tool_output("Bash", "x" * 10_000, False)
    assert len(summary) <= 180


def test_tool_details_preserve_output():
    tool = ToolViewState("t1", "Bash", {"command": "pwd"}, output="/tmp/project")
    assert "/tmp/project" in render_text(render_tool_details(tool))
```

- [ ] **Step 2: 运行测试并确认失败**

Run: `uv run pytest tests/test_inline_components.py -q`

Expected: FAIL，提示消息和工具组件不存在。

- [ ] **Step 3: 实现消息组件**

```python
from rich.console import RenderableType
from rich.markdown import Markdown
from rich.text import Text

from lantu.ui.shared.theme import DEFAULT_THEME


def render_user_message(content: str) -> RenderableType:
    text = Text("❯ ", style=DEFAULT_THEME.user_marker)
    text.append(content)
    return text


def render_assistant_message(content: str) -> RenderableType:
    return Markdown(f"● {content}")


def render_system_message(content: str) -> RenderableType:
    return Text(f"  {content}", style=DEFAULT_THEME.muted)


def render_error_message(content: str) -> RenderableType:
    return Text(f"✗ {content}", style=DEFAULT_THEME.error)
```

- [ ] **Step 4: 实现工具摘要和状态组件**

工具摘要规则必须是确定性的：Read 按行数，Write/Edit 按文件，Bash 优先最后一行，
其他工具使用单行截断。`render_tool` 第一行显示状态符号和关键参数，第二行显示摘要与
耗时。失败状态必须使用 `✗`。

```python
def summarize_tool_output(name: str, output: str, is_error: bool) -> str:
    clean = " ".join(output.strip().split())
    if name in {"Read", "ReadFile"}:
        return f"读取 {len(output.splitlines())} 行"
    if name in {"Write", "WriteFile", "Edit", "EditFile"}:
        return "修改已写入" if not is_error else clean[:180]
    if name == "Bash":
        lines = [line.strip() for line in output.splitlines() if line.strip()]
        return (lines[-1] if lines else "命令完成")[:180]
    return clean[:180]


def render_tool_details(state: ToolViewState) -> RenderableType:
    heading = Text(f"{state.name} 详细输出", style=DEFAULT_THEME.muted)
    body = Text(state.output or "(无输出)")
    return Group(heading, body)
```

- [ ] **Step 5: 实现 TranscriptRenderer**

```python
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

    def header(self, model: str, mode: str, work_dir: str, version: str | None = None) -> None:
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
```

- [ ] **Step 6: 运行测试并提交**

Run: `uv run pytest tests/test_inline_components.py -q`

Expected: PASS。

```bash
git add lantu/ui/inline/components lantu/ui/inline/transcript.py tests/test_inline_components.py
git commit -m "feat: 添加内联消息与工具摘要组件"
```

## Task 3: 唯一动态区域和 AgentEvent 状态机

**Files:**
- Create: `lantu/ui/inline/live.py`
- Create: `lantu/ui/inline/event_handler.py`
- Test: `tests/test_inline_live.py`

- [ ] **Step 1: 写 Live 生命周期和事件转换失败测试**

```python
import asyncio

import pytest

from lantu.agent import LoopComplete, StreamText, ToolResultEvent, ToolUseEvent
from lantu.ui.inline.event_handler import InlineEventHandler
from lantu.ui.shared.models import ToolStatus


class FakeLiveRenderer:
    def __init__(self):
        self.states = []
        self.stopped = 0

    def update(self, state):
        self.states.append(state)

    def stop(self):
        self.stopped += 1


class FakeTranscript:
    def __init__(self):
        self.assistant = []
        self.tools = []

    def assistant_message(self, text):
        self.assistant.append(text)

    def tool(self, state):
        self.tools.append(state)


@pytest.mark.asyncio
async def test_stream_text_commits_before_tool():
    live = FakeLiveRenderer()
    transcript = FakeTranscript()
    handler = InlineEventHandler(live, transcript)

    await handler.handle(StreamText("先读取文件"))
    await handler.handle(ToolUseEvent("ReadFile", "t1", {"file_path": "a.py"}))

    assert transcript.assistant == ["先读取文件"]
    assert handler.state.tools["t1"].status is ToolStatus.RUNNING


@pytest.mark.asyncio
async def test_tool_result_is_committed_once():
    live = FakeLiveRenderer()
    transcript = FakeTranscript()
    handler = InlineEventHandler(live, transcript)
    await handler.handle(ToolUseEvent("ReadFile", "t1", {"file_path": "a.py"}))
    await handler.handle(ToolResultEvent("t1", "ReadFile", "a\nb\n", False, 0.1))
    await handler.handle(LoopComplete(1))

    assert len(transcript.tools) == 1
    assert transcript.tools[0].status is ToolStatus.SUCCESS
    assert handler.last_tool is transcript.tools[0]
    assert live.stopped >= 1
```

- [ ] **Step 2: 运行测试并确认失败**

Run: `uv run pytest tests/test_inline_live.py -q`

Expected: FAIL，提示 `InlineEventHandler` 不存在。

- [ ] **Step 3: 实现 LiveRenderer**

`LiveRenderer` 必须延迟创建 Rich `Live`，并允许测试注入 factory：

```python
from collections.abc import Callable

from rich.console import Console
from rich.live import Live

from lantu.ui.inline.components.status import render_live_state
from lantu.ui.shared.models import LiveViewState


class LiveRenderer:
    def __init__(self, console: Console, live_factory: Callable[..., Live] = Live) -> None:
        self.console = console
        self.live_factory = live_factory
        self._live: Live | None = None

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
        else:
            self._live.update(renderable, refresh=True)

    def stop(self) -> None:
        if self._live is not None:
            self._live.stop()
            self._live = None
```

- [ ] **Step 4: 实现 InlineEventHandler 状态机**

处理规则：

- `StreamText` 追加当前 assistant buffer。
- `ToolUseEvent` 前先提交 assistant buffer，再登记运行中工具。
- `ToolResultEvent` 停止 Live、提交该工具一次、从动态状态删除工具。
- `UsageEvent` 更新 token 状态。
- `RetryEvent`、`HookEvent`、`CompactNotification` 提交为系统消息。
- `ErrorEvent` 先提交未完成文本，再提交错误。
- `TurnComplete` 不重复提交；`LoopComplete` 提交剩余文本并停止 Live。
- `PermissionRequest` 交给注入的异步 permission callback。

核心接口和状态转换固定为：

```python
import logging
from collections.abc import Awaitable, Callable

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

    def _commit_assistant(self) -> None:
        text = self.state.assistant_text.strip()
        if text:
            self.live.stop()
            self.transcript.assistant_message(text)
        self.state.assistant_text = ""

    async def handle(self, event: AgentEvent) -> None:
        if isinstance(event, StreamText):
            self.state.assistant_text += event.text
        elif isinstance(event, ThinkingText):
            self.state.thinking_text += event.text
        elif isinstance(event, ToolUseEvent):
            self._commit_assistant()
            self.state.tools[event.tool_id] = ToolViewState(
                tool_id=event.tool_id,
                name=event.tool_name,
                arguments=event.arguments,
            )
        elif isinstance(event, ToolResultEvent):
            tool = self.state.tools.pop(
                event.tool_id,
                ToolViewState(event.tool_id, event.tool_name, {}),
            )
            tool.status = ToolStatus.ERROR if event.is_error else ToolStatus.SUCCESS
            tool.output = event.output
            tool.elapsed = event.elapsed
            self.last_tool = tool
            self.live.stop()
            self.transcript.tool(tool)
        elif isinstance(event, UsageEvent):
            self.state.input_tokens = event.input_tokens
            self.state.output_tokens = event.output_tokens
        elif isinstance(event, RetryEvent):
            self.transcript.system_message(f"↻ Retrying: {event.reason}")
        elif isinstance(event, HookEvent):
            marker = "✓" if event.success else "✗"
            self.transcript.system_message(f"Hook [{event.hook_id}] {marker} {event.output}")
        elif isinstance(event, CompactNotification):
            self.transcript.system_message(event.message)
        elif isinstance(event, ErrorEvent):
            self._commit_assistant()
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
            log.debug("Unhandled AgentEvent: %r", event)

        if self.state.assistant_text or self.state.thinking_text or self.state.tools:
            self.live.update(self.state)

    def finish(self) -> None:
        self._commit_assistant()
        self.state.thinking_text = ""
        self.state.tools.clear()
        self.live.stop()
```

未知事件只记录 debug 日志，不得中断输入循环。

- [ ] **Step 5: 运行测试并提交**

Run: `uv run pytest tests/test_inline_live.py tests/test_inline_components.py -q`

Expected: PASS。

```bash
git add lantu/ui/inline/live.py lantu/ui/inline/event_handler.py lantu/ui/inline/components/status.py tests/test_inline_live.py
git commit -m "feat: 添加内联流式渲染状态机"
```

## Task 4: PromptSession、命令补全和文件引用

**Files:**
- Create: `lantu/ui/shared/references.py`
- Create: `lantu/ui/inline/session.py`
- Modify: `lantu/app.py`
- Test: `tests/test_inline_session.py`

- [ ] **Step 1: 写补全和引用失败测试**

```python
import pytest
from prompt_toolkit.document import Document

from lantu.commands.handlers import register_all_commands
from lantu.commands.registry import CommandRegistry
from lantu.config import ProviderConfig
from lantu.ui.inline.session import InlineCompleter, select_provider
from lantu.ui.shared.references import expand_at_refs, scan_files


def test_slash_completion_uses_command_registry(tmp_path):
    registry = CommandRegistry()
    register_all_commands(registry)
    completer = InlineCompleter(registry, str(tmp_path))
    values = [
        item.text
        for item in completer.get_completions(Document("/he", 3), None)
    ]
    assert "/help" in values


def test_at_completion_skips_internal_directories(tmp_path):
    (tmp_path / "main.py").write_text("print('ok')")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text("secret")
    assert scan_files(str(tmp_path), "ma") == ["main.py"]


def test_expand_at_refs(tmp_path):
    (tmp_path / "note.txt").write_text("hello")
    result = expand_at_refs("查看 @note.txt", str(tmp_path))
    assert "[File: note.txt]" in result
    assert "hello" in result


@pytest.mark.asyncio
async def test_select_provider_uses_selected_name():
    providers = [
        ProviderConfig("one", "openai-compat", "http://one", "m1", "k"),
        ProviderConfig("two", "openai-compat", "http://two", "m2", "k"),
    ]

    async def choose(names):
        assert names == ["one", "two"]
        return "two"

    assert await select_provider(providers, chooser=choose) is providers[1]
```

- [ ] **Step 2: 运行测试并确认失败**

Run: `uv run pytest tests/test_inline_session.py -q`

Expected: FAIL，提示 session/references 模块不存在。

- [ ] **Step 3: 提取文件引用逻辑**

把 `app.py` 中的 `scan_files_for_at` 和 `expand_at_refs` 移到
`lantu/ui/shared/references.py`，保持 `MAX_AT_REF_BYTES` 和跳过目录规则不变。
`app.py` 改为导入共享函数，确保旧 TUI 行为不变。

- [ ] **Step 4: 实现 InlineCompleter 和 InlinePromptSession**

```python
from prompt_toolkit import PromptSession
from prompt_toolkit.application import run_in_terminal
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.document import Document
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.history import FileHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.completion import WordCompleter

from lantu.commands.completion import complete


class InlineCompleter(Completer):
    def __init__(self, registry, work_dir: str) -> None:
        self.registry = registry
        self.work_dir = work_dir

    def get_completions(self, document: Document, complete_event):
        text = document.text_before_cursor
        if text.startswith("/") and " " not in text:
            for display, value in complete(self.registry, text):
                yield Completion(value, start_position=-len(text), display=display)
            return
        at_index = text.rfind("@")
        if at_index >= 0 and " " not in text[at_index:]:
            prefix = text[at_index + 1 :]
            for path in scan_files(self.work_dir, prefix):
                yield Completion("@" + path, start_position=-(len(prefix) + 1))


class InlinePromptSession:
    def __init__(
        self,
        registry,
        work_dir: str,
        history_path: str,
        on_toggle_details=None,
    ) -> None:
        bindings = KeyBindings()

        @bindings.add("enter")
        def submit(event) -> None:
            event.current_buffer.validate_and_handle()

        @bindings.add("escape", "enter")
        def newline(event) -> None:
            event.current_buffer.insert_text("\n")

        @bindings.add("c-o")
        def toggle_details(event) -> None:
            if on_toggle_details is not None:
                event.app.create_background_task(
                    run_in_terminal(on_toggle_details)
                )

        self.session = PromptSession(
            history=FileHistory(history_path),
            completer=InlineCompleter(registry, work_dir),
            complete_while_typing=True,
            multiline=True,
            key_bindings=bindings,
        )

    async def prompt(self, status: str) -> str:
        return await self.session.prompt_async(
            HTML("<ansicyan>❯ </ansicyan>"),
            bottom_toolbar=HTML(f"<dim>{status}</dim>"),
        )

    async def choose(self, label: str, choices: list[str]) -> str:
        completer = WordCompleter(choices, sentence=True)
        while True:
            value = (
                await self.session.prompt_async(
                    f"{label} [{'/'.join(choices)}] ",
                    completer=completer,
                    complete_while_typing=True,
                    multiline=False,
                )
            ).strip().lower()
            if value in choices:
                return value

    async def ask_text(self, label: str) -> str:
        return (await self.session.prompt_async(f"{label} ", multiline=False)).strip()

    async def choose_many(self, label: str, choices: list[str]) -> list[str]:
        while True:
            raw = await self.ask_text(f"{label}（逗号分隔）")
            selected = [item.strip() for item in raw.split(",") if item.strip()]
            if selected and all(item in choices for item in selected):
                return selected


async def select_provider(providers, chooser=None):
    if len(providers) == 1:
        return providers[0]
    names = [provider.name for provider in providers]
    if chooser is None:
        selector = PromptSession(completer=WordCompleter(names, sentence=True))

        async def chooser(options):
            while True:
                value = (await selector.prompt_async("Provider: ")).strip()
                if value in options:
                    return value

    selected = await chooser(names)
    return next(provider for provider in providers if provider.name == selected)
```

使用 Enter 提交、Esc+Enter 换行，不依赖终端对 `Shift+Enter` 的一致上报。

- [ ] **Step 5: 运行测试并提交**

Run: `uv run pytest tests/test_inline_session.py tests/test_commands.py -q`

Expected: PASS。

```bash
git add lantu/ui/shared/references.py lantu/ui/inline/session.py lantu/app.py tests/test_inline_session.py
git commit -m "feat: 添加内联输入与命令补全"
```

## Task 5: 独立交互运行时装配

**Files:**
- Create: `lantu/runtime/__init__.py`
- Create: `lantu/runtime/models.py`
- Create: `lantu/runtime/builder.py`
- Create: `lantu/runtime/lifecycle.py`
- Test: `tests/test_interactive_runtime.py`

- [ ] **Step 1: 写运行时装配失败测试**

使用 monkeypatch 替换真实 LLM client，验证运行时提供完整依赖且关闭幂等：

```python
import pytest

from lantu.client import LLMClient
from lantu.config import AppConfig, ProviderConfig
from lantu.permissions import PermissionMode
from lantu.runtime import build_interactive_runtime
from lantu.tools.base import StreamEnd


class FakeClient(LLMClient):
    async def stream(self, conversation, system="", tools=None):
        yield StreamEnd(stop_reason="end_turn")


@pytest.mark.asyncio
async def test_runtime_registers_core_and_interactive_tools(tmp_path, monkeypatch):
    provider = ProviderConfig(
        name="fake",
        protocol="openai-compat",
        base_url="http://localhost",
        model="fake-model",
        api_key="test",
    )
    config = AppConfig(providers=[provider])
    monkeypatch.setattr("lantu.runtime.builder.create_client", lambda _: FakeClient())

    runtime = await build_interactive_runtime(
        config,
        provider,
        PermissionMode.DEFAULT,
        hook_engine=None,
        work_dir=str(tmp_path),
    )

    names = {tool.name for tool in runtime.registry.list_tools()}
    assert {"ReadFile", "WriteFile", "EditFile", "Bash", "ToolSearch"} <= names
    assert {"AskUserQuestion", "ExitPlanMode", "Agent", "TeamCreate"} <= names
    assert runtime.session.session_id == runtime.agent.session_id
    await runtime.close()
    await runtime.close()
```

- [ ] **Step 2: 运行测试并确认失败**

Run: `uv run pytest tests/test_interactive_runtime.py -q`

Expected: FAIL，提示 `lantu.runtime` 不存在。

- [ ] **Step 3: 定义 InteractiveRuntime 模型**

`RuntimeContext` 必须集中持有关闭时需要的资源：

```python
@dataclass
class InteractiveRuntime:
    config: AppConfig
    provider: ProviderConfig
    client: LLMClient
    agent: Agent
    conversation: ConversationManager
    registry: ToolRegistry
    command_registry: CommandRegistry
    memory_manager: MemoryManager
    session_manager: SessionManager
    session: Session
    skill_loader: SkillLoader
    skill_executor: SkillExecutor
    task_manager: TaskManager
    trace_manager: TraceManager
    worktree_manager: WorktreeManager
    team_manager: TeamManager
    hook_engine: HookEngine | None
    mcp_manager: MCPManager | None = None
    mcp_instructions: str = ""
    mcp_task: asyncio.Task | None = None
    background_tasks: set[asyncio.Task] = field(default_factory=set)
    _closed: bool = False

    def refresh_skills_if_needed(self) -> None:
        refresh_runtime_skills(self)

    async def wait_until_ready(self) -> None:
        if self.mcp_task is not None:
            await self.mcp_task

    async def prefetch_relevant_memories(self, query: str) -> str:
        return await prefetch_runtime_memories(self, query)

    async def close(self) -> None:
        await close_interactive_runtime(self)
```

- [ ] **Step 4: 实现 builder 的四个明确阶段**

`build_interactive_runtime` 按以下顺序执行，行为来源是现有
`LantuApp._select_provider`，但不能导入 Textual 或 UI 组件：

```python
async def build_interactive_runtime(
    config: AppConfig,
    provider: ProviderConfig,
    permission_mode: PermissionMode,
    hook_engine: HookEngine | None,
    work_dir: str,
) -> InteractiveRuntime:
    core = _build_core(config, provider, permission_mode, hook_engine, work_dir)
    _register_skills(core)
    _register_worktree_and_agents(core)
    await _start_runtime_services(core)
    return core
```

四个阶段必须覆盖：

1. PermissionChecker、FileCache、Session、FileHistory、Agent 和默认工具。
2. LoadSkill、InstallSkill、ToolSearch、AskUser、ExitPlanMode 和 Skill 命令。
3. Worktree、AgentTool、TeamCreate、TeamDelete、Tasks、Trace、SyntheticOutput。
4. startup hook、context-window 后台解析、MCP 连接和 stale cleanup。

MCP instructions 由 `lifecycle.py` 构建并保存到 `runtime.mcp_instructions`；首个用户回合
前由 InlineApp 注入一次。`refresh_runtime_skills` 复用现有 reload、catalog 和动态命令
注册规则；`prefetch_runtime_memories` 复用现有 8 秒超时和 `render_reminder` 格式；
`mcp_task` 保存 MCP 初始化任务，使首个回合可以等待工具注册完成。

- [ ] **Step 5: 实现幂等关闭**

`close_interactive_runtime` 设置 `_closed` 后：

1. 最多等待 3 秒完成 memory extraction、shutdown hook 和 MCP shutdown。
2. 取消 context-window、MCP、notification 和 stale cleanup 后台任务。
3. 停止所有 Team 成员并删除运行中的 team。
4. 关闭 Session。
5. 第二次调用直接返回。

- [ ] **Step 6: 运行测试并提交**

Run: `uv run pytest tests/test_interactive_runtime.py tests/test_skills.py tests/test_worktree.py tests/test_teams.py -q`

Expected: PASS。

```bash
git add lantu/runtime tests/test_interactive_runtime.py
git commit -m "refactor: 抽离交互运行时装配"
```

## Task 6: 内联主循环、命令适配和交互提示

**Files:**
- Create: `lantu/ui/inline/commands.py`
- Create: `lantu/ui/inline/components/interaction.py`
- Create: `lantu/ui/inline/app.py`
- Create: `lantu/ui/inline/__init__.py`
- Test: `tests/test_inline_app.py`

- [ ] **Step 1: 写完整回合和权限失败测试**

构造 FakeRuntime 和 FakePromptSession，不访问网络：

```python
import asyncio
from types import SimpleNamespace

import pytest

from lantu.agent import LoopComplete, PermissionRequest, PermissionResponse, StreamText
from lantu.commands.registry import CommandRegistry
from lantu.conversation import ConversationManager
from lantu.ui.inline.app import InlineApp


class FakeAgent:
    def __init__(self, events):
        self.events = events
        self.work_dir = "/tmp/project"
        self.context_window = 128_000
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self.plan_mode = False
        self.permission_mode = SimpleNamespace(value="default")

    async def run(self, conversation):
        for event in self.events:
            yield event


class FakeRuntime:
    def __init__(self, events=None):
        self.provider = SimpleNamespace(model="fake-model")
        self.agent = FakeAgent(events or [])
        self.conversation = ConversationManager()
        self.command_registry = CommandRegistry()
        self.mcp_instructions = ""
        self.closed = False

    async def close(self):
        self.closed = True


class FakePromptSession:
    def __init__(self, prompts, choices=None):
        self.prompts = list(prompts)
        self.choices = list(choices or [])

    async def prompt(self, status):
        if not self.prompts:
            raise EOFError
        return self.prompts.pop(0)

    async def choose(self, renderable, choices):
        return self.choices.pop(0)


class FakeTranscript:
    def __init__(self):
        self.user_messages = []
        self.assistant_messages = []

    def header(self, model, mode, work_dir, version=None):
        return None

    def user_message(self, text):
        self.user_messages.append(text)

    def assistant_message(self, text):
        self.assistant_messages.append(text)

    def system_message(self, text):
        return None

    def error_message(self, text):
        return None

    def tool(self, state):
        return None


class FakeLiveRenderer:
    def update(self, state):
        return None

    def stop(self):
        return None


@pytest.mark.asyncio
async def test_inline_app_runs_prompt_and_commits_response():
    runtime = FakeRuntime(events=[StreamText("完成"), LoopComplete(1)])
    prompt = FakePromptSession(["介绍项目"])
    transcript = FakeTranscript()
    app = InlineApp(runtime, prompt=prompt, transcript=transcript, live=FakeLiveRenderer())

    await app.run()

    assert transcript.user_messages == ["介绍项目"]
    assert transcript.assistant_messages == ["完成"]
    assert runtime.closed is True


@pytest.mark.asyncio
async def test_permission_prompt_resolves_future():
    loop = asyncio.get_running_loop()
    future = loop.create_future()
    request = PermissionRequest("Bash", "执行 rm -rf build", future)
    prompt = FakePromptSession([], choices=["deny"])
    app = InlineApp(FakeRuntime(), prompt=prompt)

    await app.handle_permission(request)

    assert future.result() is PermissionResponse.DENY


@pytest.mark.asyncio
async def test_interrupt_active_turn_cancels_only_agent_task():
    app = InlineApp(FakeRuntime(), prompt=FakePromptSession([]))
    app._agent_task = asyncio.create_task(asyncio.sleep(60))
    app.interrupt_active_turn()
    with pytest.raises(asyncio.CancelledError):
        await app._agent_task
    assert app.running is True
```

- [ ] **Step 2: 运行测试并确认失败**

Run: `uv run pytest tests/test_inline_app.py -q`

Expected: FAIL，提示 `InlineApp` 不存在。

- [ ] **Step 3: 实现 InlineCommandDispatcher**

命令适配器复用 `parse_command` 和 CommandRegistry，创建与旧 TUI 相同字段的
`CommandContext`。`InlineApp` 实现 UIController：

```python
class InlineCommandDispatcher:
    def __init__(self, app: "InlineApp") -> None:
        self.app = app

    async def dispatch(self, text: str) -> bool:
        name, args, is_command = parse_command(text)
        if not is_command:
            return False
        if not name:
            await self.app.show_command_list()
            return True
        command = self.app.runtime.command_registry.find(name)
        if command is None:
            self.app.add_system_message(f"未知命令：/{name}，输入 /help 查看可用命令")
            return True
        await command.handler(self.app.build_command_context(args))
        return True
```

`clear_chat` 在内联模式不能删除 scrollback，改为输出 `新会话` 分隔线；
`render_restored` 将恢复消息依次提交到 transcript。

- [ ] **Step 4: 实现 InlineApp 输入和 Agent 循环**

```python
import asyncio
import signal
from pathlib import Path
from collections import deque

from rich.console import Console

from lantu.commands.registry import CommandContext
from lantu.permissions import PermissionMode
from lantu.ui.shared.formatting import format_tokens


class InlineApp:
    def __init__(self, runtime, *, console=None, prompt=None, transcript=None, live=None):
        self.runtime = runtime
        self.console = console or Console()
        self.transcript = transcript or TranscriptRenderer(self.console)
        self.live = live or LiveRenderer(self.console)
        history_path = str(Path(runtime.agent.work_dir) / ".lantu" / "history")
        self.prompt = prompt or InlinePromptSession(
            runtime.command_registry,
            runtime.agent.work_dir,
            history_path,
            on_toggle_details=self.show_last_tool_details,
        )
        self.events = InlineEventHandler(self.live, self.transcript, self.handle_permission)
        self.commands = InlineCommandDispatcher(self)
        self.running = True
        self.confirm_exit = False
        self.pending_prompts = deque()
        self._pre_plan_mode = runtime.agent.permission_mode
        self._mcp_injected = False
        self._agent_task = None

    async def run(self) -> None:
        self.transcript.header(
            self.runtime.provider.model,
            self.runtime.agent.permission_mode.value,
            self.runtime.agent.work_dir,
        )
        try:
            while self.running:
                try:
                    if self.pending_prompts:
                        text = self.pending_prompts.popleft()
                    else:
                        text = (await self.prompt.prompt(self.status_text())).strip()
                except KeyboardInterrupt:
                    if not self.confirm_exit:
                        self.confirm_exit = True
                        self.add_system_message("再次按 Ctrl+C 退出")
                        continue
                    break
                except EOFError:
                    break
                if not text:
                    continue
                if await self.commands.dispatch(text):
                    continue
                await self.run_prompt_interruptible(text)
        finally:
            self.live.stop()
            await self.runtime.close()

    def status_text(self) -> str:
        used = self.runtime.conversation.current_tokens()
        model = self.runtime.provider.model
        mode = self.runtime.agent.permission_mode.value
        return f"{mode} · {model} · {format_tokens(used, self.runtime.agent.context_window)}"

    def add_system_message(self, text: str) -> None:
        self.transcript.system_message(text)

    def send_user_message(self, text: str) -> None:
        self.pending_prompts.append(text)

    def set_plan_mode(self, enabled: bool) -> None:
        if enabled:
            self._pre_plan_mode = self.runtime.agent.permission_mode
            self.runtime.agent.set_permission_mode(PermissionMode.PLAN)
        else:
            self.runtime.agent.set_permission_mode(self._pre_plan_mode)

    def get_token_count(self) -> tuple[int, int]:
        agent = self.runtime.agent
        return agent.total_input_tokens, agent.total_output_tokens

    def refresh_status(self) -> None:
        return None

    def request_exit(self) -> None:
        self.running = False

    def show_last_tool_details(self) -> None:
        if self.events.last_tool is None:
            self.add_system_message("当前回合没有可展开的工具输出")
            return
        self.transcript.tool_details(self.events.last_tool)

    def interrupt_active_turn(self) -> None:
        if self._agent_task is not None and not self._agent_task.done():
            self._agent_task.cancel()

    async def run_prompt_interruptible(self, text: str) -> None:
        loop = asyncio.get_running_loop()
        self._agent_task = asyncio.create_task(self.run_prompt(text))
        signal_installed = False
        previous_sigint = signal.getsignal(signal.SIGINT)
        try:
            loop.add_signal_handler(signal.SIGINT, self.interrupt_active_turn)
            signal_installed = True
        except (NotImplementedError, RuntimeError):
            signal_installed = False
        try:
            await self._agent_task
        finally:
            if signal_installed:
                loop.remove_signal_handler(signal.SIGINT)
                signal.signal(signal.SIGINT, previous_sigint)
            self._agent_task = None

    def build_command_context(self, args: str) -> CommandContext:
        return CommandContext(
            args=args,
            agent=self.runtime.agent,
            conversation=self.runtime.conversation,
            session=self.runtime.session,
            session_manager=self.runtime.session_manager,
            memory_manager=self.runtime.memory_manager,
            ui=self,
            config={
                "registry": self.runtime.command_registry,
                "set_session": self.set_session,
                "set_conversation": self.set_conversation,
                "clear_chat": self.transcript.clear_boundary,
                "render_restored": self.render_restored,
                "skill_loader": self.runtime.skill_loader,
                "skill_executor": self.runtime.skill_executor,
                "request_exit": self.request_exit,
            },
        )

    def set_session(self, session) -> None:
        self.runtime.session = session
        self.runtime.agent.session_id = session.session_id

    def set_conversation(self, conversation) -> None:
        self.runtime.conversation = conversation

    async def render_restored(self, messages) -> None:
        for message in messages:
            if message.role == "user":
                self.transcript.user_message(message.content)
            elif message.content:
                self.transcript.assistant_message(message.content)
```

`run_prompt` 使用和旧 TUI 相同的持久化边界，核心流程为：

```python
async def run_prompt(self, text: str, *, is_notification: bool = False) -> None:
    self.confirm_exit = False
    self.runtime.refresh_skills_if_needed()
    await self.runtime.wait_until_ready()

    if text and "@" in text:
        text = expand_at_refs(text, self.runtime.agent.work_dir)

    prefetch = (
        asyncio.create_task(self.runtime.prefetch_relevant_memories(text))
        if text
        else None
    )
    if text:
        self.transcript.user_message(text)
        self.runtime.conversation.add_user_message(text)
        self.runtime.session.append(Message(role="user", content=text))

    if self.runtime.mcp_instructions and not self._mcp_injected:
        self.runtime.conversation.add_system_reminder(
            self.runtime.mcp_instructions
        )
        self._mcp_injected = True

    if prefetch is not None:
        self.runtime.agent.memory_recall_task = prefetch
        self.runtime.agent._memory_recall_consumed = False

    history_cursor = len(self.runtime.conversation.history)
    try:
        async for event in self.runtime.agent.run(self.runtime.conversation):
            if isinstance(event, CompactNotification):
                self.persist_compact_boundary(event)
                history_cursor = len(self.runtime.conversation.history)

            await self.events.handle(event)

            if isinstance(event, ToolResultEvent):
                ask_tool = self.runtime.registry.get("AskUserQuestion")
                if isinstance(ask_tool, AskUserTool) and ask_tool._pending_event:
                    await self.handle_ask_user(ask_tool._pending_event)

            if isinstance(event, TurnComplete):
                history_cursor = self.persist_history_from(history_cursor)

            if isinstance(event, LoopComplete) and self.runtime.agent.plan_mode:
                await self.handle_plan_approval()
    except asyncio.CancelledError:
        self.events.finish()
        self.add_system_message("Operation cancelled")
    except LLMError as error:
        self.events.finish()
        self.transcript.error_message(str(error))
    finally:
        self.events.finish()
        history_cursor = self.persist_history_from(history_cursor)
        self.runtime.session.meta.total_tokens = (
            self.runtime.agent.total_input_tokens
            + self.runtime.agent.total_output_tokens
        )
        await self.process_task_notifications()
```

`persist_history_from` 只追加 `conversation.history[cursor:]` 并返回新长度；
`persist_compact_boundary` 复用现有 `make_compact_boundary` 记录格式；
`process_task_notifications` 使用 `TaskManager.poll_completed()` 和
`TeamManager.drain_lead_mailbox()`，有新结果时向 conversation 注入 reminder，再以
`is_notification=True` 调用一次 `run_prompt("")`。为防止递归失控，通知回合不再次处理
空通知。

- [ ] **Step 5: 实现局部权限、计划和 AskUser 交互**

`components/interaction.py` 只负责 Rich 文本；选择逻辑由 PromptSession 提供：

```python
PERMISSION_CHOICES = {
    "allow": PermissionResponse.ALLOW,
    "always": PermissionResponse.ALLOW_ALWAYS,
    "deny": PermissionResponse.DENY,
}


async def handle_permission(self, event: PermissionRequest) -> None:
    self.live.stop()
    self.transcript.commit(
        render_permission_request(event.tool_name, event.description),
        blank_after=False,
    )
    choice = await self.prompt.choose(
        "选择",
        choices=["allow", "always", "deny"],
    )
    if not event.future.done():
        event.future.set_result(PERMISSION_CHOICES[choice])
```

AskUserQuestion 从 `AskUserTool._pending_event` 读取问题，并按类型生成答案：

```python
async def handle_ask_user(self, event: AskUserEvent) -> None:
    self.live.stop()
    answers: dict[str, str] = {}
    try:
        for question in event.questions:
            name = question["name"]
            label = question.get("message", name)
            kind = question.get("type", "text")
            options = [
                option.get("label", str(option)) if isinstance(option, dict) else str(option)
                for option in question.get("options", [])
            ]
            if kind == "checkbox":
                answers[name] = ", ".join(
                    await self.prompt.choose_many(label, options)
                )
            elif kind in {"radio", "select"} and options:
                answers[name] = await self.prompt.choose(label, options)
            else:
                answers[name] = await self.prompt.ask_text(label)
    except (KeyboardInterrupt, EOFError):
        answers = {}
    if not event.future.done():
        event.future.set_result(answers)
```

计划批准使用 `yolo`、`manual`、`feedback` 三个固定选择。前两项读取计划文件、调用
`build_plan_mode_exit_reminder`、恢复对应权限模式并把批准消息放入待执行 prompt；
`feedback` 再调用 `ask_text("修改意见")` 并把结果放入待执行 prompt。取消等同
`manual`，所有路径都必须完成 Future 或返回到输入循环。

- [ ] **Step 6: 运行测试并提交**

Run: `uv run pytest tests/test_inline_app.py tests/test_permissions.py tests/test_commands.py -q`

Expected: PASS。

```bash
git add lantu/ui/inline tests/test_inline_app.py
git commit -m "feat: 实现内联终端交互主循环"
```

## Task 7: 增加 `/exit` 和 `/quit`

**Files:**
- Create: `lantu/commands/handlers/exit.py`
- Modify: `lantu/commands/handlers/__init__.py`
- Modify: `lantu/app.py`
- Modify: `tests/test_commands.py`

- [ ] **Step 1: 写退出命令失败测试**

```python
from types import SimpleNamespace

import pytest

from lantu.commands.handlers import register_all_commands
from lantu.commands.handlers.exit import EXIT_COMMAND
from lantu.commands.registry import CommandContext, CommandRegistry


@pytest.mark.asyncio
async def test_exit_command_requests_shared_shutdown():
    requested = []
    ctx = CommandContext(
        args="",
        agent=None,
        conversation=None,
        session=None,
        session_manager=None,
        memory_manager=None,
        ui=SimpleNamespace(add_system_message=lambda message: None),
        config={"request_exit": lambda: requested.append(True)},
    )
    await EXIT_COMMAND.handler(ctx)
    assert requested == [True]
    assert "quit" in EXIT_COMMAND.aliases


def test_all_commands_include_exit():
    registry = CommandRegistry()
    register_all_commands(registry)
    assert registry.find("exit") is not None
    assert registry.find("quit") is registry.find("exit")
```

- [ ] **Step 2: 运行测试并确认失败**

Run: `uv run pytest tests/test_commands.py -q`

Expected: FAIL，提示 `EXIT_COMMAND` 不存在。

- [ ] **Step 3: 实现共享退出命令**

```python
async def handle_exit(ctx: CommandContext) -> None:
    request_exit = ctx.config.get("request_exit")
    if request_exit is None:
        ctx.ui.add_system_message("当前前端不支持 /exit")
        return
    result = request_exit()
    if inspect.isawaitable(result):
        await result


EXIT_COMMAND = Command(
    name="exit",
    aliases=["quit"],
    description="退出 Lantu",
    usage="/exit",
    type=CommandType.LOCAL_UI,
    handler=handle_exit,
)
```

将命令加入 `ALL_COMMANDS`。InlineApp 的 `request_exit` 设置 `running=False`；旧 TUI 的
CommandContext 增加 `request_exit`，调用现有完整清理路径而不是直接 `self.exit()`。

- [ ] **Step 4: 运行测试并提交**

Run: `uv run pytest tests/test_commands.py tests/test_inline_app.py -q`

Expected: PASS。

```bash
git add lantu/commands/handlers/exit.py lantu/commands/handlers/__init__.py lantu/app.py tests/test_commands.py
git commit -m "feat: 添加退出斜杠命令"
```

## Task 8: CLI 默认路由、`--tui` 和文档

**Files:**
- Modify: `lantu/__main__.py`
- Modify: `README.md`
- Create: `tests/test_cli_modes.py`

- [ ] **Step 1: 写 CLI 路由失败测试**

将参数解析提取为 `build_parser()`，运行方式提取为 `run_interactive()`，便于无终端测试：

```python
from lantu.__main__ import build_parser, run_interactive
from lantu.config import AppConfig, ProviderConfig
from lantu.permissions import PermissionMode


def make_config() -> AppConfig:
    return AppConfig(
        providers=[
            ProviderConfig(
                name="fake",
                protocol="openai-compat",
                base_url="http://localhost",
                model="fake-model",
                api_key="test",
            )
        ]
    )


def test_tui_flag_is_available():
    args = build_parser().parse_args(["--tui"])
    assert args.tui is True


def test_default_interactive_mode_uses_inline(monkeypatch):
    called = []
    monkeypatch.setattr("lantu.__main__.run_inline", lambda *args, **kwargs: called.append("inline"))
    monkeypatch.setattr("lantu.__main__.run_tui", lambda *args, **kwargs: called.append("tui"))
    run_interactive(
        make_config(),
        PermissionMode.DEFAULT,
        None,
        build_parser().parse_args([]),
    )
    assert called == ["inline"]


def test_tui_flag_uses_legacy_frontend(monkeypatch):
    called = []
    monkeypatch.setattr("lantu.__main__.run_tui", lambda *args, **kwargs: called.append("tui"))
    run_interactive(
        make_config(),
        PermissionMode.DEFAULT,
        None,
        build_parser().parse_args(["--tui"]),
    )
    assert called == ["tui"]
```

- [ ] **Step 2: 运行测试并确认失败**

Run: `uv run pytest tests/test_cli_modes.py -q`

Expected: FAIL，提示 parser/routing 函数不存在。

- [ ] **Step 3: 重构 CLI 入口**

`__main__.py` 的优先级固定为：`-p` -> `--remote` -> `--tui` -> 默认 inline。

```python
parser.add_argument(
    "--tui",
    action="store_true",
    help="Use the legacy fullscreen Textual interface",
)


def run_inline(config, permission_mode, hook_engine) -> None:
    async def start() -> None:
        provider = await select_provider(config.providers)
        runtime = await build_interactive_runtime(
            config,
            provider,
            permission_mode,
            hook_engine,
            os.getcwd(),
        )
        await InlineApp(runtime).run()

    asyncio.run(start())
```

非 TTY 且没有 `-p` 时输出明确错误：

```text
Error: interactive mode requires a TTY; use `lantu -p "prompt"` instead.
```

`run_tui` 保留当前 `LantuApp` 参数，并继续传入
`driver_class=NoAltScreenDriver`，不移动旧前端代码。

- [ ] **Step 4: 更新 README**

文档说明：

- `uv run lantu` 是默认内联模式。
- `uv run lantu --tui` 是旧 Textual 模式。
- `uv tool install --editable <repo>` 后可在任意项目目录运行 `lantu`。
- 全局模型配置使用 `~/.lantu/config.yaml`。
- `/exit`、`/quit` 和 Ctrl+C 行为。

- [ ] **Step 5: 运行测试并提交**

Run: `uv run pytest tests/test_cli_modes.py tests/test_inline_app.py -q`

Expected: PASS。

```bash
git add lantu/__main__.py README.md tests/test_cli_modes.py
git commit -m "feat: 默认启用内联终端前端"
```

## Task 9: PTY 验证和全量回归

**Files:**
- Create: `tests/fixtures/run_inline_fake.py`
- Create: `tests/test_inline_pty.py`
- Modify: `README.md` only if verification exposes a documented limitation

- [ ] **Step 1: 创建可离线运行的 Fake Agent PTY fixture**

fixture 使用真实 InlineApp、PromptSession、TranscriptRenderer 和 LiveRenderer，但注入
FakeRuntime/FakeAgent。收到普通输入后依次产生：

```python
[
    StreamText("正在处理"),
    ToolUseEvent("ReadFile", "t1", {"file_path": "demo.py"}),
    ToolResultEvent("t1", "ReadFile", "a\nb\n", False, 0.1),
    StreamText("处理完成"),
    LoopComplete(1),
]
```

收到 `/exit` 后走正式关闭流程。

- [ ] **Step 2: 写 PTY 失败测试**

```python
from io import BytesIO
import os

import pexpect


def test_inline_app_keeps_scrollback_and_restores_terminal():
    captured = BytesIO()
    child = pexpect.spawn(
        ".venv/bin/python",
        ["tests/fixtures/run_inline_fake.py"],
        cwd=os.getcwd(),
        encoding=None,
        timeout=10,
    )
    child.logfile_read = captured
    child.expect("LANTU".encode())
    child.sendline("hello".encode())
    child.expect("处理完成".encode())
    child.sendline("/exit".encode())
    child.expect(pexpect.EOF)
    output = captured.getvalue()
    assert b"\x1b[?1049h" not in output
    assert b"\x1b[2J" not in output
    assert b"\x1b[?25h" in output
```

- [ ] **Step 3: 运行 PTY 测试并修复终端恢复问题**

Run: `uv run pytest tests/test_inline_pty.py -q`

Expected: PASS，并确认没有 alternate-screen 和 clear-screen 序列。

- [ ] **Step 4: 运行前端和相关回归测试**

Run:

```bash
uv run pytest \
  tests/test_inline_formatting.py \
  tests/test_inline_components.py \
  tests/test_inline_live.py \
  tests/test_inline_session.py \
  tests/test_interactive_runtime.py \
  tests/test_inline_app.py \
  tests/test_cli_modes.py \
  tests/test_inline_pty.py \
  tests/test_commands.py \
  tests/test_permissions.py -q
```

Expected: PASS。

- [ ] **Step 5: 运行全量测试和编译检查**

Run:

```bash
uv run pytest
uv run python -m compileall lantu tests
```

Expected: 所有测试通过，compileall 返回 0。

- [ ] **Step 6: 手工终端验收**

在至少 100 列和 40 列两个终端宽度运行：

```bash
uv run lantu
uv run lantu --tui
uv run lantu -p "介绍当前项目"
```

确认：

- 默认模式不出现固定黑色矩形。
- 像素字标在宽终端显示，窄终端正确降级。
- 完成消息保留在 scrollback。
- 工具运行期间只刷新当前动态区域。
- Ctrl+C、`/exit` 和 `/quit` 均恢复光标。
- `--tui`、`-p` 和 `--remote` 没有行为回归。

- [ ] **Step 7: 提交最终验证调整**

```bash
git add tests/test_inline_pty.py tests/fixtures/run_inline_fake.py README.md
git commit -m "test: 补充内联终端集成验证"
```

## 完成条件

- 默认 `lantu` 使用内联前端，`--tui` 保留旧 Textual 前端。
- 新渲染代码位于 `lantu/ui/`，运行时装配位于 `lantu/runtime/`。
- `lantu/app.py` 只增加兼容接线，不新增内联组件和事件处理逻辑。
- 不发送 alternate-screen 或 clear-screen 控制码。
- 已完成输出永久进入终端 scrollback。
- 像素风 LANTU 标识、窄终端降级和终端主题继承均通过测试。
- 全量测试、compileall 和手工终端验收全部通过。
