# Lantu 内联终端前端设计

## 背景

Lantu 当前使用 Textual `App` 作为默认交互前端。自定义
`NoAltScreenDriver` 只移除了 alternate screen 切换码，但 Textual 仍持续管理和
重绘一个固定高度的 `Screen`。因此启动后会出现占满终端可视区域的黑色矩形，历史
消息也不能像普通终端输出一样自然进入 scrollback。

本次改造的目标是让默认交互方式接近 Claude Code 的非全屏模式：已经完成的内容
永久写入终端历史，只动态刷新当前回复、运行中的工具和输入区域。现有 Textual 前端
作为可选模式保留。

## 已确认决策

- `lantu` 默认启动新的内联终端前端。
- `lantu --tui` 启动现有 Textual 前端。
- `lantu --remote` 和 `lantu -p` 保持现有行为。
- Agent、工具、权限、MCP、Memory、Team 和 Worktree 核心不因前端改造而改变。
- 新前端消费现有 `AgentEvent`，不复制 Agent 主循环。
- 不把新前端继续堆入现有 `lantu/app.py`。
- 启动标识采用极简 `L` 结构线和像素风 `ANTU` 字标。

## 非目标

- 本阶段不删除 Textual 依赖和旧 TUI。
- 本阶段不重写 Remote Web 前端。
- 本阶段不修改 Agent 的工具调用协议和对话存储格式。
- 本阶段不复刻 Claude Code 的商业服务、账户或订阅界面。

## 整体架构

```text
CLI
├── lantu                 -> InlineApp
├── lantu --tui           -> LantuApp (Textual)
├── lantu --remote        -> RemoteServer
└── lantu -p              -> 非交互输出

Agent.run(ConversationManager)
        |
        v
AgentEvent
├── InlineEventHandler    -> 内联终端
├── LantuApp              -> Textual TUI
├── RemoteServer          -> WebSocket
└── _run_prompt           -> stdout / NDJSON
```

CLI 入口只负责模式选择和依赖装配。`InlineApp` 负责交互循环，但不负责模型请求和工具
执行。这样前端可以独立演进，且不会产生第二套 Agent 实现。

## 模块边界

```text
lantu/ui/
├── shared/
│   ├── theme.py
│   ├── formatting.py
│   └── models.py
├── inline/
│   ├── app.py
│   ├── session.py
│   ├── transcript.py
│   ├── live.py
│   ├── event_handler.py
│   └── components/
│       ├── header.py
│       ├── message.py
│       ├── tool.py
│       ├── status.py
│       ├── permission.py
│       ├── plan.py
│       └── question.py
└── tui/
    └── legacy.py
```

### `inline/app.py`

负责生命周期和依赖组装：创建会话、读取输入、启动 Agent、处理中断和退出。该文件不
包含具体 ANSI 样式、消息排版或工具摘要规则。

### `inline/session.py`

封装 `prompt_toolkit.PromptSession`，提供多行输入、历史、斜杠命令补全、`@` 文件
补全和键盘中断。输入组件只返回用户意图，不直接调用 Agent。

### `inline/transcript.py`

使用 Rich Console 输出已完成内容。提交到 transcript 的内容只写一次，之后不再参与
动态重绘，使终端原生 scrollback 成为历史记录。

### `inline/live.py`

管理唯一的动态区域，用于流式文本、thinking 状态、正在运行的工具和 spinner。一个
事件完成后，先停止动态渲染，再将最终版本提交给 `TranscriptRenderer`。

### `inline/event_handler.py`

把 `StreamText`、`ToolUseEvent`、`ToolResultEvent`、`PermissionRequest` 等现有事件
转换成前端状态。它不直接拼 ANSI 字符串，所有显示由组件完成。

### `components/`

每个组件只接收前端状态模型并返回 Rich renderable。组件不能持有 Agent、
ConversationManager 或 ToolRegistry，避免展示层反向依赖核心层。

### `shared/`

存放两种终端前端都能使用的主题 token、路径缩短、耗时格式化和轻量视图模型。
旧 TUI 的迁移按需进行，不在首个版本中强制重构。

## 终端渲染模型

内联前端将内容分为两类：

1. **已提交内容**：启动标识、用户消息、已完成回复、已完成工具摘要、错误和系统通知。
2. **临时内容**：流式回复、spinner、thinking、执行中的工具和等待中的交互提示。

任何时刻只允许一个 Live 动态区域。发生权限确认、计划确认或 AskUserQuestion 时，先
暂停 Live，再启动交互提示；用户完成选择后恢复动态区域。这一约束避免多个渲染器
争用 stdout。

## 视觉设计

启动标识只输出一次，不固定在顶部：

```text
┃  █▀█ █▄░█ ▀█▀ █░█
┃  █▀█ █░▀█  █  █▄█
┗━━ 0.2.0 · deepseek-v4-pro · default
    ~/project
```

- `L + ANTU` 使用青蓝色强调。
- 版本、模型和权限模式使用中灰色。
- 工作目录使用低强调灰色，并将用户主目录缩写为 `~`。
- 不设置终端背景色，不绘制包围整个应用的边框。
- 窄终端降级为 `LANTU 0.2.0 · <model>` 和单独一行工作目录。
- 版本号从包元数据读取，不在前端硬编码。

消息与工具采用连续终端记录：

```text
❯ 修改配置加载逻辑

● 正在分析配置文件

● Read lantu/config.py
  ⎿ 读取 285 行

● Edit lantu/config.py
  ⎿ 修改 2 处

● 配置加载逻辑已经调整完成。
```

颜色必须表达状态，同时保留符号区别，不能只依赖颜色：

- 用户输入：`❯` 和轻量强调文本。
- AI 内容：`●` 和正常前景色。
- 运行中：动态 spinner 和弱强调文字。
- 成功：绿色状态符号。
- 失败：红色状态符号和错误摘要。
- 权限等待：黄色状态符号。

输入区域只有局部横线，不形成固定窗口：

```text
────────────────────────────────────────
❯ 继续完善测试
────────────────────────────────────────
 default · deepseek-v4-pro        18k/128k
```

## 工具展示

- 运行中工具显示工具名、关键参数和 spinner。
- 完成后默认保留一至两行摘要。
- Read、Glob、Grep 等连续只读工具可以合并显示。
- Bash 显示命令和退出状态，长输出默认截断。
- Edit 和 Write 显示文件路径及修改统计。
- 详细工具内容沿用 `Ctrl+O` 展开语义；首个版本可以先实现当前回合的展开，不要求
  重绘整个历史 scrollback。
- 工具错误不能被普通成功摘要覆盖。

## 输入与命令

- `Enter` 提交，`Shift+Enter` 或配置的组合键插入换行。
- 支持历史浏览、斜杠命令补全和 `@` 文件补全。
- `Ctrl+C` 在生成中中断当前响应；空闲时第一次显示退出提示，再次按下退出。
- 新增 `/exit` 和 `/quit`，两者走同一套清理流程。
- `/help`、`/model` 等命令通过现有 CommandRegistry 适配，不维护另一份命令表。

## 权限与临时交互

权限、计划和 AskUserQuestion 使用局部交互块，可以有局部边框，但不能进入全屏模式
或清空历史。所有选择都支持键盘操作，并保留取消路径。

中断或异常时必须恢复终端状态，包括光标、输入模式和 Live 区域。清理流程继续执行
Memory、Hook、MCP 和 Team 的现有关闭逻辑。

## 错误处理

- 模型认证和配置错误在进入输入循环前输出，并以非零状态退出。
- Agent 运行错误提交为静态错误消息，输入循环保持可用，除非错误不可恢复。
- Live 渲染异常必须先停止 Live，再使用普通 stderr 输出诊断信息。
- 非 TTY 环境不启动交互前端，沿用 `-p` 或明确返回使用提示。
- 不支持 Unicode 的终端使用 ASCII 符号回退，核心操作不能依赖像素字符。

## 兼容和迁移

首个版本不迁移旧 Textual 组件，只在 CLI 中增加 `--tui` 入口。现有 `app.py` 继续
作为旧 TUI 实现，后续确认内联模式稳定后再移动到 `lantu/ui/tui/legacy.py`。

建议分阶段交付：

1. 启动标识、基础输入、用户消息和流式回答。
2. 工具状态、权限确认、计划和 AskUserQuestion。
3. 命令补全、文件补全、会话恢复和多 Agent 状态。
4. 评估是否迁移或删除旧 Textual TUI。

## 测试策略

- 组件快照测试：不同宽度、颜色开关和 Unicode 回退。
- EventHandler 单元测试：每种 AgentEvent 的状态转换和提交时机。
- Live 生命周期测试：开始、刷新、暂停、提交和异常恢复。
- 输入测试：多行、历史、补全、Ctrl+C、`/exit` 和 `/quit`。
- 集成测试：使用假的 LLMClient 驱动完整文本和工具调用回合。
- PTY 测试：确认不发送 alternate-screen 码、不清空 scrollback、不残留隐藏光标。
- 回归测试：`--tui`、`--remote` 和 `-p` 行为保持不变。

## 验收标准

- 默认 `lantu` 不进入固定黑色全屏区域。
- 启动标识和完成消息自然保留在终端 scrollback。
- 流式更新期间没有明显重复行或屏幕闪烁。
- 终端背景完全由用户终端主题决定。
- 窄终端下标题、消息、工具和输入区域不横向溢出。
- 中断、正常退出和异常退出后终端光标及输入状态正常。
- 新前端模块边界清晰，具体渲染代码不进入 `lantu/app.py`。
