# Kimi CLI 能力评测调研

调研日期：2026-08-13

## 核心结论

Kimi 对 Coding Agent 的能力评测分为两层：

1. 用 Terminal-Bench 2 的小规模固定任务集做日常准确率冒烟，检查 CLI 或 Agent 框架改动后能力是否明显退化。
2. 用更大规模的 Coding 与 Agentic benchmark 对外评估完整能力，包括真实软件工程、终端操作、长程任务、MCP 工具使用和成本效率。

## Kimi CLI 仓库内的能力冒烟测试

官方仓库的 `tests_ai/accuracy_smoke` 明确采用 Harbor + Terminal-Bench-2：

- 固定选择 15 个任务；
- 任务不需要额外 API key、不依赖 GPU，运行时间适合开发阶段；
- Harbor 和 Terminal-Bench-2 都固定版本，保证不同代码版本之间可以稳定比较；
- 测试当前仓库代码，而不是已经发布的安装包；
- 每个任务收集 reward，最终生成汇总结果；
- smoke 和 nightly 可以使用不同预算。

来源：[Accuracy Smoke (Harbor + Terminal-Bench-2)](https://github.com/MoonshotAI/kimi-cli/tree/main/tests_ai/accuracy_smoke)

## 大规模能力评测

Kimi 官方发布材料展示的 Coding Agent 相关评测包括：

- Kimi Code Bench v2：内部真实代码任务；
- Program Bench、MLS Bench Lite：代码实现与软件工程；
- Terminal-Bench：终端环境中的完整任务成功率；
- DeepSWE、SWE-bench 系列：真实仓库问题修复；
- MCP Atlas、MCP Mark Verified：MCP 工具调用能力；
- Kimi Claw 24/7 Bench：长时间自主执行任务。

官方还同时报告任务得分与 token 使用变化，用于判断能力提升是否以更高成本换来。

来源：[Kimi K2.7 Code](https://www.kimi.com/en/resources/kimi-k2-7-code)、[Kimi benchmark best practices](https://platform.kimi.com/docs/guide/benchmark-best-practice)

## 对 LANTU 的直接启示

LANTU 当前不需要立刻跑完整 Terminal-Bench。更接近 Kimi CLI 的第一阶段做法是：

- 从公开 benchmark 固定选择 10-15 个有区分度的小任务；
- 每次框架关键改动后使用相同模型、参数和任务重跑；
- 比较任务成功率、异常率、运行时间、token 成本；
- 用 LANTU Lens 对失败任务做轨迹归因；
- 稳定后再扩大到 SWE-bench 或更完整的 Terminal-Bench。

这种测试的重点不是证明 GLM-5.2 有多强，而是判断“同一个 GLM-5.2 经过 LANTU 后，LANTU 的改动有没有让它变好或变坏”。
