# LANTU 开发交接记录

更新时间：2026-09-08

本文用于下一次会话继续开发当前的上下文管理与压缩策略。

## 一、当前目标

在不丢失原始事实、尽量保护前缀缓存的前提下，对上下文压力进行分级处理：

```text
无压力       → 不处理
软压力       → 缓存健康时延后处理
工具结果占主导 → 优先局部驱逐旧工具结果
普通历史占主导 → 结构化摘要压缩
重复压缩后再次接近上限 → 后续实现窗口切换
```

当前已经实现到“局部驱逐旧工具结果”，窗口切换尚未实现。

## 二、已经完成

### 1. 上下文压力分析

文件：`lantu/context/manager.py`

新增：

- `ContextPressureReport`
- `CompactionPolicy`
- `analyze_context_pressure(...)`

压力报告会统计：

- 当前 token 数和上下文窗口占用率
- 旧区工具结果 token 数
- 旧对话 token 数
- 最近保留消息 token 数
- 已有摘要 token 数
- 当前主要占用来源
- 推荐处理动作

策略动作包括：

- `none`
- `defer`
- `compact_stale_tools`
- `structured_summary`
- `window_rollover`

### 2. 局部驱逐旧工具结果

文件：`lantu/context/manager.py`

新增：`compact_stale_tool_results(...)`

行为：

- 只处理保留窗口之前的旧消息。
- 最近消息不改。
- 用户消息和助手决策不改。
- 已落盘的大工具结果只保留短引用。
- 尚未落盘但超过大小限制的工具结果，先写入 session 工具结果目录，再替换成引用。
- 短工具结果保持原样。
- 原始完整结果仍保存在文件中，可恢复和查询。

### 3. 接入自动压缩入口

文件：`lantu/context/manager.py`、`lantu/agent.py`

`auto_compact(...)` 现在会先分析压力，再决定是否执行动作。

局部驱逐成功时返回 `CompactEvent(action="compact_stale_tools")`，不会执行完整摘要流程。

交互 Agent 对局部驱逐和完整摘要分别处理：

- 局部驱逐：不清空 File Ledger，不重复注入环境和长期记忆。
- 完整摘要：保持原有的摘要、重建上下文和重新注入流程。

用户边界通过 `iteration == 1` 传入；工具链执行中不会主动做局部驱逐。

### 4. Journal 持久化与恢复

文件：

- `lantu/memory/journal.py`
- `lantu/memory/session.py`
- `lantu/ui/inline/app.py`
- `lantu/remote.py`
- `lantu/__main__.py`

新增 Journal 事件：

```text
context.tool_results_compacted
```

事件保存被替换的 `tool_use_id` 和新引用内容。

恢复 Session 时，Journal 投影会按 `tool_use_id` 重放替换，因此不会出现：

```text
运行时已经是文件引用
恢复后却重新出现旧的完整工具输出
```

### 5. 保留运行时状态的历史替换

文件：`lantu/conversation.py`

新增：`replace_history_preserving_runtime(...)`

局部驱逐只改变历史消息内容，不重置环境和长期记忆注入标记，避免下一轮重复插入运行时上下文。

同时会重置旧 token 用量锚点，因为消息内容已发生变化。

### 6. 导出接口

文件：`lantu/context/__init__.py`

已导出：

- `CompactionPolicy`
- `ContextPressureReport`
- `compact_stale_tool_results`

## 三、测试情况

已新增：

- 压力分析测试
- 压缩策略测试
- 局部驱逐测试
- 超大原始工具结果先落盘测试
- Journal 恢复局部驱逐结果测试

已验证：

```text
tests/test_context.py
78 passed

tests/test_session_journal.py::test_resume_replays_local_tool_result_compaction
1 passed
```

相关测试组合当前结果：

```text
118 passed
2 failed
```

剩余 2 个失败是旧提醒测试仍检查 `message.reminder_key`，而当前提醒已经改成 appendix 增量块：

- `test_deferred_tool_reminder_is_not_repeated_across_iterations`
- `test_plan_mode_reminder_is_not_repeated_while_content_is_unchanged`

这两个失败不是本次局部驱逐逻辑引入的，但后续应更新断言，使其检查新的 appendix 结构。

## 四、当前未完成

### 1. 完整测试集

尚未运行完整测试集。下一步先更新上述 2 个过期测试，再运行相关测试和完整测试。

### 2. 窗口切换

`CompactionPolicy.WINDOW_ROLLOVER` 目前只是策略结果，尚未真正实现窗口切换。

后续需要设计：

- 新窗口如何继承 Session Journal
- 当前任务状态如何保存和恢复
- File Ledger、Schema Epoch、长期记忆如何重新注入
- 旧窗口如何通过指针保留，而不是复制全部历史
- Lens 如何展示窗口之间的关系

在窗口切换实现前，不要把 `window_rollover` 当作已完成能力对外宣称。

### 3. 局部驱逐的端到端验证

目前已有单元测试，但还没有在真实交互任务中确认：

- 大工具结果是否确实先落盘
- 局部驱逐后模型是否能通过引用继续工作
- Journal 恢复后上下文是否与运行中一致
- 局部驱逐是否改善缓存命中率
- 是否会影响任务完成率

### 4. Lens 展示

`context.tool_results_compacted` 已写入 Journal，但 Lens 前端还没有专门的事件展示文案。当前可以先按普通事件显示，后续再增加“工具结果已局部驱逐”的可读展示。

## 五、当前工作区注意事项

本次修改尚未提交或推送。

当前工作区还存在其他未提交内容，包括：

- 缓存测试结果文件
- Tianshu 参考项目目录
- 研究文档
- 其他之前的测试修改

下一次提交前必须先用 `git diff` 分类确认，避免把第三方项目、测试结果或研究资料与本功能混在一起提交。

## 六、建议的下一步顺序

```text
1. 更新 2 个过期的 appendix 提醒测试
2. 运行相关测试，确认没有回归
3. 补充 Journal 事件和 Lens 的可读展示
4. 编写一个真实大工具结果的交互测试
5. 观察缓存命中率和任务完成率
6. 再决定是否实现窗口切换
```

## 七、继续开发时的关键约束

- Journal 是唯一事实来源，运行态变更必须可恢复。
- 原始工具结果不能因为上下文压缩而删除。
- 局部驱逐只处理旧工具结果，不修改用户消息、助手决策和最近消息。
- 完整摘要与局部驱逐必须区分，不能对两者执行相同的环境重注入逻辑。
- 不要为了提高缓存命中率而让模型失去继续读取原始事实的能力。
- 暂时不要实现窗口切换，先完成局部驱逐的真实任务验证。
