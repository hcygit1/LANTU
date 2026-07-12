# Lantu

Lantu 是一个教学版终端 AI 编程 Agent 核心实现，重点演示 Agent 主循环、工具调用、权限控制、上下文压缩、MCP、Skill、子 Agent 与 worktree 隔离等机制。

## 技术栈

- Python 3.11+
- Textual
- Anthropic / OpenAI / OpenAI-compatible API
- MCP
- pytest

## 快速开始

```bash
uv sync
cp .lantu/config.yaml.example .lantu/config.yaml
uv run lantu
```

非交互执行：

```bash
uv run lantu -p "介绍当前项目"
```

运行测试：

```bash
uv run pytest
```
