# Coding Agent 崩溃与未完成工具恢复

> 调研日期：2026-08-03  
> 范围：Codex CLI、Claude Code、OpenHands、OpenCode、Qwen Code。仅引用官方文档、官方源码和官方 issue/PR。

## 一句话结论

这些项目的“恢复会话”通常只是恢复历史上下文，**不等于重新执行崩溃前的工具**。能明确看到实现的项目都避免盲目重跑：把悬空工具调用补成中断/失败结果，或者停下来让用户决定下一步。

| 项目 | 恢复对话 | 识别未完成工具 | 自动重跑未完成工具 |
|---|---|---|---|
| Codex CLI | 支持 | 支持恢复 rollout 历史；具体见下文 | 未发现官方承诺会自动重跑 |
| Claude Code | 支持 `--resume` / `--continue` | 官方公开资料未完整说明硬崩溃后的配对算法 | 未发现官方承诺会自动重跑 |
| OpenHands | 持久化事件与状态，可继续会话 | Action 无 Observation 可被识别；主动中断时补合成错误 | 否 |
| OpenCode | 持久化消息和工具状态 | pending/running 会转成 interrupted error | 否 |
| Qwen Code | JSONL 会话可恢复 | 明确分类 `interrupted_turn` | 否；需用户确认，并补合成失败结果 |

## Qwen Code

这是最直接、最适合 LANTU 参考的实现。

- 恢复时，从持久化历史尾部识别两种中断：用户消息没有模型回复，或模型发出了 `functionCall` 但没有 `functionResponse`。
- 对悬空工具调用，它不会重新执行工具，而是为每个调用构造一个合成的失败 `functionResponse`，让协议历史重新合法。
- `interrupted_turn` 的 `canAutoContinue` 固定为 `false`，`requiresUserConfirmation` 固定为 `true`。
- 用户确认继续后，只把“工具中断失败”结果交给模型；模型再决定下一步，而不是恢复器直接重跑原工具。

来源：

- [中断识别与合成工具结果](https://github.com/QwenLM/qwen-code/blob/main/packages/core/src/core/turn-interruption.ts)
- [统一 Session Recovery Plan](https://github.com/QwenLM/qwen-code/blob/main/packages/core/src/core/session-recovery.ts)
- [恢复安全设计：不自动重放未知工具](https://github.com/QwenLM/qwen-code/blob/main/docs/design/session-crash-recovery/session-crash-recovery-interruption-detection.md)
- [Headless 模式的 continueInterrupted 执行入口](https://github.com/QwenLM/qwen-code/blob/main/packages/cli/src/nonInteractiveCli.ts)

## OpenCode

- 会话消息中持久化工具的 `pending`、`running`、`completed`、`error` 状态。
- 当前执行被取消或中断时，清理逻辑把仍在处理的工具改成 `status: error`，错误为 `Tool execution aborted`，并写入 `metadata.interrupted: true`。
- 如果加载历史时仍看到 `pending` 或 `running`，提供给模型的历史会补成 `[Tool execution was interrupted]` 的错误结果，避免留下不合法的悬空 tool use。
- Agent 循环会忽略这种已经标记为 interrupted 的孤立工具，并退出，不会把它当成待执行任务重新运行。

来源：

- [中断清理：将工具状态改为 error](https://github.com/anomalyco/opencode/blob/dev/packages/opencode/src/session/processor.ts)
- [历史转换：为 pending/running 工具生成中断结果](https://github.com/anomalyco/opencode/blob/dev/packages/opencode/src/session/message-v2.ts)
- [Agent 循环忽略 interrupted orphan](https://github.com/anomalyco/opencode/blob/dev/packages/opencode/src/session/prompt.ts)
- [官方恢复行为测试](https://github.com/anomalyco/opencode/blob/dev/packages/opencode/test/session/prompt.test.ts)

## OpenHands

- `EventLog` 持久化事件，恢复时加载事件和 Conversation State；工具调用以 `ActionEvent` 和 Observation 类事件配对。
- 主动中断时，OpenHands 查找没有 Observation 的 Action，为它们补 `AgentErrorEvent`，内容是工具在完成前被中断，然后把会话状态设为 `PAUSED`。
- 下一次 `run()` 是从已保存事件上下文继续 Agent，不是直接重新调用那一个工具。
- 对“进程被强杀，来不及执行中断清理”的情况，源码能够证明持久化事件仍可识别不匹配 Action；但没有找到官方实现承诺自动重跑该工具。因此不能把它解读成可靠的工具续跑机制。

来源：

- [EventLog 持久化实现](https://github.com/OpenHands/software-agent-sdk/blob/main/openhands-sdk/openhands/sdk/conversation/event_store.py)
- [中断时补 orphan action 错误](https://github.com/OpenHands/software-agent-sdk/blob/main/openhands-sdk/openhands/sdk/conversation/impl/local_conversation.py)
- [Action/Observation 配对约束](https://github.com/OpenHands/software-agent-sdk/blob/main/openhands-sdk/openhands/sdk/context/view/properties/tool_call_matching.py)
- [InterruptEvent 语义](https://github.com/OpenHands/software-agent-sdk/blob/main/openhands-sdk/openhands/sdk/event/user_action.py)

## Codex CLI

待补充官方源码核对结果。

## Claude Code

待补充官方资料核对结果。

## 对 LANTU 的直接建议

第一版采用和 Qwen Code、OpenCode、OpenHands 相同的保守原则：

1. Session Journal 恢复完整历史，并找出没有 `tool.completed` / `tool.failed` 的 `tool.started`。
2. 把它标记成 `interrupted`，但不自动调用工具。
3. 上下文恢复到可继续交互的状态，并明确告诉模型和用户该工具结果未知。
4. 用户确认后开始一个新的 Turn；由模型根据当前文件和环境重新判断是否需要再次调用工具。

这不是简单地“只恢复到最后一个完整对话”，而是：**保留中断事实，修复对话协议，但不假装工具没执行过，也不盲目重试。**
