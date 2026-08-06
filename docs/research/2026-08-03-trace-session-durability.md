# 开源 Coding Agent 的 Session、Trace 与失败恢复

> 调研日期：2026-08-03  
> 范围：Codex CLI、Gemini CLI、OpenHands SDK、OpenCode。仅引用官方源码、官方文档和仓库内基准。

## 结论

开源项目主要采用两种设计：

1. **Session 是事实来源，Trace 是旁路遥测**：Codex、Gemini CLI。Session 写入更可靠；Trace/telemetry 失败不阻塞 Agent。
2. **持久事件日志是事实来源，Session 是事件投影**：OpenHands、OpenCode。执行、恢复和观测共享同一批持久事件，因此一致性更强，但实现复杂度更高。

不存在一种合理设计能同时满足“Session 与独立 Trace 文件严格一致”以及“任意一个写入失败时 Agent 仍继续运行”。两个独立文件无法通过普通追加写获得原子性；要严格一致，只能使用单一事实来源，或把两份数据放进同一个数据库事务。

对 LANTU 最合适的方向是：**将现有 Session JSONL 深化为版本化的 Session Journal，保存恢复与故障判断所需的核心事件；CCWhat 直接读取它。可选的详细 Trace 只保存大体积、可丢失的诊断数据。**

## 对比

| 项目 | 事实来源 | Trace/遥测 | 写入失败 | 崩溃恢复 |
|---|---|---|---|---|
| Codex CLI | canonical rollout JSONL | 独立 rollout trace bundle | rollout 缓冲、重开文件并重试；trace 多处明确 best-effort | 从 rollout 恢复；未写后缀保留在内存等待下一次 flush |
| Gemini CLI | chat recording JSONL | OpenTelemetry/Clearcut/UI telemetry | 普通记录错误抛出；磁盘满时关闭记录但继续对话；telemetry 异步且失败只记 debug | 逐行读取，忽略单条 JSON 解析错误；支持 rewind/checkpoint 记录 |
| OpenHands SDK | 持久 EventLog | 事件本身同时用于观察 | append 写失败或锁超时直接抛出 | 重放事件重建状态；Action 无 Observation 表示执行中崩溃 |
| OpenCode | SQLite durable events | 内存发布/客户端同步是持久事件的投影 | 持久事件和本地投影在同一事务提交；监听器失败被隔离 | 按 aggregate + sequence 重放；检测序列冲突和重放分歧 |

## Codex CLI

### Session

Codex 把 rollout JSONL 明确定义为 canonical session rollout。写入由后台任务串行处理，使用容量为 256 的 channel 保持顺序并避免调用线程阻塞。

写入失败后，它不会直接丢弃事件：

- 待写事件先进入 `pending_items`。
- 只有单条成功写入后才从队列移除。
- flush 失败时关闭文件句柄，重新打开并重试一次。
- 第二次仍失败时将错误返回调用方。
- 后台 writer 的终止错误会保存，后续 recorder 调用可以得到真实失败原因。

来源：[Codex rollout recorder](https://github.com/openai/codex/blob/main/codex-rs/rollout/src/recorder.rs)

### Trace

Codex 另有 rollout trace bundle：manifest、顺序化事件 JSONL 和独立 payload 文件。Writer 每次追加都会 flush；payload 先写，引用它的事件后写，避免事件指向尚未落盘的 payload。

但调用侧普遍使用 `append_with_context_best_effort` 和 `write_json_payload_best_effort`，失败时记录错误或直接忽略，不终止 Agent。说明这个 Trace 是诊断投影，不是恢复事实来源。

来源：[Trace writer](https://github.com/openai/codex/blob/main/codex-rs/rollout-trace/src/writer.rs)、[tool trace 调用](https://github.com/openai/codex/blob/main/codex-rs/rollout-trace/src/tool_dispatch.rs)、[inference trace 调用](https://github.com/openai/codex/blob/main/codex-rs/rollout-trace/src/inference.rs)

## Gemini CLI

Gemini CLI 使用 append-only chat recording JSONL 保存会话。消息有 ID 和时间戳，同时支持 `$rewindTo` 与 `$set` 记录，加载时按顺序重建当前会话。

失败策略：

- 单条损坏 JSON 在加载时被忽略，其余有效记录仍可恢复。
- 一般初始化或写入错误会抛出。
- `ENOSPC`（磁盘空间不足）是明确例外：禁用后续 chat recording，但 Agent 继续运行，并提示当前对话不会保存。
- telemetry 与 Session 分离，采用异步发送；失败只写 debug 日志，不影响对话。

来源：[ChatRecordingService](https://github.com/google-gemini/gemini-cli/blob/main/packages/core/src/services/chatRecordingService.ts)、[Telemetry loggers](https://github.com/google-gemini/gemini-cli/blob/main/packages/core/src/telemetry/loggers.ts)、[Session management](https://github.com/google-gemini/gemini-cli/blob/main/docs/cli/session-management.md)

## OpenHands SDK

OpenHands 采用事件溯源。`EventLog` 是持久事件列表，每个事件独立写入文件，并使用文件锁支持线程和进程并发。写入失败和锁超时会抛出，不会假装成功。

恢复时扫描连续事件索引并重建内存状态。工具执行被两条事件包围：

```text
ActionEvent -> 执行工具 -> ObservationEvent
```

若崩溃后只有 Action、没有匹配的 Observation，恢复逻辑可识别为未完成动作。官方仓库还用 433 个 SWE-Bench Verified 会话、39,870 个事件测试生产持久化与重放路径；报告称中位会话恢复约 5ms，最大观测会话低于 20ms。该数据只证明其给定环境中的系统开销，不代表 Agent 任务成功率。

来源：[EventLog](https://github.com/OpenHands/software-agent-sdk/blob/main/openhands-sdk/openhands/sdk/conversation/event_store.py)、[事件溯源基准](https://github.com/OpenHands/software-agent-sdk/blob/main/scripts/event_sourcing_benchmarks/README.md)、[事件丢失回归测试](https://github.com/OpenHands/software-agent-sdk/blob/main/tests/cross/test_event_loss_repro.py)

## OpenCode

OpenCode 当前源码包含持久事件表和每个 aggregate 的序列号。durable event、序列更新以及调用方提供的本地 projection commit 在同一个 SQLite transaction 中完成，提交后才通知订阅者。

关键处理：

- `(aggregate_id, seq)` 有唯一约束。
- replay 检查事件 ID、类型、内容和连续序列；出现分歧时失败。
- durable event 的监听器失败被捕获并记录，不回滚已经提交的事实。
- 客户端实时事件只是持久事件的投影，可以通过 sequence 补读。

这是四个项目中一致性最强的方案，但引入了数据库、事务、schema migration、projection 和 replay 所有权等明显复杂度。

来源：[EventV2](https://github.com/anomalyco/opencode/blob/dev/packages/core/src/event.ts)、[事件表](https://github.com/anomalyco/opencode/blob/dev/packages/core/src/event/sql.ts)、[Event bridge](https://github.com/anomalyco/opencode/blob/dev/packages/opencode/src/event-v2-bridge.ts)

## 对 LANTU 的建议

### 不建议：同时写完整 Session 和完整 Trace

这会产生双写问题：

```text
Session 成功，Trace 失败
Trace 成功，Session 失败
进程在两次写入之间崩溃
```

除非使用同一个数据库事务，否则无法严格保证一致。

### 建议：Session Journal 作为单一事实来源

第一阶段继续使用 `.lantu/sessions/<session-id>.jsonl`，但将它从“消息存档”升级为版本化的 Session Journal。核心事件包括：

```text
session.started
turn.started
message.created
tool.started
tool.completed
permission.decided
error.occurred
turn.completed
session.completed
```

每条记录包含：

```text
schema_version, event_id, session_id, turn_id, sequence, timestamp, type, payload
```

现有会话恢复只消费它需要的事件；CCWhat 的 `LantuAdapter` 消费同一 Journal，转换为 normalized events。这样两者不会产生语义漂移。

### 可选 Trace 只放非关键细节

若以后需要，可增加 `.lantu/traces/` 保存：

- token 流 delta；
- 原始模型请求/响应；
- 大体积工具输出；
- 性能采样；
- 调试栈。

这些内容可以 best-effort，因为核心故障链已经存在 Session Journal 中。

### 写入失败策略

建议借鉴 Codex 的 recorder：

1. 单 writer 保证顺序。
2. 事件进入内存 pending queue。
3. 成功写入并 flush 后才从 pending 移除。
4. 失败时重开文件并重试一次。
5. 再次失败则停止当前 Agent，向用户报告“会话无法持久化”。
6. 加载时接受最后一行因崩溃而截断，但不静默忽略中间序列缺口。

工具必须先持久化 `tool.started` 再执行；工具结束后持久化 `tool.completed`。恢复时发现未匹配的 `tool.started`，标记为 interrupted。对于可能有外部副作用的工具，不应自动重试，必须让用户确认。

## 最终判断

LANTU 不需要建立两个同级、都要求强可靠的 Session 和 Trace。更稳妥的设计是：

```text
Agent 执行
  -> Session Journal（强可靠、事实来源）
       -> 会话恢复
       -> CCWhat LantuAdapter
       -> 可选详细 Trace 投影
```

这兼顾一致性、故障诊断和第一阶段实现成本，也符合 Codex 的 canonical rollout 与 OpenHands/OpenCode 的事件事实来源思路。
