# LANTU 学习资源

## Knowledge

- [LANTU README](README.md)
  项目的安装、配置、运行模式和测试命令。用于确认项目对外承诺的行为。
- [项目入口](lantu/__main__.py)
  `lantu` 命令的真实启动代码。用于追踪参数解析和运行模式分流。
- [Python argparse 官方文档](https://docs.python.org/3/library/argparse.html)
  Python 命令行参数解析的权威说明。用于理解 `build_parser()`。
- [Python asyncio 官方文档](https://docs.python.org/3/library/asyncio.html)
  Python 异步运行时的权威说明。用于理解 `asyncio.run()` 和异步 Agent 流程。
- [pytest 官方文档](https://docs.pytest.org/)
  项目测试框架的官方文档。用于编写和运行回归测试。

## Wisdom (Communities)

- [Python Discourse](https://discuss.python.org/)
  Python 官方社区。用于核实复杂异步、打包或语言行为问题。

## Gaps

- 项目缺少完整架构文档，课程将以入口、模块接口和测试为主要证据逐步补齐地图。
