# Session Journal

Session Journal 是 Session 的唯一事实来源。每行是一个按 `sequence` 排序的 JSON 事件。

## Event Envelope

```json
{
  "schema_version": 1,
  "event_id": "evt_xxx",
  "session_id": "session_xxx",
  "runtime_id": "runtime_xxx",
  "turn_id": "turn_xxx",
  "sequence": 42,
  "timestamp": "2026-08-04T10:00:00Z",
  "type": "tool.started",
  "payload": {}
}
```

- `sequence` 在单个 Session 内严格递增。
- 不属于 Turn 的事件将 `turn_id` 设为 `null`。
- 第一版不增加 `parent_event_id`；事件通过 `runtime_id`、`turn_id` 和 payload 中的业务 ID 关联。

## Event Types

```text
session.created
runtime.started
runtime.stopped
runtime.interrupted
turn.started
turn.completed
turn.interrupted
message.created
context.compacted
tool.started
tool.completed
tool.failed
tool.interrupted
permission.decided
error.occurred
model.request.started
model.request.completed
model.request.failed
model.request.interrupted
usage.recorded
```

模型流式文本、UI 状态、Hook 内部步骤和原始 HTTP 请求/响应正文不属于第一版 Session Journal。模型请求的生命周期属于 Journal，原始 HTTP 数据由 Lens 的可选抓包功能独立保存。

## Message Event

`message.created` 保存完整的结构化 Message：

```json
{
  "message_id": "msg_xxx",
  "role": "assistant",
  "content": "我来读取文件",
  "tool_uses": [
    {
      "tool_call_id": "call_xxx",
      "tool_name": "ReadFile",
      "arguments": {"path": "README.md"}
    }
  ],
  "tool_results": [],
  "thinking_blocks": []
}
```

消息中的 `tool_uses` 表示模型提出的调用；`tool.started` 表示 LANTU 确实开始执行。两者通过 `tool_call_id` 关联。

## Tool Events

```text
tool.started
  tool_call_id, tool_name, arguments

tool.completed
  tool_call_id, tool_name, output, elapsed_ms

tool.failed
  tool_call_id, tool_name, error { type, message }, output, elapsed_ms

tool.interrupted
  tool_call_id, tool_name, reason, result_known=false
```

`completed` 只表示明确成功，`failed` 只表示明确失败，`interrupted` 表示最终结果未知。每条事件重复保存 `tool_name`，方便消费者独立解释事件。

## Session And Runtime Events

```text
session.created
  project_root, lantu_version

runtime.started
  mode=new|resume, work_dir, lantu_version

runtime.stopped
  reason=user_exit|session_switch

runtime.interrupted
  reason=missing_runtime_stopped, detected_by_runtime_id
```

`runtime.interrupted` 外壳的 `runtime_id` 是被中断的旧 Runtime，`detected_by_runtime_id` 是执行恢复的新 Runtime。

## Turn Events

一个 Journal Turn 对应一次完整用户请求；模型内部循环使用 Iteration 表示，不单独进入第一版事件清单。

```text
turn.started
  trigger=user|notification|recovery

turn.completed
  iteration_count

turn.interrupted
  reason, last_iteration
```

## Permission Event

```text
permission.decided
  tool_call_id, tool_name
  decision=allow|deny|allow_always
  source=user|rule|mode
```

所有权限决定都记录，包括规则和权限模式产生的自动决定。

## Error Event

`tool.failed` 表示工具正常返回了失败结果；`error.occurred` 表示 Agent 流程本身出现异常，两者不重复记录同一问题。

```text
error.occurred
  phase=model|agent|runtime|journal|recovery|capture
  error_type, message, retryable
  stack (optional)

## Usage Event

`usage.recorded` 在每次模型调用结束后记录本次增量，而不是记录累计值：

```text
provider, model
input_tokens, output_tokens
cache_read_tokens, cache_creation_tokens
```

## Model Request Events

每次模型调用在发送前生成稳定的 `model_call_id`：

```text
model.request.started
  model_call_id, provider, model

model.request.completed
  model_call_id, response_id (optional), elapsed_ms

model.request.failed
  model_call_id, error { type, message }, elapsed_ms

model.request.interrupted
  model_call_id, reason, result_known=false
```

启用 Lens 双证据模式时，LANTU 将同一个 `model_call_id` 临时放入 `X-LANTU-Model-Call-ID` 请求头。本地代理记录该 ID 后先删除请求头，再将请求转发给模型服务商。Lens 用 `model_call_id` 将 Journal 事件和 HTTP 请求一对一关联。

如果精确 ID 缺失，Lens 可按请求地址、模型、请求正文哈希和时间进行推测匹配，并明确标记为低置信度关联；不会将推测结果当作 Session 恢复事实，也不会因此自动重发模型请求。

原始 HTTP 抓包数据与 Journal 分开保存。抓包默认关闭；用户主动开启后，数据与对应 Session 一起长期保留。删除 Session 时，Journal、`.meta` 和该 Session 的 HTTP 抓包数据一并永久删除。

抓包默认保存完整的请求和响应正文，单条请求或响应设 `50 MB` 安全上限。超过上限时截断并标记“双证据不完整”。`Authorization`、API Key、Cookie 等敏感 Header 在写盘前删除；正文保持本地原始证据，仅在导出或分享时执行脱敏。

抓包是辅助观测，不是执行前提。代理无法转发时，模型请求按正常请求失败处理；代理已经完成转发但抓包写入失败时，不中断当前 Turn，而是在 Journal 写入 `error.occurred(phase=capture)`、立即向用户报警，并由 Lens 将该 Session 标记为“双证据不完整”。

恢复时，若发现 `model.request.started` 没有对应的 `completed` 或 `failed`，Session 追加 `model.request.interrupted`。抓包中可能存在的响应只供 Lens 排查，不用于恢复对话；该请求不自动重发。

## Ordering

`sequence` 只表示事件写入 Journal 的顺序，不表示并发工具的因果顺序。并发工具通过 `tool_call_id` 关联，并使用 `timestamp` 和 `elapsed_ms` 表示实际时间。

## LANTU Lens Reader

第一版由 `tools/lens/` 中的 Journal Reader 直接读取本地 `.lantu/sessions/*.jsonl`，并转换为 Lens 现有 normalized events：

Lens 可以实时轮询正在追加的 Journal，但始终以只读方式工作，不获取 Session 写锁。尾部未完成的 JSONL 行视为正在写入并等待下一次读取；中间损坏或序号断裂则停止该 Session 的刷新并报告精确错误。

```text
message.created(role=user)       -> kind=message, role=user
message.created(role=assistant)  -> kind=message, role=assistant
message.created.tool_uses        -> tool_call
tool.completed                   -> tool_result
tool.failed                      -> tool_result + is_error=true
tool.interrupted                 -> tool_result + result_unknown=true
error.occurred                   -> error
model.request.*                  -> model_call
```

LANTU Lens 的下游任务分段模块再把两种 `message` 分别解释为 `user_message` 和 `assistant_text`。

## Module Layout

```text
lantu/memory/journal.py
  JournalEvent, SessionJournal, JournalReader
  写入顺序、flush/fsync、重试、解析和损坏检测

lantu/memory/session.py
  Session, SessionManager, SessionMeta
  消息提交、生命周期和 Conversation 恢复投影
```

Journal 模块隐藏文件句柄和持久化细节；Session 模块负责业务语义。

## Journal Interface

```text
append(event_type, payload, runtime_id, turn_id) -> JournalEvent
read() -> list[JournalEvent]
checkpoint() -> None
close() -> None
```

模块内部生成 `event_id`、`session_id`、`sequence` 和 `timestamp`，并隐藏文件句柄、flush/fsync 与重试实现。

## Session Interface

```text
start_runtime(mode)
start_turn(trigger)
commit_message(message)
record(execution_event)
complete_turn(iteration_count)
stop_runtime(reason)
```

`record()` 接收有类型的执行事件对象，例如 `ToolStarted`、`ToolCompleted`、`PermissionDecided`、`ErrorOccurred` 和 `UsageRecorded`，再转换成对应 Journal 事件。恢复由 `SessionManager.resume(session_id)` 负责。

## Migration

项目尚无需要保留的用户 Session，因此新版本直接写 Journal v1，不实现旧 `SessionRecord` 格式兼容；目录继续使用 `.lantu/sessions/`。

## Writer Ownership

一个 Session 同时只能由一个 Runtime 写入。Runtime 启动时获取 Session 写锁；获取失败时拒绝可写恢复。CCWhat 以只读方式读取 Journal，不获取写锁。

写锁使用跨平台 `filelock`，锁的获取、释放和崩溃后的自动释放由 Journal 模块隐藏。

## Verification Scope

第一版验证分三层：

1. Journal 单元测试：顺序、重试、截断末行、中间损坏、sequence 缺口。
2. Session 集成测试：生命周期、工具中断恢复、Conversation 重建、`.meta` 重建和写失败隔离。
3. LANTU Lens 契约测试：消息、工具调用/结果、错误、interrupted 标记、`model_call_id` 精确关联和低置信度降级关联。

另加一个单写入 Runtime 锁测试。第一版不做真实断电测试，只保证 LANTU 进程崩溃恢复。

`tool.started` 作为原始执行事实保留在 `raw`/metadata 中，不再次生成 `tool_call`，避免与 `message.created.tool_uses` 重复。Runtime、Turn、权限和用量事件保留在原始数据或聚合字段中，后续可扩展分析。
```
