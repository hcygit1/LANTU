# Kimi CLI 发布与合并前测试调研

调研日期：2026-08-13

资料范围：只使用 MoonshotAI 官方 `kimi-cli` 仓库中的工作流、Makefile、测试目录和项目说明。

## 核心结论

Kimi CLI 把测试分成两层：

1. **合并前 CI** 检查代码是否可靠：静态检查、单元测试、E2E 测试、多 Python 版本兼容、跨平台构建和二进制启动冒烟测试。
2. **Tag 发布流水线** 检查制品是否可以发布：版本与依赖一致性、跨平台打包、macOS 签名与公证、产物完整性和发布。

公开仓库中没有证据表明 Terminal-Bench、SWE-bench 等 Coding Agent 能力评测是 PR 合并或 Tag 发布的自动门禁。仓库虽有 `tests_ai` 和 `make ai-test`，但当前公开的 `ci-kimi-cli.yml` 与 `release-kimi-cli.yml` 都没有调用 `make ai-test`。

这只能说明“公开自动工作流没有把能力 benchmark 设为门禁”，不能证明 Moonshot 内部没有另外运行未公开的评测。

## 合并前实际执行的检查

### 1. 代码质量与类型检查

`CI (kimi-cli)` 在 pull request 和 `main` push 时运行 `make check-kimi-cli`。该 Makefile 目标执行：

- `ruff check`
- `ruff format --check`
- `pyright`
- `ty check`，但通过 `|| true` 设为非阻塞

来源：

- [CI (kimi-cli) workflow](https://github.com/MoonshotAI/kimi-cli/blob/main/.github/workflows/ci-kimi-cli.yml)
- [Kimi CLI Makefile](https://github.com/MoonshotAI/kimi-cli/blob/main/Makefile)

### 2. 单元测试与 E2E 测试

CI 在 Python 3.12、3.13、3.14 三个版本上运行 `make test-kimi-cli`。该 Makefile 目标依次执行：

```bash
uv run pytest tests -vv
uv run pytest tests_e2e -vv
```

`tests_e2e` 覆盖 MCP CLI、Wire 协议、授权、配置、错误、Prompt、Session、Skills 与 MCP 等集成行为。公开的“真实 LLM”测试目前标记为 `skip`，也就是说常规 CI 不会真的请求模型验证 Agent 完成编码任务。

来源：

- [CI (kimi-cli) workflow](https://github.com/MoonshotAI/kimi-cli/blob/main/.github/workflows/ci-kimi-cli.yml)
- [Kimi CLI Makefile](https://github.com/MoonshotAI/kimi-cli/blob/main/Makefile)
- [`tests_e2e` 目录](https://github.com/MoonshotAI/kimi-cli/tree/main/tests_e2e)
- [被跳过的真实 LLM E2E 测试](https://github.com/MoonshotAI/kimi-cli/blob/main/tests_e2e/test_wire_real_llm.py)

### 3. 跨平台构建与启动冒烟测试

合并前 CI 会构建 Linux x86_64、Linux ARM64、macOS ARM64、Windows x86_64 的独立二进制，然后执行 `kimi --help`，检查输出是否包含 `Kimi`。这验证“二进制能构建并启动”，不验证 Agent 能完成任务。

来源：[CI (kimi-cli) workflow](https://github.com/MoonshotAI/kimi-cli/blob/main/.github/workflows/ci-kimi-cli.yml)

### 4. 发布版本预检查

如果 PR 修改了版本，CI 还会检查：

- 内部依赖版本是否匹配；
- `kimi-cli` 与 `kimi-code` 包版本是否对齐；
- `kimi-code` 对 `kimi-cli` 的依赖是否固定到相同版本。

来源：[CI (kimi-cli) workflow](https://github.com/MoonshotAI/kimi-cli/blob/main/.github/workflows/ci-kimi-cli.yml)

## Tag 发布时实际执行的验证

Tag 发布工作流由数字版本标签触发，主要执行：

- 检查 Git tag 与 `pyproject.toml`、`packages/kimi-code/pyproject.toml` 版本一致；
- 检查内部依赖版本；
- 在 Linux x86_64/ARM64、macOS ARM64/Intel、Windows x86_64/ARM64 上构建 onefile 和 onedir 产物；
- 严格检查 Web 资源版本与发布标签一致；
- 对 macOS 产物签名、验证签名并提交 Apple notarization；
- 打包 ZIP 或 `tar.gz`，生成 SHA-256 校验文件；
- 创建 GitHub Release 并发布 PyPI 包。

发布工作流中没有 `pytest`、`make test` 或 `make ai-test`。因此 Tag 阶段本身不重新运行完整测试，而是依赖代码此前通过 PR/main CI，再完成发布制品验证。

来源：[Release (kimi-cli) workflow](https://github.com/MoonshotAI/kimi-cli/blob/main/.github/workflows/release-kimi-cli.yml)

## AI 测试与公开能力评测的位置

仓库提供 `make ai-test`，它运行 `tests_ai/scripts/run.py tests_ai`。`tests_ai` 目录包含 `accuracy_smoke` 和若干自然语言测试说明。这说明项目维护者确实保留了面向 AI 行为的测试入口。

但当前公开工作流的事实是：

- `tests_ai/**` 的变更会触发普通 CI；
- 普通 CI 触发后仍只运行 `make check-kimi-cli`、`make test-kimi-cli` 和构建冒烟测试；
- `make test-kimi-cli` 只执行 `tests` 与 `tests_e2e`；
- `make ai-test` 没有出现在普通 CI 或发布 workflow 中。

所以，`tests_ai` 目前更像手动或另行执行的 Agent 行为测试，而不是公开的强制发布门禁。

来源：

- [Kimi CLI Makefile](https://github.com/MoonshotAI/kimi-cli/blob/main/Makefile)
- [`tests_ai` 目录](https://github.com/MoonshotAI/kimi-cli/tree/main/tests_ai)
- [CI (kimi-cli) workflow](https://github.com/MoonshotAI/kimi-cli/blob/main/.github/workflows/ci-kimi-cli.yml)
- [Release (kimi-cli) workflow](https://github.com/MoonshotAI/kimi-cli/blob/main/.github/workflows/release-kimi-cli.yml)

## 对 LANTU 的直接启示

Kimi CLI 的公开做法不是“每次发布都跑大型 Agent benchmark”，而是：

- 用快速、稳定、可重复的单元/E2E/构建测试阻止软件回归；
- 把需要真实模型、耗时且有随机性的 Agent 能力评测单独运行；
- 发布时重点保证版本和最终制品正确。

因此 LANTU 也适合拆成两套：日常 CI 保证框架不坏，Harbor 定期评估真实任务成功率、轨迹和成本。Harbor 的结果适合做版本能力报告，不必卡住每一次代码提交或发布。
