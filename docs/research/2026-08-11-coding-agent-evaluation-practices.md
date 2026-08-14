# Claude Code、OpenAI Codex 与 Kimi CLI 的官方评测方式

> 调研日期：2026-08-11
>
> 来源范围：厂商官方文档、官方博客/技术报告、官方 GitHub 仓库与发布材料。
>
> 说明：本文将“公开事实”和“推断”严格分开。公开榜单中的很多数字评的是“模型 + 指定 agent harness”，不能自动视为完整 CLI 产品成绩。

## 一、核心结论

| 项目 | Claude Code / Anthropic | OpenAI Codex | Kimi CLI / Kimi Code |
|---|---|---|---|
| 公开或内部 benchmark | SWE-bench 系列、Terminal-Bench 2.1、FrontierCode，以及未公开的 Claude Code 内部质量回归集 | SWE-bench、OpenAI 内部 SWE 任务；当前模型材料还报告 SWE-Bench Pro、DeepSWE、Terminal-Bench 2.1 等 | 最新材料报告 DeepSWE、Terminal-Bench 2.1、FrontierSWE、Kimi Code Bench 2.0 等；旧 Kimi CLI 仓库另有 15 题 Terminal-Bench-2 smoke |
| 结果判定 | 优先看环境终态和测试；必要时再用 LLM rubric 判断代码质量 | 公开 benchmark 由各自 grader 判定；OpenAI 内部任务的详细 grader 未公开 | 公开 benchmark 使用各自 verifier/judge；内部 benchmark 同时使用任务分数、专家盲评或内部 rubric，但细节不全 |
| 轨迹/工具评估 | 明确评估，且建议人工阅读失败轨迹；内部 Agent Behavior 类评测还看工具效率和纪律 | 官方通用 eval 方法支持 trace grading；Codex 可导出结构化事件和 rollout trace，但是否用于所有内部榜单未公开 | Kimi K3 的 Agent Behavior Bench 明确看工具使用、效率和纪律；技术报告也做失败轨迹分析 |
| token、成本、延迟 | Claude Code telemetry 可记录 token、估算成本、TTFT、API/工具耗时；部分 benchmark 按真实 token 计费 | Codex 结构化事件记录 token；官方模型材料比较 token、完成时间及模拟延迟，但完整 benchmark 成本表通常未公开 | Kimi K3 报告对 4 类任务比较每题推理成本；Kimi CLI smoke 脚本本身只汇总 reward 和错误数 |
| 重复运行 | 通常每题 5 次，并同时建议看 `pass@k` 与 `pass^k` | 官方通用方法要求重复 eval run，但当前 Codex 公开编码表格的 trial 数通常未披露 | Kimi K2.5 编码任务曾公开为 5 次独立运行平均；Kimi K3 只对部分任务披露 3/5 次；Kimi CLI smoke 默认每题一次 |

最重要的事实是：**harness 会显著影响成绩**。Anthropic 公开展示过同一模型在 `mini-SWE-agent` 与 Codex harness 下得到不同结果；Kimi K3 的 Terminal-Bench 2.1 表格甚至对部分模型取“跨 harness 最佳分”。因此，厂商发布表不能直接用于判断 Claude Code、Codex、Kimi Code 三个完整产品谁更强。[Anthropic system card](https://www.anthropic.com/claude-fable-5-mythos-5-system-card)；[Kimi K3 技术报告](https://github.com/MoonshotAI/Kimi-K3/blob/main/k3_tech_report.pdf)

## 二、Claude Code / Anthropic

### 1. 使用哪些 benchmark

**公开事实**

- Claude Code 产品早期主要依赖员工和外部用户反馈，后来增加“简洁性、文件编辑、过度设计”等内部 eval，并结合生产监控、A/B 测试和用户研究。内部任务集和具体分数没有公开。[Demystifying evals for AI agents](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents)
- Anthropic 的公开 coding benchmark 包括 SWE-bench 系列、Terminal-Bench 2.1 和 FrontierCode 等。最新 system card 对不同 benchmark 明确写出任务数量、harness、上下文和重复运行配置。[Fable 5 / Mythos 5 System Card](https://www.anthropic.com/claude-fable-5-mythos-5-system-card)
- Terminal-Bench 2.1 使用 `mini-SWE-agent` harness、GKE 环境、89 个任务，每题 5 次，共 445 个 trials。Anthropic 还说明旧 `Terminus-2` harness 超时更多，导致结果更噪。[Fable 5 / Mythos 5 System Card](https://www.anthropic.com/claude-fable-5-mythos-5-system-card)
- 早期 SWE-bench 评测公开了较完整 scaffold：只有 Bash 与 Edit 两类工具、无网络，直到模型结束或耗尽 200K 上下文；部分成功任务超过 100K token 和数百轮交互。[SWE-bench with Claude](https://www.anthropic.com/engineering/swe-bench-sonnet)

### 2. 如何判定任务结果

**公开事实**

- Anthropic 把一次任务尝试称为 `trial`，并强调 grader 应检查环境的最终状态，而不是相信 Agent 自己声明“完成”。[Demystifying evals for AI agents](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents)
- 编码任务通常先用确定性测试判正确性，再用 LLM rubric 评估代码质量。以 SWE-bench Verified 为例，核心要求是修复目标失败测试且不破坏已有测试。[Demystifying evals for AI agents](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents)
- FrontierCode 使用真实开源 PR、容器、隐藏单测和加权 rubric，并报告 `mean@5`，所以不是只检查 Agent 的最终文本回答。[Fable 5 / Mythos 5 System Card](https://www.anthropic.com/claude-fable-5-mythos-5-system-card)

### 3. 是否评估轨迹和工具调用

**公开事实**

- Anthropic 将 trial 的轨迹定义为最终输出、工具调用、推理过程和中间结果，并建议人工阅读失败轨迹，以区分 Agent、grader、任务和基础设施问题。[Demystifying evals for AI agents](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents)
- Claude Code 的 `stream-json` 输出包含工具事件、session metadata 和子 Agent 的 `parent_tool_use_id`；Hooks 能在工具调用前后记录输入、结果和失败，具备离线轨迹分析基础。[Headless mode](https://code.claude.com/docs/en/headless)；[Hooks](https://code.claude.com/docs/en/hooks)
- 最新 Anthropic 内部 Agent Behavior Bench 不只看任务完成，还评估工具使用行为、效率和纪律。[Fable 5 / Mythos 5 System Card](https://www.anthropic.com/claude-fable-5-mythos-5-system-card)

### 4. 是否统计 token、成本和延迟

**公开事实**

- Claude Code 的 OpenTelemetry 指标可以记录 session 数、输入/输出/缓存 token、估算成本、active time、API 请求时长、TTFT、工具耗时和子 Agent 层级。[Claude Code monitoring](https://code.claude.com/docs/en/monitoring-usage)
- FrontierCode 的成本按每次 trial 实际 API token、实际缓存命中率和公开单价计算。[Fable 5 / Mythos 5 System Card](https://www.anthropic.com/claude-fable-5-mythos-5-system-card)

### 5. 是否重复运行衡量稳定性

**公开事实**

- 最新 system card 的通用配置是每题平均 5 次 trial；SWE-bench 各变体也采用 5 次平均。[Fable 5 / Mythos 5 System Card](https://www.anthropic.com/claude-fable-5-mythos-5-system-card)
- Anthropic 建议同时报告 `pass@k` 和 `pass^k`：前者衡量至少一次成功，后者衡量连续稳定成功；每次 trial 应从干净环境开始。[Demystifying evals for AI agents](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents)

### 6. 未公开细节

- Claude Code 内部质量回归集的题目、提示词、权重、版本和具体成绩未公开。[Demystifying evals for AI agents](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents)
- 最新公开模型 benchmark 是否直接运行完整 Claude Code 产品，而非裁剪后的 benchmark harness，未全部说明。[Fable 5 / Mythos 5 System Card](https://www.anthropic.com/claude-fable-5-mythos-5-system-card)
- 产品 A/B 测试的流量划分、显著性、失败样本和发布门槛未公开。[Demystifying evals for AI agents](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents)

## 三、OpenAI Codex

### 1. 使用哪些 benchmark

**公开事实**

- Codex 最初公开报告 SWE-bench Verified 和 OpenAI 内部 SWE 任务；内部集被描述为真实内部工程任务。SWE-bench 中有 23 个无法在内部基础设施运行的样本被排除，使用 192K 最大上下文和 medium reasoning。[Introducing Codex](https://openai.com/index/introducing-codex/)
- 当前 GPT-5.6 官方材料的 coding eval 包括 Artificial Analysis Coding Agent Index、SWE-Bench Pro、DeepSWE 1.1 和 Terminal-Bench 2.1。公开表格展示的是模型在指定 agent 环境中的成绩，不等同于 Codex CLI 全产品回归测试。[GPT-5.6](https://openai.com/index/gpt-5-6/)
- OpenAI 还维护内部工程任务，但当前 Codex 产品内部回归集的规模、版本和题目没有公开。[Introducing Codex](https://openai.com/index/introducing-codex/)

### 2. 如何判定任务结果

**公开事实**

- 公开 benchmark 使用各 benchmark 的 grader 或验证环境计算最终分数；OpenAI 最新发布材料没有完整公开每项 coding benchmark 的 grader、timeout、镜像和逐题结果。[GPT-5.6](https://openai.com/index/gpt-5-6/)
- OpenAI 官方通用 agent eval 方法建议先做 trace grading，再把失败模式固化成 dataset 和重复 eval run；grader 可检查最终结果或整个轨迹。[Agent evals](https://developers.openai.com/api/docs/guides/agent-evals)；[Evaluation best practices](https://developers.openai.com/api/docs/guides/evaluation-best-practices)

### 3. 是否评估轨迹和工具调用

**公开事实**

- OpenAI 的通用 trace 定义包括模型调用、工具调用、guardrail 和 handoff，并支持对整个 trace 做 grading。[Agent evals](https://developers.openai.com/api/docs/guides/agent-evals)
- `codex exec --json` 的公开源码会输出线程、轮次、命令执行、文件修改、MCP、子 Agent、错误和完成事件；完成事件包含输入、缓存输入、缓存写入、输出和 reasoning token。[Codex exec events](https://github.com/openai/codex/blob/main/codex-rs/exec/src/exec_events.rs)
- Codex 公开仓库还提供 opt-in rollout trace，可保存请求、响应、工具输入输出、终端输出、compaction 和多 Agent 关系，并归约成语义图。[Codex rollout trace](https://github.com/openai/codex/blob/main/codex-rs/rollout-trace/README.md)

**事实边界**

- 上述接口证明 Codex 能采集和评估轨迹，但 OpenAI 没有公开证明每一个 Codex 产品 benchmark 都使用了同一套 trace grader。

### 4. 是否统计 token、成本和延迟

**公开事实**

- Codex 完成事件记录输入、缓存输入、缓存写入、输出和 reasoning token，因此可以按统一价格离线计算成本。[Codex exec events](https://github.com/openai/codex/blob/main/codex-rs/exec/src/exec_events.rs)
- GPT-5.6 Ultra 默认并行四个 Agent；官方材料公开比较单 Agent 与多 Agent 的分数、token 和完成时间关系。[GPT-5.6](https://openai.com/index/gpt-5-6/)
- GPT-5.4 的 coding 延迟曲线来自生产行为的离线模拟，包含工具执行时间、采样 token 和输入 token；OpenAI 明确提醒真实延迟可能明显不同。[GPT-5.4](https://openai.com/index/introducing-gpt-5-4/)
- 当前 GPT-5.6 Sol API 的公开价格为输入 `$5/M`、缓存输入 `$0.5/M`、输出 `$30/M`，但公开 coding benchmark 未提供完整逐任务账单。[GPT-5.6 Sol model docs](https://developers.openai.com/api/docs/models/gpt-5.6-sol)

### 5. 是否重复运行衡量稳定性

**公开事实**

- OpenAI 官方通用 eval 方法要求固定 dataset 后重复运行 eval，以观察回归和波动。[Agent evals](https://developers.openai.com/api/docs/guides/agent-evals)

**未公开**

- 当前 GPT-5.6 coding 表格没有公开完整 trial 次数、随机种子、置信区间、`pass@k` 或 `pass^k`。[GPT-5.6](https://openai.com/index/gpt-5-6/)

### 6. 未公开细节

- 最新 Codex benchmark 的完整 system prompt、工具 schema、Codex CLI/Cloud 版本、沙箱镜像和 timeout 未公开。[GPT-5.6](https://openai.com/index/gpt-5-6/)
- Codex 产品内部回归集、人工复核流程、逐题分数和发布门槛未公开。[Introducing Codex](https://openai.com/index/introducing-codex/)
- 最新公开成绩大多是“模型 + Codex harness”的能力成绩，不能直接视为完整 Codex CLI 产品成绩。[GPT-5.6](https://openai.com/index/gpt-5-6/)

## 四、Kimi CLI / Kimi Code

### 范围说明

旧 `MoonshotAI/kimi-cli` 仓库已声明逐步迁移到新 `MoonshotAI/kimi-code`。截至调研日，产品公开评测证据分为两层：旧 Kimi CLI 仓库中的小型回归 smoke，以及 Kimi K3 技术报告中使用 Kimi Code、Claude Code、Codex 等 harness 的模型评测。[Kimi CLI README](https://github.com/MoonshotAI/kimi-cli)；[Kimi Code repository](https://github.com/MoonshotAI/kimi-code)

### 1. 使用哪些 benchmark

**公开事实**

- Kimi K3 的公开 coding suite 包括 DeepSWE、ProgramBench、Terminal-Bench 2.1、FrontierSWE、SWE-Marathon、PostTrainBench、MLS-Bench-Lite、SciCode，以及内部 Kimi Code Bench 2.0。[Kimi K3 README](https://github.com/MoonshotAI/Kimi-K3)
- Kimi K3 在不同任务上使用 Kimi Code、Claude Code 或 Codex 三类 harness。Terminal-Bench 2.1 对所有模型报告跨 harness 最佳成绩，因此该表不是固定单一框架的公平对照。[Kimi K3 技术报告](https://github.com/MoonshotAI/Kimi-K3/blob/main/k3_tech_report.pdf)
- 内部 Kimi Code Bench 2.0 面向真实端到端软件工程任务，覆盖多种语言和生产技术栈；技术报告表格还分别给出 Kimi Code、Claude Code、Codex harness 下的部分结果。[Kimi K3 技术报告](https://github.com/MoonshotAI/Kimi-K3/blob/main/k3_tech_report.pdf)
- 旧 Kimi CLI 仓库提供 `Accuracy Smoke`：从 Terminal-Bench-2 选择 15 个不需要外部 API key、无 GPU 要求且运行时间适中的任务，固定 Harbor 0.5.0 和 Terminal-Bench-2 commit，用当前源码构建 wheel 后在容器中运行。[Accuracy Smoke README](https://github.com/MoonshotAI/kimi-cli/blob/cbc15c076d17f70fec9f89c90c0502e68657f505/tests_ai/accuracy_smoke/README.md)；[15-task list](https://github.com/MoonshotAI/kimi-cli/blob/cbc15c076d17f70fec9f89c90c0502e68657f505/tests_ai/accuracy_smoke/terminal_bench_2_tasks_default.txt)

### 2. 如何判定任务结果

**公开事实**

- Kimi CLI smoke 通过 Harbor 运行每个 Terminal-Bench-2 任务，并从 `result.json` 汇总 `reward_mean` 与 `n_errors`；具体 reward 仍由每个任务及 Harbor grader 定义，而不是由 Kimi CLI 自己宣告成功。[run_smoke.sh](https://github.com/MoonshotAI/kimi-cli/blob/cbc15c076d17f70fec9f89c90c0502e68657f505/tests_ai/accuracy_smoke/scripts/run_smoke.sh)
- Kimi K3 的公开 benchmark 沿用各任务的 verifier：例如 SWE-Marathon 保留 correctness 与 anti-cheat validators；FrontierSWE 使用官方脚本重算；MCP-Atlas 使用 Gemini 3.1 Pro 作为 judge。[Kimi K3 技术报告](https://github.com/MoonshotAI/Kimi-K3/blob/main/k3_tech_report.pdf)
- Kimi Webdev Bench 使用专家盲评，按代码质量、功能完整性、视觉还原和交互体验比较输出；这属于主观质量评估，不是隐藏测试通过率。[Kimi K3 技术报告](https://github.com/MoonshotAI/Kimi-K3/blob/main/k3_tech_report.pdf)

### 3. 是否评估轨迹和工具调用

**公开事实**

- Kimi K3 的内部 Agent Behavior Bench 明确把评估从结果正确性扩展到过程质量，评分包括工具使用行为、效率和纪律。[Kimi K3 技术报告](https://github.com/MoonshotAI/Kimi-K3/blob/main/k3_tech_report.pdf)
- Kimi K3 的网络安全评测对失败轨迹做了归因，识别出错误策略、无效调试循环、最终交付物验证不足等模式。[Kimi K3 技术报告](https://github.com/MoonshotAI/Kimi-K3/blob/main/k3_tech_report.pdf)
- Kimi CLI 源码 telemetry 会记录工具名、成功/错误/取消、耗时、重复调用和 trace id；这证明产品具备工具轨迹观测能力，但不代表 smoke benchmark 已将这些字段纳入分数。[toolset telemetry](https://github.com/MoonshotAI/kimi-cli/blob/cbc15c076d17f70fec9f89c90c0502e68657f505/src/kimi_cli/soul/toolset.py)

### 4. 是否统计 token、成本和延迟

**公开事实**

- Kimi K3 技术报告专门比较 Kimi Code Bench 2.0、BrowseComp、GDPval-AA v2 和 AA-Briefcase 的每题推理成本；Kimi Code Bench 2.0 成本来自内部实测，其他部分来自自测或公开 API 价格。[Kimi K3 技术报告](https://github.com/MoonshotAI/Kimi-K3/blob/main/k3_tech_report.pdf)
- Kimi CLI 运行时能获得 LLM 输入/输出 token，并记录工具耗时；但公开 Accuracy Smoke 脚本只汇总 reward 和错误数，没有统一输出 token、美元成本或端到端延迟。[KimiSoul usage](https://github.com/MoonshotAI/kimi-cli/blob/cbc15c076d17f70fec9f89c90c0502e68657f505/src/kimi_cli/soul/kimisoul.py)；[run_smoke.sh](https://github.com/MoonshotAI/kimi-cli/blob/cbc15c076d17f70fec9f89c90c0502e68657f505/tests_ai/accuracy_smoke/scripts/run_smoke.sh)

### 5. 是否重复运行衡量稳定性

**公开事实**

- Kimi K2.5 的官方材料曾明确写出：coding tasks 的成绩均为 5 次独立运行平均；SWE-bench 系列使用内部最小工具框架，Terminal-Bench 2.0 使用 Terminus-2，而不是 Kimi CLI。[Kimi K2.5 README](https://github.com/MoonshotAI/Kimi-K2.5)
- Kimi K3 对 PostTrainBench 披露为 3 次平均，视觉任务通常 3 次、ZeroBench 5 次；其余主要 coding benchmark 没有统一公开 trial 数。[Kimi K3 技术报告](https://github.com/MoonshotAI/Kimi-K3/blob/main/k3_tech_report.pdf)
- Kimi CLI Accuracy Smoke 的公开脚本逐题调用一次 `harbor run`，没有 `n` 次 trial 循环，因此默认 smoke 不能衡量随机稳定性。[run_smoke.sh](https://github.com/MoonshotAI/kimi-cli/blob/cbc15c076d17f70fec9f89c90c0502e68657f505/tests_ai/accuracy_smoke/scripts/run_smoke.sh)

### 6. 未公开细节

- Kimi Code Bench 2.0 的完整 80 道任务、提示词、隐藏测试、权重和评分规则未公开。[Kimi K3 技术报告](https://github.com/MoonshotAI/Kimi-K3/blob/main/k3_tech_report.pdf)
- Kimi K3 多数 coding benchmark 的逐题结果、随机种子、trial 数和置信区间未公开。[Kimi K3 技术报告](https://github.com/MoonshotAI/Kimi-K3/blob/main/k3_tech_report.pdf)
- “Kimi Code harness”的完整评测配置、版本、权限、timeout 与系统提示未全部公开。[Kimi K3 技术报告](https://github.com/MoonshotAI/Kimi-K3/blob/main/k3_tech_report.pdf)
- Kimi CLI smoke 没有公开纳入 token、成本、延迟或轨迹质量的统一报告格式。[Accuracy Smoke README](https://github.com/MoonshotAI/kimi-cli/blob/cbc15c076d17f70fec9f89c90c0502e68657f505/tests_ai/accuracy_smoke/README.md)；[run_smoke.sh](https://github.com/MoonshotAI/kimi-cli/blob/cbc15c076d17f70fec9f89c90c0502e68657f505/tests_ai/accuracy_smoke/scripts/run_smoke.sh)

## 五、对 LANTU 对比实验的推断

以下是基于上述事实的建议，不是三家官方共同标准：

1. **固定同一模型后比较完整框架。** LANTU 与 OpenCode 使用相同模型、提示词、仓库 commit、容器、网络权限、时间和 token 预算，只改变 agent framework。
2. **结果以环境终态为主。** 使用隐藏测试和回归测试判成功；代码质量 rubric 作为辅指标，不能替代测试。
3. **每题至少运行 5 次。** 同时报告 `pass@1`、`pass@5`、`pass^5` 和均值/置信区间，避免单次偶然结果。
4. **统一记录原始 usage。** 从模型 API 响应计算输入、缓存、输出和 reasoning token，再按同一价格表换算成本；延迟应包含工具执行和重试时间。
5. **轨迹单独评分。** 统一映射为 inspect、edit、execute、test、retry、delegate、finish，统计重复调用、工具错误、未测试完成、无效循环和人工介入。
6. **不要直接复用厂商榜单作结论。** 厂商公开数字常混用不同 harness、推理 effort、上下文、任务子集和“最佳 harness”，只能用来设计方法，不能作为 LANTU 的直接基线成绩。
