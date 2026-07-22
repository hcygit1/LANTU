# Lantu

Lantu 是一个教学版终端 AI 编程 Agent 核心实现，重点演示 Agent 主循环、工具调用、权限控制、上下文压缩、MCP、Skill、子 Agent 与 worktree 隔离等机制。

## 技术栈

- Python 3.11+
- Rich / prompt_toolkit（默认内联终端前端）
- Textual（legacy 全屏前端）
- Anthropic / OpenAI / OpenAI-compatible API
- MCP
- pytest

## 配置

推荐把通用模型配置放在 `~/.lantu/config.yaml`。例如 DeepSeek：

```yaml
providers:
  - name: deepseek
    protocol: openai-compat
    base_url: https://api.deepseek.com
    model: deepseek-chat
    api_key: ${DEEPSEEK_API_KEY}
```

启动前设置 API Key：

```bash
export DEEPSEEK_API_KEY="your-api-key"
```

如需为单个项目调整配置，可创建项目目录下的 `.lantu/config.yaml`。每个配置文件都必须是完整、合法的配置并包含 `providers`；项目层的 `providers` 会整体替换全局 `providers`，其他支持项按现有合并规则叠加。不要求复制 `.lantu/config.yaml.example`。

## 安装与运行

在仓库内安装依赖并启动默认的内联终端前端：

```bash
uv sync
uv run lantu
```

旧 Textual 全屏前端仍可通过 `--tui` 使用：

```bash
uv run lantu --tui
```

也可以在仓库根目录把 Lantu 以可编辑方式安装为全局工具：

```bash
uv tool install --editable .
```

从其他目录安装时，也可以传入通用仓库路径，例如 `uv tool install --editable /path/to/lantu`。安装后可在任意项目目录执行 `lantu`。执行命令时所在的目录就是 Agent 的工作目录，项目级 `.lantu/config.yaml` 也从该目录读取。

## 使用方式

内联模式支持 `/exit` 和 `/quit` 退出。等待输入时第一次按 `Ctrl+C` 会显示退出提示，再按一次退出；生成过程中按 `Ctrl+C` 只取消当前生成，不退出程序。

非交互执行单个提示：

```bash
uv run lantu -p "介绍当前项目"
```

启动远程模式：

```bash
uv run lantu --remote
```

远程服务监听 `0.0.0.0:18888`，可通过 `http://localhost:18888` 访问浏览器界面。

## 测试

```bash
uv run pytest
```
