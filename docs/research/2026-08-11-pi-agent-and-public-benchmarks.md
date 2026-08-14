# Pi Agent 对比与公共 Coding Agent 基准

> 调研日期：2026-08-11
> 来源范围：Pi、Harbor、Terminal-Bench、SWE-bench 的原始仓库和官方文档。本文不采用媒体榜单或二手文章。

## 核心结论

1. **可以直接使用现成基准，但要自己重跑 LANTU 和 OpenCode。** Terminal-Bench 适合测完整 agent 的端到端终端能力；SWE-bench Verified 适合测真实仓库 bug 修复能力。不能把自己的结果直接和厂商发布分数横比，因为模型、agent、版本、超时和预算通常不同。
2. 用户所说的 Pi 对比，最符合公开证据的是 Pi 作者 Mario Zechner 的 [`badlogic/pi-terminal-bench`](https://github.com/badlogic/pi-terminal-bench/tree/0074c915dc7d8ceeba5f61b19e7b9aa078564fa3)。它不是新建 benchmark，而是给 Pi 写了一个 Harbor agent adapter，用 Terminal-Bench 2.0 的任务和 verifier 评分，再把 Pi 结果插入当时的公开榜单。
3. **这个 Pi 项目可借鉴“接入方法”，不能照搬为公平实验。** 它的展示表混用了不同模型；默认安装 Pi `latest`；榜单写死在代码中；排名只看正确率；错误 trial 另计但未进入正确率分母。因此它是便利脚本，不是严格控制变量的框架研究。
4. 对 LANTU vs OpenCode，最省事、最中立的第一版是：**用 Harbor 为两者各写一个 installed-agent adapter，固定同一模型和运行参数，同时跑 `terminal-bench@2.0` 和 `swe-bench-verified`，让 benchmark 自己的 verifier 判分。** Harbor 官方明确支持自定义 agent，且已把 Terminal-Bench 和 SWE-bench Verified 统一成相同的 task/trial/job 模型。[Harbor agents](https://github.com/laude-institute/harbor/blob/488af1b12b3b728b9364ab5e1bb663bd3e0ae643/docs/content/docs/agents/index.mdx)；[Harbor evals](https://github.com/laude-institute/harbor/blob/488af1b12b3b728b9364ab5e1bb663bd3e0ae643/docs/content/docs/run-jobs/run-evals.mdx)

## Pi 的公开对比是怎么做的

### 项目和作者

- 仓库是 [`badlogic/pi-terminal-bench`](https://github.com/badlogic/pi-terminal-bench/tree/0074c915dc7d8ceeba5f61b19e7b9aa078564fa3)，最后一次提交为 2025-12-01，作者是 Pi 作者 Mario Zechner。
- 项目 README 将自己定义为“Pi coding agent 的 Harbor adapter，用来运行 Terminal-Bench”。[README](https://github.com/badlogic/pi-terminal-bench/blob/0074c915dc7d8ceeba5f61b19e7b9aa078564fa3/README.md)
- Terminal-Bench 官方将基准定义为“任务数据集 + 把模型连接到终端沙箱的执行 harness”；每题包括英文指令、验证脚本和 oracle 解法。[Terminal-Bench README](https://github.com/laude-institute/terminal-bench/blob/d28711d0da2675d0bb1d56de45ae5df6082438a3/README.md)

### 如何运行

Pi adapter 继承 Harbor 的 `BaseInstalledAgent`，在每个任务容器中：

1. 安装 Node.js 22；
2. 用 npm 安装 `@mariozechner/pi-coding-agent`；
3. 以无交互模式运行 `pi --print --mode json`；
4. 把任务 instruction 原样传给 Pi；
5. 保存 Pi session 和 JSONL 事件，供 Harbor 形成轨迹和 usage。

对应实现见 [PiAgent](https://github.com/badlogic/pi-terminal-bench/blob/0074c915dc7d8ceeba5f61b19e7b9aa078564fa3/src/pi_terminal_bench/pi_agent.py) 和 [安装脚本](https://github.com/badlogic/pi-terminal-bench/blob/0074c915dc7d8ceeba5f61b19e7b9aa078564fa3/src/pi_terminal_bench/install-pi.sh.j2)。

仓库给出的主要命令是：

```bash
harbor run \
  -d terminal-bench@2.0 \
  --agent-import-path pi_terminal_bench:PiAgent \
  -m anthropic/claude-sonnet-4-5 \
  --k 5 \
  --jobs-dir ./pi-tbench-results
```

仓库中的 `run.sh` 实际选择 `anthropic/claude-opus-4-5`，每题 5 次、并发 4 个 trial。[run.sh](https://github.com/badlogic/pi-terminal-bench/blob/0074c915dc7d8ceeba5f61b19e7b9aa078564fa3/run.sh)

### 如何判分

- 每个 Terminal-Bench 任务自己的 verifier 检查容器最终状态并产生 reward，而不是相信 Pi 的最终文字回答。Harbor 把一个 agent 对一个任务的一次尝试称为 trial，本质是“一次 rollout 产生一个 reward”。[Harbor core concepts](https://github.com/laude-institute/harbor/blob/488af1b12b3b728b9364ab5e1bb663bd3e0ae643/docs/content/docs/core-concepts.mdx)
- Pi 的 `show-results.js` 读取 Harbor `result.json`，统计 reward 为 `1.0` 和 `0.0` 的数量，计算 `accuracy = passed / (passed + failed)`，并用伯努利标准误 `sqrt(p(1-p)/n)` 展示误差。[show-results.js](https://github.com/badlogic/pi-terminal-bench/blob/0074c915dc7d8ceeba5f61b19e7b9aa078564fa3/show-results.js)
- 同一脚本把 Pi 的 accuracy 插入一份“截至 2025-12-01”的硬编码 Terminal-Bench 2.0 榜单。榜单包括 Codex CLI、Claude Code、OpenHands、Mini-SWE-Agent、Terminus 2 等，但每行模型并不相同。[show-results.js](https://github.com/badlogic/pi-terminal-bench/blob/0074c915dc7d8ceeba5f61b19e7b9aa078564fa3/show-results.js)

### 模型、框架和成本是否受控

**没有严格受控。**

- README 示例用 Sonnet 4.5，`run.sh` 用 Opus 4.5；展示榜单还混有 GPT、Gemini、Kimi 和多模型系统。因此该排名回答的是“某个 agent+model 配置在榜单上的位置”，不是“只改变 agent 框架后谁更强”。
- 安装模板在未传 version 时使用 Pi `latest`，`pyproject.toml` 对 Harbor 只要求 `>=0.1.0`，不能从仓库本身重建唯一的软件组合。[安装脚本](https://github.com/badlogic/pi-terminal-bench/blob/0074c915dc7d8ceeba5f61b19e7b9aa078564fa3/src/pi_terminal_bench/install-pi.sh.j2)；[pyproject.toml](https://github.com/badlogic/pi-terminal-bench/blob/0074c915dc7d8ceeba5f61b19e7b9aa078564fa3/pyproject.toml)
- Adapter 会从 Pi 的 `message_end` 事件累计输入、输出、缓存 token 和美元成本，写入 Harbor `AgentContext`；说明数据能采集。但 `show-results.js` 完全没有用 token、成本或耗时排名。[PiAgent](https://github.com/badlogic/pi-terminal-bench/blob/0074c915dc7d8ceeba5f61b19e7b9aa078564fa3/src/pi_terminal_bench/pi_agent.py)；[show-results.js](https://github.com/badlogic/pi-terminal-bench/blob/0074c915dc7d8ceeba5f61b19e7b9aa078564fa3/show-results.js)
- 脚本读取 `n_errors`，但正确率分母只包含有 `1.0/0.0` reward 的 trial。若错误 trial 没有 reward，显示正确率会偏高。当前 Harbor 官方指标默认把 missing reward 当 0；复现时应采用官方聚合而不是这个旧脚本。[Pi 展示脚本](https://github.com/badlogic/pi-terminal-bench/blob/0074c915dc7d8ceeba5f61b19e7b9aa078564fa3/show-results.js)；[Harbor metrics](https://github.com/laude-institute/harbor/blob/488af1b12b3b728b9364ab5e1bb663bd3e0ae643/docs/content/docs/datasets/metrics.mdx)
- 仓库还要求手工修补当时 Harbor 的 `upload_dir` bug，避免 agent 预先创建 `/tests` 后 verifier 被复制到错误目录。这说明基础设施错误必须先单独排除，不能计成 agent 失败。[Pi README](https://github.com/badlogic/pi-terminal-bench/blob/0074c915dc7d8ceeba5f61b19e7b9aa078564fa3/README.md)

## LANTU/OpenCode 能否直接使用现成基准

### Terminal-Bench：可以，最适合先做

Harbor 官方支持不修改 Harbor 源码就接入自定义 agent：实现 `BaseAgent` 或 `BaseInstalledAgent`，然后用 `harbor run ... --agent path.to.agent:SomeAgent` 运行。官方文档还直接提供 Terminal-Bench 和 SWE-bench Verified 的命令。[Harbor agents](https://github.com/laude-institute/harbor/blob/488af1b12b3b728b9364ab5e1bb663bd3e0ae643/docs/content/docs/agents/index.mdx)；[Harbor evals](https://github.com/laude-institute/harbor/blob/488af1b12b3b728b9364ab5e1bb663bd3e0ae643/docs/content/docs/run-jobs/run-evals.mdx)

因此只需各写一个很薄的 adapter：安装固定 commit/tag 的 LANTU 或 OpenCode，调用其 headless 命令，把 provider/model、凭证和任务 instruction 传入，并解析两边 usage/轨迹。任务、容器和 verifier 都复用同一份 Terminal-Bench。

局限是 Terminal-Bench 不只测改代码，还包含编译、服务配置、数据处理等终端任务；它衡量“通用终端 agent”，不能单独代表代码仓库修复能力。

### SWE-bench Verified：可以，但有两种接法

SWE-bench Verified 有 500 个经工程师确认可解的真实仓库问题。每题给出 `repo`、`base_commit` 和 `problem_statement`；官方 evaluator 接收每题的 `instance_id`、`model_patch` 和 `model_name_or_path`。[SWE-bench datasets](https://github.com/SWE-bench/SWE-bench/blob/ec9181d65aca823e8fd8d07a61bdcd39914564ef/docs/guides/datasets.md)；[evaluation format](https://github.com/SWE-bench/SWE-bench/blob/ec9181d65aca823e8fd8d07a61bdcd39914564ef/docs/assets/evaluation.md)

可选接法：

1. **推荐：走 Harbor。** Harbor 已提供 `swe-bench/swe-bench-verified` dataset，可直接复用和 Terminal-Bench 相同的 LANTU/OpenCode adapter。[Harbor evals](https://github.com/laude-institute/harbor/blob/488af1b12b3b728b9364ab5e1bb663bd3e0ae643/docs/content/docs/run-jobs/run-evals.mdx)
2. **走 SWE-bench 官方 harness。** 自己准备每题的 base repo，让 agent 修改后导出 `git diff` 为 `model_patch`，再交给官方 `swebench eval verified`。官方 harness 负责 Docker 中应用 patch 和运行测试，但它只评 patch，不负责运行任意 CLI agent。[SWE-bench README](https://github.com/SWE-bench/SWE-bench/blob/ec9181d65aca823e8fd8d07a61bdcd39914564ef/README.md)；[evaluation format](https://github.com/SWE-bench/SWE-bench/blob/ec9181d65aca823e8fd8d07a61bdcd39914564ef/docs/assets/evaluation.md)

官方判定 `resolved` 的条件是：所有 `FAIL_TO_PASS` 测试转为通过，同时所有 `PASS_TO_PASS` 回归测试仍通过。[grading.py](https://github.com/SWE-bench/SWE-bench/blob/ec9181d65aca823e8fd8d07a61bdcd39914564ef/swebench/harness/grading.py)

## 推荐的公平实验配置

对 LANTU vs OpenCode，只改变 agent 框架，其余全部固定：

| 变量 | 固定方式 |
|---|---|
| benchmark | 固定 dataset 名称、版本、任务 ID 清单和 verifier commit |
| agent | 固定 LANTU commit、OpenCode commit、adapter commit |
| 模型 | 同一 provider、同一精确 model ID、同一 reasoning/temperature/max output 配置 |
| 环境 | 同一 Harbor 版本、容器 runtime、CPU/内存、网络策略和工作目录 |
| 预算 | 同一 agent timeout；尽量同一模型 token 上限；超限统一记失败 |
| 重复 | 每题至少 3 次，正式结果建议 5 次；每次从干净容器开始 |
| 判分 | benchmark verifier reward 为主；agent 自称完成不算成功 |
| 成本 | 从 provider 原始 usage 统一计算输入、缓存、输出 token 和美元成本；不要用各 CLI 不同口径的摘要 |
| 错误 | 超时、崩溃、安装和 verifier 错误单列；主成功率按预先声明的规则处理，不能事后丢掉 |

第一阶段不必直接跑满 500 题。可先固定 20 个 Terminal-Bench 2.0 任务和 20 个 SWE-bench Verified 任务做 adapter smoke；确认 oracle、安装、轨迹、usage 和错误计数都正确后，再扩展到完整集。

## 能否和现有公开分数直接比较

**可以放在同一张参考表中，但不能据此得出框架优劣。** 只有当 benchmark 版本、任务集合、agent/model 精确版本、系统提示、工具权限、超时、重复次数和错误处理规则一致时，才接近可比。Pi 项目的硬编码榜单恰好说明了问题：它把不同模型和不同 agent 混排，并未控制成本。

对外应分别报告：

- `LANTU + 固定模型` 与 `OpenCode + 同一模型` 的本次受控结果；
- 同 benchmark 官方榜单作为历史参考，并明确“非同配置”；
- success/reward、错误率、token、成本、端到端耗时和每次成功成本；
- 所有原始 job、trial、trajectory、verifier 输出与版本清单。

这样既能利用现成 benchmark 的可信题目和 grader，又不会把“模型差异”误写成“框架差异”。
