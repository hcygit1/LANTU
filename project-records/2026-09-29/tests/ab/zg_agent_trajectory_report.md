# ZG Agent 轨迹 A/B 测试报告

## 实验信息

- **日期**：2026-09-10
- **模型**：`deepseek-v4-flash`
- **运行次数**：每组 1 次
- **任务数**：每组 5 个连续代码定位任务
- **Baseline**：禁用全部 MCP，`CodeSearch` 降级到本地 `rg`
- **Optimized**：只启用本地 zvec-grep MCP
- **共同条件**：相同模型、任务顺序、提示、最大输出 Token 和权限模式

任务覆盖 File Ledger、文件结果去重、配置到客户端的调用链、窗口切换策略，以及 CodeSearch 与 MCP manager 的装配关系。

## 原始结果

| 指标 | Baseline rg | Optimized ZG | 变化 |
|---|---:|---:|---:|
| 自动判定通过 | 4/5 | 4/5 | 0 |
| 人工复核通过 | 4/5 | 5/5 | +1 |
| 总耗时 | 92.827 s | 78.080 s | -15.9% |
| 总工具调用 | 32 | 31 | -3.1% |
| CodeSearch | 17 | 19 | +2 |
| ReadFile | 15 | 12 | -20.0% |
| 未缓存输入 Token | 48,894 | 49,797 | +1.8% |
| 缓存读取 Token | 367,104 | 378,624 | +3.1% |
| 总提示 Token | 415,998 | 428,421 | +3.0% |
| 输出 Token | 3,958 | 3,382 | -14.6% |
| 运行错误 | 0 | 0 | 0 |

总提示 Token 按 `input_tokens + cache_read_tokens` 计算。两组缓存命中率分别约为 88.25% 和 88.38%。

## 判定修正

第 5 个任务的原始标准答案错误地要求 `lantu/mcp/manager.py`。实际 manager 注入发生在 `lantu/runtime/lifecycle.py` 的 `initialize_runtime_mcp`，再由 `CodeSearch.set_mcp_manager()` 保存。ZG 组准确找到了这条装配链路，Baseline 只找到了 CodeSearch 内部调用点，因此人工复核为 Baseline 4/5、ZG 5/5。测试脚本中的标准答案已修正，原始 JSON 保留首次运行数据。

## 结论

本次真实 Agent 轨迹中，ZG 提高了复杂装配关系任务的定位完整性，将 ReadFile 调用减少 20%，总耗时降低 15.9%，输出 Token 降低 14.6%。但总提示 Token 增加约 3%，原因是 ZG 返回的语义检索片段更丰富，且模型额外进行了 2 次 CodeSearch。因此当前结果支持“提高定位质量并减少文件读取”，尚不能证明“降低总 Token”。

## 限制

- 每组只运行一次，无法排除模型随机性。
- 只有 5 个任务，样本较小。
- 两组连续会话的缓存状态可能受 Provider 跨 Session 缓存影响。
- 后续正式结论应使用至少 10 个固定任务、每组重复 3 次，并报告均值、标准差和任务级配对差异。
