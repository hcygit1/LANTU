# LANTU Development Validation Log

本文持续记录新增功能、验收方法、实测结果和遗留风险。新增功能完成后，应在这里追加记录；不能只记录测试命令，必须说明验证了什么行为。

## 记录规则

每项记录至少包含：

- 功能变化：修改前后的行为差异。
- 自动测试：相关测试和完整测试结果。
- 真实验收：如涉及模型、网络、缓存或交互，记录真实运行指标。
- 结论与遗留项：明确通过、失败或未覆盖，不能用单元测试代替真实验收。

## 2026-09-09：上下文压力分析与分层策略

### 功能变化

- 增加上下文压力报告，区分 `none`、`soft` 和 `hard`。
- 增加 `CompactionPolicy`，支持 `defer`、`compact_stale_tools`、`structured_summary` 和 `window_rollover`。
- 修改前所有压力主要进入完整摘要；修改后优先选择影响更小的局部驱逐。

### 验收结果

- 验证压力来源统计、用户边界限制、缓存健康时延迟、hard pressure 决策。
- 当 hard pressure 且当前 Conversation 已含摘要时，策略返回 `window_rollover`。
- 阶段性完整测试：`999 passed, 14 skipped`。

### 结论

策略决策和压力统计通过自动测试。此阶段尚未把 `window_rollover` 接入真实切换。

## 2026-09-09：陈旧工具结果局部驱逐

### 功能变化

- 只清理近期保留窗口之前的大型工具结果。
- 大结果先写入 `.lantu/session/tool-results/`，Conversation 中替换为 `<persisted-output>` 引用。
- Journal 增加 `context.tool_results_compacted`，恢复 Session 时按 `tool_use_id` 重放引用替换。
- 完整摘要和局部驱逐不再共享 artifact 清理行为；artifact 只由 Session 删除生命周期回收。

### 自动测试

- 验证最近消息不变、工具调用和结果不拆对、大结果先落盘、Journal 可恢复引用。
- 验证删除一个 Session 只删除其独占 artifact，不删除其他 Session 仍引用的文件。

### 真实模型验收

- 使用真实 `glm-5.2` 构造大型旧工具结果并触发 `compact_stale_tools`。
- 唯一标记：`RECOVERY_CODE=LAN20260909_X7Q4`。
- Journal 成功记录驱逐事件，artifact 文件存在并包含完整标记。
- Session 恢复后保留 `<persisted-output>` 引用。
- Agent 首次成功找回标记；后续暴露出 Grep 不能搜索单文件路径的问题。

### 结论

局部驱逐、落盘和恢复链路通过。发现的单文件检索缺口在下一项修复。

## 2026-09-09：Grep 支持单文件路径

### 功能变化

- 修改前 `Grep` 只接受目录；artifact 已有精确文件路径时不能直接搜索。
- 修改后 `path` 可以是目录或单个文件，目录行为保持不变。

### 验收结果

- 新增单文件路径测试。
- 完整测试：`1000 passed, 14 skipped`。

### 结论

压缩后的 artifact 可以通过精确路径使用 `Grep` 重新检索。

## 2026-09-09：真实 Provider 缓存基线

### 验收方法

- 命令：`uv run python bench/lantu_live_cache.py --runs 1 --turns 1 ...`
- 模型：真实 `glm-5.2`。
- 数据来源：Provider 返回的 `usage.recorded.cache_read_tokens`，不使用字符估算。

### 验收结果

- 模型请求：13。
- 总体缓存命中率：87.8%。
- 排除首次请求后的 warm 命中率：93.1%。
- 错误：0。
- 临时 Session：`session_20260909_095255_tbtn`，验收后已删除。

### 结论

真实运行下前缀缓存可以稳定复用。该结果是当时配置和 Provider 缓存状态的观测值，不作为固定性能承诺。

## 2026-09-09：同 Session 窗口切换

### 功能变化

- 修改前 `WINDOW_ROLLOVER` 只有策略枚举，实际仍会落入普通结构化摘要。
- 修改后 hard pressure 且已有摘要时，在同一 Session 内创建新的 Conversation Window。
- Journal 增加 `context.window.rolled_over`，记录 `window_id`、`parent_window_id`、归档序列、摘要、保留消息、artifact 引用、FileLedger 序列和 Schema Epoch。
- Session 恢复只投影最新窗口；旧消息仍完整保存在 Journal。
- artifact 文件不会因切换窗口删除，需要时由 Agent 重新调用读取或搜索工具。

### 自动测试

- 验证真实选择 `WINDOW_ROLLOVER`，而不是普通摘要。
- 验证 Session ID 不变、Window ID 更新、最新窗口恢复、artifact 索引保留、旧工具结果不进入新投影。
- 接入窗口切换后的阶段性完整测试：`1002 passed, 14 skipped`。

### 真实模型验收

- 脚本：`uv run python bench/lantu_window_rollover.py`。
- 模型：真实 `glm-5.2`。
- Session：`session_20260909_134609_lpt9`。
- 动作：`window_rollover`。
- 切换前估算：99,000 tokens。
- 切换后估算：5,823 tokens。
- 减少：93,177 tokens，约 94.1%。
- Window ID：从 `window_7b212454ad0c4c5f9e17125d5336111e` 变为 `window_7be6b4bc971d4ae8b37689527acfd7cf`。
- 重启恢复后 Session ID 保持不变，Window ID 保持为新窗口。
- `context.window.rolled_over` 位于 Journal sequence 17。
- artifact 文件在恢复后仍存在，路径已进入 rollover 事件索引。
- 旧工具结果没有直接进入恢复后的 Conversation 投影。
- Agent 在新窗口重新调用 `ReadFile`，工具输出和最终回答均找回 `LANTU_WINDOW_20260909_Q9M4`。
- 模型请求：3；Provider 报告 prompt tokens 46,332，cached tokens 24,064，缓存命中率 51.94%。
- 错误：0；最终结果：通过。

### 结论

真实模型环境下，窗口切换、上下文释放、artifact 召回和 Session 重启恢复全部通过。切换请求会形成新的消息边界，因此本次缓存命中率低于稳定 warm 基线，属于预期观测；后续应继续积累不同任务规模的数据。

## 2026-09-09：Lens Window 分组界面

### 功能变化

- 修改前 Events 页只有连续事件流，只能看到单条 rollover 事件。
- 修改后 Lens API 同时返回原始 `events` 和结构化 `windows`。
- Events 页按 Window 折叠展示，显示父窗口、Journal 序列范围、事件数量和 Current 状态。
- 旧窗口默认折叠，当前窗口默认展开；没有 Window 元数据的旧 Session 显示为 `window_legacy`。

### 自动测试

- 验证 rollover 事件是新窗口的第一条事件。
- 验证父子窗口、活动窗口和旧 Session 兼容投影。
- 验证 Web API 保留原始事件并返回 Window 分组。
- Lens 定向测试：`7 passed`。

### 浏览器验收

- Playwright 使用本机 Chrome，测试桌面 `1440x900` 和移动端 `390x844`。
- 两种视口均识别 2 个窗口：旧窗口折叠，当前窗口展开。
- 页面和窗口标题均无水平溢出。
- 首次移动端截图发现 Window 标题与长 ID 重叠；调整为明确的响应式网格后重新验收通过。
- 截图：`%TEMP%/lantu-lens-windows-desktop.png`、`%TEMP%/lantu-lens-windows-mobile.png`。

### 结论

Window 分组的数据、交互和桌面/移动布局通过验收。

### 最终回归

- 命令：`uv run pytest -q`。
- 结果：`1004 passed, 14 skipped in 29.00s`。
- `uv run python -m compileall -q lantu` 通过。
- `git diff --check` 未发现空白错误，仅显示工作区既有的 LF/CRLF 转换提醒。

## 当前遗留范围

- 跨 Session handoff 尚未实现，作为未来扩展。
- 真实缓存命中率受 Provider 缓存状态、请求内容和窗口边界影响，需要长期积累，不应以单次结果作为保证。

## 2026-09-09：Remote、CodeSearch 和 RepoMap 收敛

### 功能变化

- Remote MCP 初始化现在绑定统一 `CodeSearch`，并隐藏 `mcp_zvec_grep_*` 原始工具。
- Remote `/compact` 现在把摘要、窗口切换和工具结果驱逐事件写入 Session Journal；无活动 turn 时使用 notification turn 持久化。
- 语义搜索降级链统一为 `zvec semantic → 官方 rg → 内置 Grep`，并标记降级元数据。
- 删除 RepoMap 实现、配置、Agent 接口、命令和专用测试，仓库检索统一使用 `CodeSearch`。

### 验收

- 完整测试：`1000 passed, 14 skipped`。
- RepoMap 代码引用扫描：仅保留历史设计文档和测试名称说明，无运行时引用。
