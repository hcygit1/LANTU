# 文档与测试记录

整理日期：2026-09-29。日期表示归档时间，各次测试的执行时间以原始记录为准。

## 分类索引

| 目录 | 内容 |
| --- | --- |
| [docs/design](docs/design/lantu-lens.md) | Lens 设计与流程说明 |
| [docs/handoff](docs/handoff/development-handoff-2026-09-08.md) | 开发交接文档 |
| [docs/research](docs/research/) | 上下文管理与 Tianshu 压缩机制研究 |
| [tests/cache](tests/cache/) | 多轮缓存测试的 JSON 数据与文本报告 |
| [tests/validation](tests/validation/) | 功能验证的 JSON 数据与文本报告 |
| [tests/ab](tests/ab/README.md) | FileLedger、工具结果处理、上下文压缩、窗口切换和 ZG 的 A/B 记录 |
| [tests/terminalbench](tests/terminalbench/) | Terminal-Bench 各次运行的配置、结果、日志和执行记录 |

## 数据说明

- 本次整理保留原始文件内容，共移动并校验 196 个文件；包含成功、失败和超时记录。
- 文件内容通过移动前后的 SHA256 校验，没有重新计算或修改测试指标。
- 报告和日志中的原始路径保留为历史执行信息；实际文件位置以本目录分类为准。
- 测试脚本继续使用原有输出目录 `bench/results/`，本目录保存此次归档快照。
- 运行时的 `*.lock` 文件保留在本地，不纳入版本管理；测试的 `lock.json` 配置记录保留。
- `workspace/` 和 `Tianshu-harness/` 不属于本次归档与提交内容。

## 原路径对应关系

| 原位置 | 归档位置 |
| --- | --- |
| `docs/design/lantu-lens.md` | `docs/design/lantu-lens.md` |
| `docs/development-handoff-2026-09-08.md` | `docs/handoff/development-handoff-2026-09-08.md` |
| `docs/research/context-as-environment.md` | `docs/research/context-as-environment.md` |
| `docs/research/tianshu-context-compaction.md` | `docs/research/tianshu-context-compaction.md` |
| `bench/results/lantu_live_cache.*` | `tests/cache/` |
| `bench/results/lantu_validation.*` | `tests/validation/` |
| `bench/results/ab/` | `tests/ab/` |
| `bench/results/terminalbench/` | `tests/terminalbench/` |
