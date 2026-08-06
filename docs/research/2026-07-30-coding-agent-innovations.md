# 2025-2026 Coding Agent 新能力调研及 LANTU 建议

> 调研日期：2026-07-30  
> 范围：终端、IDE、云端 coding agent；仅使用厂商官方文档、官方博客、发布记录和源码仓库。  
> 说明：没有发布日期的持续更新文档标为“访问于 2026-07-30”。功能存在性可由文档或源码验证；“更快”“最好”等效果声明若没有公开评测，只视为厂商营销，不作为结论。

## 一、结论先行

LANTU 已有 Agent 循环、权限与沙箱、MCP、Skill、Hook、记忆、上下文压缩、子 Agent、团队协作、worktree 和会话恢复。继续照搬同类产品的“功能清单”价值不高。

最值得加入的四项是：

1. **验证闭环**：修改后自动收集测试、lint、类型检查和 LSP 诊断，失败时把结构化结果交回 Agent。
2. **结构化事件与可重放 trace**：统一记录模型、工具、权限、文件 diff、token、耗时和退出原因，支持 JSONL 导出及故障复现。
3. **后台任务协议**：把长任务变成可暂停、恢复、查看进度、取消的持久任务，而不是仅延长前台循环。
4. **代码智能层**：优先接入 LSP 的定义、引用、诊断和符号；之后再考虑语义索引，避免先造昂贵的向量检索系统。

其中 1、2 最适合短期实现：依赖少，直接提升可靠性和排障能力，也符合 LANTU 的教学定位。

## 二、2025-2026 的共同演进

| 趋势 | 可验证变化 | 对 LANTU 的意义 |
|---|---|---|
| 前台聊天转为后台委托 | Codex cloud、Copilot coding agent、Cursor Background Agents、Jules 都在隔离环境中异步工作并产出可审查改动 | 需要正式的任务状态机、持久事件和取消/恢复语义 |
| 单 Agent 转为专职 Agent/团队 | Claude Code subagents/agent teams、Gemini CLI subagents、Copilot custom agents、Roo modes | LANTU 已有基础；下一步应加强资源预算、交接协议和结果合并，而非继续增加 Agent 数量 |
| “能改代码”转为“能证明改对” | Aider 自动 lint/test、Copilot PR 工作流、Codex review、Gemini checkpoint/plan | 建立统一 verifier 比再加工具更值钱 |
| 提示词扩展转为可分发扩展 | MCP、Skills、Hooks、plugins/extensions、custom agents 已成为共同接口 | LANTU 已覆盖大部；应补版本、能力声明和信任边界 |
| 黑盒对话转为可观察执行 | Gemini telemetry、OpenHands event stream、Codex app-server protocol、OpenCode client/server | 用稳定事件协议解耦核心与 UI，并支撑回放和评测 |
| 全量工具常驻转为按需加载 | Skills、MCP tool search、专职子 Agent 减少初始上下文 | LANTU 已有 tool search/skills；应测量实际 token 节省与命中率 |

## 三、产品与项目对比

### 1. OpenAI Codex

**可验证事实**

- Codex cloud 在独立云端环境中并行执行任务，读取仓库、运行命令和测试，并以可审查的变更和终端证据返回；官方于 **2025-05-16** 发布研究预览。[官方发布](https://openai.com/index/introducing-codex/)
- Codex CLI 是 Rust 实现的开源终端 agent；最新可见发布为 **0.146.0，2026-07-29**。[源码](https://github.com/openai/codex) · [发布](https://github.com/openai/codex/releases/tag/rust-v0.146.0)
- 仓库公开了稳定的 app-server JSON-RPC 接口、exec policy、MCP 接口和结构化 schema，可验证其核心与多种客户端解耦，而不只是一个 TUI。[app-server 文档](https://github.com/openai/codex/blob/main/codex-rs/app-server/README.md) · [协议 schema](https://github.com/openai/codex/tree/main/codex-rs/app-server-protocol/schema) · [exec policy](https://github.com/openai/codex/blob/main/codex-rs/execpolicy/README.md)

**真正值得参考**

- 核心事件协议与 UI 分离；同一执行内核可服务 CLI、桌面端和自动化。
- 云端任务不是“后台线程”，而是环境、任务、事件、产物、审批都有明确生命周期。
- 将命令安全策略做成可测试的策略层，而不是只靠提示词。

**营销边界**

- 官方称其“更快地交付功能”等属于产品效果声明；隔离执行、并行任务和引用证据是可验证机制，但不能据此推出它对所有仓库都更可靠。

### 2. Anthropic Claude Code

**可验证事实**

- Claude Code 随 Claude 3.7 Sonnet 于 **2025-02-24** 以研究预览推出，定位为终端中的 agentic coding 工具。[官方发布](https://www.anthropic.com/news/claude-3-7-sonnet)
- 官方文档提供 hooks、subagents、agent teams、plugins、skills、MCP、sandboxing、checkpointing 和 headless/SDK 用法；这些是公开接口，不只是演示。[Hooks](https://code.claude.com/docs/en/hooks) · [Subagents](https://code.claude.com/docs/en/sub-agents) · [Agent teams](https://code.claude.com/docs/en/agent-teams) · [Checkpointing](https://code.claude.com/docs/en/checkpointing) · [Sandboxing](https://code.claude.com/docs/en/sandboxing)
- hooks 能在工具调用前后、权限请求、会话结束等生命周期点执行确定性命令；subagent 有独立上下文、工具与权限范围。[Hooks reference](https://code.claude.com/docs/en/hooks) · [Subagents](https://code.claude.com/docs/en/sub-agents)
- 最新可见 GitHub 发布为 **v2.1.220，2026-07-25**。[官方仓库](https://github.com/anthropics/claude-code) · [发布](https://github.com/anthropics/claude-code/releases/tag/v2.1.220)

**真正值得参考**

- “确定性 Hook + 概率性 Agent”的组合：合规、格式化、审计放 Hook，判断和修复留给模型。
- 子 Agent 的价值在独立上下文和最小工具集，不在角色名称数量。
- checkpoint 让用户以文件状态为单位撤销，降低自治修改的心理成本。

**对 LANTU**

LANTU 已有 hooks、subagent/team、rewind、sandbox，不应重复实现。应补充 hook 的事件覆盖测试、每个子 Agent 的 token/时间/工具预算，以及 team 交接的结构化摘要。

### 3. Google Gemini CLI

**可验证事实**

- Google 于 **2025-06-25** 发布开源 Gemini CLI，提供 ReAct 循环、工具、MCP 和终端交互。[官方博客](https://blog.google/technology/developers/introducing-gemini-cli-open-source-ai-agent/) · [源码](https://github.com/google-gemini/gemini-cli)
- 当前仓库文档明确包含 auto memory、checkpointing、git worktrees、headless、plan mode、sandbox、session management、telemetry、agent skills、subagents、remote agents 与 A2A server。[文档目录](https://github.com/google-gemini/gemini-cli/tree/main/docs) · [Subagents](https://github.com/google-gemini/gemini-cli/blob/main/docs/core/subagents.md) · [Remote agents](https://github.com/google-gemini/gemini-cli/blob/main/docs/core/remote-agents.md) · [Telemetry](https://github.com/google-gemini/gemini-cli/blob/main/docs/cli/telemetry.md)
- 最新可见发布为 **v0.53.0，2026-07-28**。[发布](https://github.com/google-gemini/gemini-cli/releases/tag/v0.53.0)

**真正值得参考**

- headless 模式和 telemetry 是自动化、回归评测和排障的基础设施。
- A2A/remote-agent 接口把“子 Agent”从进程内实现提升为可替换协议。
- plan mode steering 允许用户在执行前后修正方向，比一次性审批完整计划更实用。[Plan mode steering](https://github.com/google-gemini/gemini-cli/blob/main/docs/cli/tutorials/plan-mode-steering.md)

**对 LANTU**

LANTU 已有 `-p` 和输出格式基础，可优先补 JSONL 事件、稳定退出码和 telemetry opt-in；A2A 应等核心协议稳定后再做。

### 4. GitHub Copilot coding agent

**可验证事实**

- GitHub 于 **2025-05-19** 发布 Copilot coding agent：从 issue/提示接任务，在 GitHub Actions 驱动的临时环境中修改代码并创建 PR，用户通过 PR 评论继续协作。[官方发布](https://github.blog/news-insights/product-news/github-copilot-meet-the-new-coding-agent/) · [官方概念文档](https://docs.github.com/en/copilot/concepts/agents/coding-agent/about-coding-agent)
- coding agent 支持 custom agents、MCP、hooks、custom instructions，并在 PR 中展示会话日志；其工作流天然绑定 issue、branch、commit、PR 和 review。[功能文档](https://docs.github.com/en/copilot/how-tos/use-copilot-agents/coding-agent) · [Custom agents](https://docs.github.com/en/copilot/concepts/agents/coding-agent/about-custom-agents) · [MCP](https://docs.github.com/en/copilot/customizing-copilot/extending-copilot-coding-agent-with-mcp)
- GitHub 文档明确列出防火墙、权限和人工审查约束；PR 工作流不是无条件自治。[安全文档](https://docs.github.com/en/copilot/concepts/agents/coding-agent/about-coding-agent#security-considerations)

**真正值得参考**

- 用既有开发对象作为任务协议：issue 是输入，PR 是产物，checks 是验证，review 是审批。
- Agent 的完整执行日志附着在变更上，审查者不必依赖聊天上下文。

**对 LANTU**

可做一个薄的 GitHub Issues/PR adapter，但不要内建完整 GitHub 平台。先定义通用 `TaskSource` 与 `ChangeArtifact` 接口。

### 5. Cursor

**可验证事实**

- Cursor 于 **2025-05-15** 发布 Background Agents：agent 在隔离的远程 Ubuntu 环境中异步运行，可连接 GitHub、克隆仓库并在独立分支工作。[官方 changelog](https://www.cursor.com/changelog/0-50) · [官方文档](https://docs.cursor.com/background-agent)
- Cursor 文档公开 rules、MCP、background agents、Bugbot/code review 等能力。[Rules](https://docs.cursor.com/context/rules) · [MCP](https://docs.cursor.com/context/model-context-protocol) · [Bugbot](https://docs.cursor.com/bugbot)
- Background Agents 需要访问代码及配置的密钥；官方安全文档承认自动执行命令带来 prompt injection 和数据外泄风险。[安全说明](https://docs.cursor.com/background-agent/security)

**真正值得参考**

- 远程 agent 以分支和可接管会话为边界，用户可在前台继续工作。
- code review agent 与生成 agent 分离，形成独立审查视角。

**营销边界**

- “显著提高生产力”无法从公开机制直接验证。远程隔离能减少本机风险，但密钥和网络访问会把风险转移到远程环境，并不会消失。

### 6. OpenHands

**可验证事实**

- OpenHands 是开源的 agent 平台，提供 SDK、CLI、本地/容器 runtime、事件流、REST API 和可扩展工具；最新可见发布为 **v1.7.1，2026-07-30**。[源码](https://github.com/All-Hands-AI/OpenHands) · [发布](https://github.com/All-Hands-AI/OpenHands/releases/tag/v1.7.1) · [文档](https://docs.openhands.dev/)
- OpenHands 将 agent、workspace/runtime、conversation/event stream 分层，并支持 Docker/Kubernetes 等隔离执行。[Runtime 文档](https://docs.openhands.dev/openhands/usage/runtimes) · [SDK 文档](https://docs.openhands.dev/sdk/)
- Microagents 用仓库或用户级 Markdown 提供领域知识和触发式指导。[Microagents](https://docs.openhands.dev/openhands/usage/prompting/microagents)

**真正值得参考**

- event-sourced conversation 适合恢复、回放、UI 同步和离线评测。
- runtime 抽象让本机、容器、远端执行共享上层 Agent。

**对 LANTU**

事件日志与 runtime 接口值得借鉴；完整平台化、Kubernetes 和 Web 多租户超出当前项目定位。

### 7. Cline

**可验证事实**

- Cline 是开源 coding agent，覆盖 IDE、CLI 和 SDK；仓库描述和代码可验证其支持 human-in-the-loop 工具审批、MCP、浏览器自动化、Plan/Act 与 checkpoints。[源码](https://github.com/cline/cline) · [官方文档](https://docs.cline.bot/)
- checkpoints 保存任务过程中的 workspace 快照，允许比较或恢复；browser 工具可观察网页并执行点击、输入、截图。[Checkpoints](https://docs.cline.bot/features/checkpoints) · [Browser](https://docs.cline.bot/features/browser-use) · [MCP](https://docs.cline.bot/mcp/mcp-overview)
- 最新可见仓库发布为 **desktop-v0.0.7，2026-07-29**；该 tag 只代表桌面组件版本，不能当成整个产品的统一版本。[发布](https://github.com/cline/cline/releases/tag/desktop-v0.0.7)

**真正值得参考**

- 将 diff、终端、浏览器结果都变成可审批的具体证据。
- checkpoint 不等同于 Git commit，可覆盖尚未准备提交的中间状态。

**对 LANTU**

LANTU 已有 file history/rewind。更有价值的是统一展示每轮变更与验证证据，而非再建一种快照格式。浏览器工具可作为 MCP 扩展，不应进入核心。

### 8. Roo Code

**可验证事实**

- Roo Code 是开源 IDE agent，支持自定义 modes、MCP、checkpoints、rules，以及用 Orchestrator/“Boomerang Tasks”把子任务委派给不同 mode。[源码](https://github.com/RooCodeInc/Roo-Code) · [Modes](https://docs.roocode.com/features/custom-modes) · [Boomerang Tasks](https://docs.roocode.com/features/boomerang-tasks) · [MCP](https://docs.roocode.com/features/mcp/using-mcp-in-roo)
- 最新可见发布为 **v3.54.0，2026-05-15**。[发布](https://github.com/RooCodeInc/Roo-Code/releases/tag/v3.54.0)

**真正值得参考**

- mode 不只是提示词：它同时限制可编辑路径、可用工具与职责。
- 子任务返回摘要而非完整上下文，有利于控制上下文污染。

**对 LANTU**

把现有 built-in agents 的工具过滤扩展成声明式能力清单、路径范围和预算即可；不必复制大量角色模板。

### 9. Aider

**可验证事实**

- Aider 是终端 pair programmer，repo map 用 tree-sitter 提取定义和引用，再用图排序选择最相关符号，以有限 token 给模型代码库结构。[Repo map](https://aider.chat/docs/repomap.html) · [实现](https://github.com/Aider-AI/aider/blob/main/aider/repomap.py)
- Architect mode 让一个模型先提出方案，另一个 editor 模型落地；Aider 还能在编辑后自动 lint/test 并尝试修复错误。[Architect mode](https://aider.chat/docs/usage/modes.html#architect-mode) · [Lint/test](https://aider.chat/docs/usage/lint-test.html)
- 每次改动自动形成 Git commit，并提供 undo；最新正式发布为 **v0.86.0，2025-08-09**。[Git integration](https://aider.chat/docs/git.html) · [发布](https://github.com/Aider-AI/aider/releases/tag/v0.86.0)

**真正值得参考**

- repo map 是低复杂度、可解释的代码检索方案，比一开始引入 embedding/vector DB 更适合 LANTU。
- 自动 lint/test 是最朴素但最有效的反馈闭环。
- architect/editor 分离可以路由不同成本模型，但收益应通过评测验证。

### 10. OpenCode（新近代表）

**可验证事实**

- OpenCode 是开源 coding agent，采用 client/server 架构，提供 TUI、桌面/网页客户端、SDK、LSP、MCP、自定义 agents、权限和 headless server；源码是这些接口存在的直接证据。[源码](https://github.com/anomalyco/opencode) · [文档](https://opencode.ai/docs/) · [Server](https://opencode.ai/docs/server/) · [SDK](https://opencode.ai/docs/sdk/) · [LSP](https://opencode.ai/docs/lsp/)
- 最新可见发布为 **v1.18.9，2026-07-28**。[发布](https://github.com/anomalyco/opencode/releases/tag/v1.18.9)

**真正值得参考**

- 内核 server 化后，终端 UI 不再拥有业务状态，便于远程控制、自动化和多前端。
- LSP 诊断直接进入 Agent 上下文，比仅靠 `grep` 和测试更快发现局部错误。

**对 LANTU**

优先借鉴事件协议和 LSP adapter，不建议现在复制桌面/网页多端产品面。

### 11. Google Jules（新近代表）

**可验证事实**

- Google 于 **2025-05-20** 开放 Jules 公测。它是异步 coding agent：导入 GitHub 仓库，在云端 VM 中制定计划、修改和测试，再让用户审阅并创建 PR。[官方发布](https://blog.google/technology/google-labs/jules/) · [官方站点](https://jules.google/) · [帮助文档](https://jules.google/docs/)
- Jules 的核心差异不是另一套聊天 UI，而是任务可离线运行、计划先确认、执行过程可查看、结果以代码变更交付。

**营销边界**

- “减少切换上下文”等属于目标；可验证的是异步 VM、GitHub 集成、计划和 PR 交付机制。

## 四、LANTU 能力基线

根据本仓库 2026-07-30 的源码和测试，LANTU 已有：

- 多供应商 client、流式 Agent 循环和上下文压缩；
- Bash、读写/编辑、glob/grep、工具按需搜索；
- 权限模式、规则、危险命令检查和 Linux/macOS 沙箱；
- MCP、Skills、Hooks、自动记忆、会话恢复；
- Plan、review、rewind、worktree；
- 子 Agent、共享任务、mailbox、team 与 in-process/tmux/iTerm2 backend；
- inline/TUI/非交互/remote 多种入口。

依据：[README](../../README.md)、[`lantu/agent.py`](../../lantu/agent.py)、[`lantu/runtime`](../../lantu/runtime)、[`lantu/teams`](../../lantu/teams)、[`tests`](../../tests)。

因此下面不建议再做“有 MCP”“有子 Agent”“有 plan mode”这类同质化功能。

## 五、建议排序

评分：价值、复杂度、风险均为 1-5；价值越高越好，复杂度/风险越低越好。

| 排名 | 能力 | 价值 | 复杂度 | 关键依赖 | 主要风险 | 建议 |
|---:|---|---:|---:|---|---|---|
| 1 | 统一 verifier：test/lint/type/LSP diagnostics | 5 | 2 | subprocess；可选 LSP | 自动修复循环失控、慢测试 | **短期做** |
| 2 | JSONL 事件与 trace replay | 5 | 2 | 现有 trace/serialization | 日志泄密、schema 演进 | **短期做** |
| 3 | LSP adapter：diagnostics/definition/references/symbols | 5 | 3 | 用户已有 language server | 跨语言兼容、进程管理 | **短期试点** |
| 4 | 持久后台任务状态机 | 5 | 4 | SQLite/事件存储、runtime 抽象 | 孤儿进程、恢复一致性 | **中期做** |
| 5 | 轻量 repo map | 4 | 3 | tree-sitter，可选 NetworkX | 索引陈旧、语言覆盖 | **中期做** |
| 6 | Agent 预算与交接协议 | 4 | 2 | 现有 teams/task/mailbox | 子任务提前终止 | **短期做** |
| 7 | GitHub Issues/PR adapter | 4 | 3 | `gh` CLI 或 GitHub App | 权限、外部副作用 | **中期做** |
| 8 | 扩展清单：版本/能力/信任声明 | 3 | 2 | 现有 skills/MCP/hooks | 生态兼容负担 | **短期做** |
| 9 | runtime 抽象到容器/远端 | 4 | 4 | Docker/SSH/云资源 | 密钥、网络、成本 | **后做** |
| 10 | 模型路由与 architect/editor 分离 | 3 | 3 | 多模型配置、评测集 | 成本增加未必增益 | **先评测** |
| 11 | A2A/ACP 等远程 Agent 协议 | 3 | 4 | 稳定事件/任务模型 | 标准变化、兼容成本 | **暂缓** |
| 12 | 浏览器/Computer Use 内建 | 2 | 4 | Playwright/视觉模型 | prompt injection、脆弱 | **用 MCP，不进核心** |

### 1. 统一 verifier 的最小实现

新增一个与具体命令无关的结果模型：

```text
VerificationRun
  command / kind / cwd
  status / exit_code / duration
  diagnostics[]: file, line, severity, code, message
  stdout_excerpt / stderr_excerpt
```

执行流程：文件修改完成 → 根据项目配置运行 verifier → 结构化结果写入 conversation 和 trace → 失败时允许有限次数修复 → 最终回答必须列出实际运行和未运行的验证。

不要让模型自由发明每个项目的验证命令。优先读取项目配置；自动探测只能作为建议并需确认。

### 2. 事件协议与回放

先统一内部事件，再考虑 server 化：

```text
session.started
turn.started / turn.completed
model.requested / model.delta / model.completed
tool.requested / permission.decided / tool.completed
file.changed
verification.completed
agent.spawned / agent.completed
session.failed / session.completed
```

每条事件含 `schema_version`、`session_id`、`turn_id`、时间、父事件、耗时和脱敏后的 payload。提供 `lantu -p ... --output-format jsonl` 及 `lantu trace replay`。这会同时解决 UI 解耦、CI 集成、故障报告和离线评测四个问题。

### 3. LSP 先于向量数据库

第一阶段只实现标准 JSON-RPC 客户端和四个只读能力：diagnostics、definition、references、workspace symbols。没有可用 language server 时静默降级到现有 grep/glob。

理由：结果有文件与行号、可解释、随编辑更新；依赖由用户项目提供。语义向量索引的成本、更新一致性和收益都更难控制。

### 4. 后台任务必须有状态机

建议状态：`queued → provisioning → running → waiting_approval → verifying → completed/failed/cancelled`。每个状态变化写持久事件；进程重启后可判断任务是否能恢复。任务必须有 deadline、预算、取消传播、环境标识、Git 基线和产物清单。

不要把现有 remote 模式直接包装成“云 agent”：没有持久状态、环境隔离和恢复语义时，它只是远程 UI。

## 六、90 天实施顺序

### 第 1 阶段（1-3 周）

1. 定义版本化事件 schema，把现有 trace 接入 JSONL。
2. 为非交互模式补稳定退出码、结构化错误和工具/权限/验证事件。
3. 建 verifier 接口，先支持配置命令及 pytest/ruff/mypy 通用解析。

验收：同一失败任务可由 JSONL 解释“模型做了什么、哪个工具失败、验证为何失败”，且不依赖 UI 截图。

### 第 2 阶段（4-7 周）

1. 接入一个语言（建议 Python）的 LSP diagnostics/definition/references。
2. 为子 Agent 增加 token、时间、工具次数预算和结构化 handoff。
3. 建 20-50 个本仓库真实任务的回归集，记录成功率、成本、耗时和人工干预次数。

验收：新增能力必须在回归集上证明收益；否则不默认开启。

### 第 3 阶段（8-12 周）

1. 在事件模型上实现持久 Task 状态机和取消/恢复。
2. 以本地子进程或 worktree 做第一个后台 runtime，不急于上云。
3. 做薄 GitHub adapter：从 issue 取任务、推分支、创建 draft PR，并保持每个外部写操作显式授权。

## 七、不值得照搬

- **堆角色和提示词模板**：LANTU 已有 agent/skill；没有评测时只增加维护和上下文噪声。
- **默认多 Agent 处理所有任务**：成本、等待和合并冲突会放大。仅对可独立、可验证的子任务并行。
- **先建云平台/Kubernetes**：对教学型本地终端工具投入过大；先把 runtime 和任务协议做深。
- **自建浏览器自动化核心**：安全面和维护成本高，通过 MCP/插件按需接入。
- **把自动 commit 当 checkpoint**：会污染用户历史。继续用独立 file history/rewind，并把 Git commit 留给明确里程碑。
- **先上向量数据库**：代码库规模和收益未验证；先用 LSP + repo map。
- **追逐私有 UI 特效**：Cursor/Cline 的 IDE 体验难在终端复刻，也不是 LANTU 的核心优势。
- **相信“自主完成”营销词**：可靠性应以 LANTU 自己的固定任务集、验证通过率和人工干预次数衡量。

## 八、决策原则

每个候选能力进入主线前回答四个问题：

1. 它是否让错误更早暴露，或让执行更容易解释？
2. 它能否用本仓库固定任务集量化收益？
3. 它是否保持本地优先、模型无关和 UI 无关？
4. 失败或缺少依赖时，能否降级而不破坏基本 Agent 循环？

按这四条，LANTU 下一版本最合理的主题不是“更多自治”，而是 **可验证、可回放、可恢复的自治**。

## 参考来源说明

- GitHub “最新发布”日期来自各官方仓库 Releases 页面，查询于 2026-07-30；tag 可能只覆盖仓库中的某个组件，报告已对 Cline 特别注明。
- 官方文档多为持续更新页面，若页面未标发布日期，本文不推测日期。
- 本文没有采用媒体评测、聚合榜单、个人博客或供应商之间的比较表。SWE-bench 等成绩也未用于排序，因为运行配置、模型、工具和数据污染条件常不可直接比较。
