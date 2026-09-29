# LANTU zvec-grep 本地索引接入 Spec

日期：2026-09-09

状态：Draft，等待实现

## 1. 背景

LANTU 当前依靠以下能力理解和检索仓库：

- `RepoMap` 在启用时扫描仓库、提取符号，并将最多约 4000 token 的完整地图附加到稳定 System Prompt。
- `Grep` 每次查询都由 Python 遍历文件、读取全文并逐行执行正则。
- `Glob` 负责按路径发现文件。
- `ReadFile` 在目标文件已知后读取当前磁盘内容和具体行段。

这套实现存在三个主要问题：

1. RepoMap 增加固定上下文成本，而且地图会截断，不保证覆盖目标符号。
2. Grep 重复扫描和读取仓库，缺少 ripgrep 的性能及完整参数能力。
3. 仓库概览与查询是两套独立扫描逻辑，没有共享持久化索引。

引入 zvec-grep 后，仓库索引由本地 zvec-grep MCP Server 统一提供。LANTU 不再把完整仓库地图放入 System Prompt，而是保留短小、稳定的检索说明，按需查询索引并用 `ReadFile` 验证当前代码。

## 2. 目标

- 使用 zvec-grep 的本地持久化索引完成语义、词法和跨文件检索。
- 使用 `zvec_grep_rg` 作为默认精确搜索后端。
- MCP Server 不可用或工具调用失败时，自动降级到 LANTU 现有 `Grep`。
- 移除完整 RepoMap 的 System Prompt 注入和启动时仓库扫描。
- System Prompt 仅保留稳定、简短的搜索策略说明。
- 所有需要精确代码证据或准备修改的场景，最终使用 `ReadFile` 读取当前文件和行段。
- 索引创建、更新、重建和状态查询沿用 zvec-grep 原生 MCP/CLI 能力；LANTU 本阶段不新增索引管理命令。
- 索引结果必须携带并保留 freshness 信息，不能把可能过期的片段当作当前代码事实。

## 3. 非目标

- 本阶段不使用 zvec-grep 替换 `ReadFile`。
- 本阶段不移除 `Glob`；简单路径查找仍可使用它。
- 本阶段不把用户级或项目级记忆接入 zvec-grep。
- 本阶段不修改 `memory_search` 和自动记忆召回。
- 本阶段不同时维护 Python RepoMap、Tianshu SemanticIndex 和 zvec-grep 三套代码索引。
- 本阶段不允许模型自动删除索引。
- 本阶段不保证语义检索列出一个符号或字符串的全部出现位置；穷举需求必须走 rg。

## 4. 决策

### 4.1 唯一仓库索引

zvec-grep 本地 MCP Server 是 Python LANTU 主运行时唯一的持久化仓库索引。

索引默认位于：

```text
<project-root>/.zvec-grep/
```

`.zvec-grep/` 必须加入仓库 `.gitignore`。索引属于本地派生数据，不写入 Session Journal，也不提交 Git。

### 4.2 工具职责

| 工具 | 职责 | 是否依赖索引 |
| --- | --- | --- |
| `CodeSearch` | LANTU 暴露给模型的统一搜索入口 | 视查询模式而定 |
| `zvec_grep_search` | 语义、混合、词法排名、跨文件定位 | 是 |
| `zvec_grep_rg` | 精确字符串、正则、穷举出现位置 | 否 |
| 现有 `Grep` | MCP/zg 不可用时的精确搜索降级 | 否 |
| `Glob` | 简单文件名和路径发现 | 否 |
| `ReadFile` | 读取当前磁盘上的权威代码内容 | 否 |

模型默认只看到 `CodeSearch`，不同时暴露三个含义重叠的搜索工具。底层 MCP 管理工具不应进入常规模型工具列表。

### 4.3 查询路由

`CodeSearch` 接口建议为：

```json
{
  "query": "authentication flow",
  "mode": "exact",
  "path": ".",
  "include": ["lantu/**"],
  "limit": 10,
  "freshness": "eventual"
}
```

`mode` 支持：

- `exact`：调用 `zvec_grep_rg`，失败后降级到现有 `Grep`。默认值。
- `semantic`：调用 `zvec_grep_search` 的混合或向量检索。

`mode` 由模型给出，`CodeSearch` 不按查询特征猜测后端。两个模式的判据随工具描述一起交给模型：

- 已知的词、符号、文件名、源码片段、正则能够回答问题：`exact`，结果是穷举的。
- 架构、调用链、依赖关系、生命周期、数据或控制流、设计原因、跨文件比较，精确查找单独答不出：`semantic`。

不应依赖模型自行记住降级步骤。降级必须由 `CodeSearch` 内部执行。

### 4.4 精确搜索降级

正常路径：

```text
CodeSearch(exact)
  -> zvec_grep_rg
  -> 返回压缩后的穷举结果
```

降级路径：

```text
CodeSearch(exact)
  -> MCP 未连接、超时、工具不存在或调用失败
  -> 现有 Python Grep
  -> 返回结果并标注 backend=python_grep、degraded=true
```

以下情况触发降级：

- zvec-grep MCP Server 启动或连接失败。
- Server 未暴露 `zvec_grep_rg`，例如误用默认 `agent` toolset。
- MCP 调用超时或返回错误。
- 返回内容无法解析为有效搜索结果。

索引过期不触发精确搜索降级，因为 `zvec_grep_rg` 本身不依赖索引。

现有 `Grep` 在本阶段保留实现和测试，但改为内部 fallback，不作为主要模型工具。

### 4.5 语义搜索失败行为

`zvec_grep_search` 失败时不能假装 Python `Grep` 具备语义检索能力。

- 查询中存在可靠词法锚点时，可用这些锚点降级到现有 `Grep`。
- 没有可靠锚点时，返回明确的 `semantic_search_unavailable`，提示模型改用 Glob、ReadFile 或构造精确查询。
- 降级结果必须标注没有完成语义等价检索。

### 4.6 搜索不是最终代码证据

zvec-grep 返回的是排名后的有限片段，受 `limit`、分块、MCP 结果大小和上下文预算限制。它用于定位，不代表完整文件，也不保证索引绝对新鲜。

以下场景必须继续调用 `ReadFile`：

- 用户询问具体实现细节，需要引用准确代码。
- 准备修改文件。
- 搜索结果标记为 `possibly_stale`。
- 需要搜索片段之外的上下文。
- 需要确认具体行号、函数边界或当前文件版本。

`ReadFile` 读取当前磁盘内容，其结果继续进入 File Ledger。文件修改和可见范围判断仍以 File Ledger 为准，不能使用索引内容代替。

## 5. System Prompt 变化

### 修改前

启用 `context.repo_map.enabled` 时，`Agent._get_system_prompt()` 将以下内容追加到稳定 System Prompt：

```text
## Repository Map
This is a compact, possibly incomplete symbol index...

<最多约 4000 token 的符号列表>
```

仓库变化后需要刷新 RepoMap，并重建稳定 System Prompt。

### 修改后

删除完整符号列表，只保留固定说明：

```text
## Repository Search
This workspace uses a local persistent code index. Use CodeSearch for repository
search. Use exact mode when a known word, symbol, filename, or regex can answer; use
semantic mode for architecture, call chains, dependencies, or cross-file questions
that exact lookup alone cannot answer. Search results are bounded and may be stale;
use ReadFile to inspect current source before making claims or edits.
```

要求：

- 文本必须固定且短小，目标不超过 100 token。
- 文本不能包含实时索引状态、文件数、更新时间或符号列表，避免破坏稳定前缀缓存。
- MCP Server 是否在线通过启动消息或 `/index status` 展示，不写进 System Prompt。

## 6. 索引生命周期

### 6.1 Server 模式

LANTU 连接本地 Streamable HTTP MCP：

```yaml
mcp_servers:
  - name: zvec_grep
    url: http://127.0.0.1:7999/mcp
```

Server 必须以 `full` MCP toolset 启动，才能提供：

- `zvec_grep_search`
- `zvec_grep_rg`
- `zvec_grep_index`
- `zvec_grep_index_status`
- `zvec_grep_server_status`

配置名称使用 `zvec_grep` 而不是 `zvec-grep`，避免生成的 LANTU MCP 工具名包含连字符。

LANTU 不负责静默安装 npm 包，也不在每次 Runtime 启动时重新启动 Server。启动时只连接已配置的本地 Server，并在失败时给出一次非阻塞警告。

### 6.2 索引管理边界

本阶段不新增 `/index` 命令。索引建立、增量更新、重建、状态查询和删除均由
zvec-grep 原生 CLI 或其 full MCP toolset 负责，LANTU 只通过现有 MCP 配置连接和调用。

LANTU 不会在 Runtime 启动时静默创建、重建或删除索引，也不把索引管理工具加入模型常规工具列表。
用户可以直接使用 zvec-grep 的原生命令或管理 MCP 工具完成索引生命周期操作。

索引管理工具不暴露给模型。

### 6.3 自动更新

正常搜索使用：

- 默认 `freshness=eventual`。
- 默认 `autoUpdate=true`，允许 Server 在后台调度增量更新。
- 搜索响应必须保留 Server 返回的 freshness。
- 修改前仍必须通过 `ReadFile` 验证，不等待索引更新作为前置条件。

当用户明确要求最新索引结果时，可使用 `freshness=wait_for_fresh`，但必须设置超时，超时后返回明确状态，不能无限阻塞 Turn。

### 6.4 Worktree

每个 worktree 使用自身根目录和自身 `.zvec-grep/` 索引。切换 worktree 时：

1. 更新 `CodeSearch` 的绝对 root。
2. 不复制主工作区索引目录。
3. 若目标 worktree 尚无索引，语义搜索返回缺少索引状态；精确搜索仍可走 `zvec_grep_rg`。
4. 用户可在目标 worktree 显式执行 `/index build`。

## 7. 配置变更

建议新增：

```yaml
context:
  repository_search:
    backend: zvec_grep
    mcp_server: zvec_grep
    semantic_timeout_seconds: 20
    exact_timeout_seconds: 10
    default_limit: 10
    auto_update: true
    fallback_to_python_grep: true
```

删除或废弃：

```yaml
context:
  repo_map:
    enabled: true
    max_tokens: 4000
```

兼容策略：

- 发现旧 `context.repo_map` 配置时给出一次弃用警告。
- 过渡版本忽略 `repo_map.enabled`，不再执行扫描和 Prompt 注入。
- 下一次破坏性配置版本再删除解析支持。

## 8. 代码修改范围

### 8.1 新增统一搜索工具

建议新增：

```text
lantu/tools/code_search.py
```

职责：

- 定义稳定的 `CodeSearch` 参数和返回元数据。
- 实现 `exact/semantic` 路由。
- 调用指定 MCP Server 的原始工具名，而不是依赖模型可见包装器名称。
- 精确查询失败时调用现有 `Grep`。
- 统一规范化 root、glob、limit、超时、错误和 freshness。

返回元数据至少包括：

```json
{
  "backend": "zvec_grep_search",
  "mode": "semantic",
  "degraded": false,
  "freshness": "fresh",
  "root": "D:/code/LANTU"
}
```

### 8.2 MCP Manager 增加内部调用接口

修改：

```text
lantu/mcp/manager.py
lantu/mcp/client.py
```

当前 MCP 工具主要通过 `MCPToolWrapper` 注册到模型工具表。需要新增内部接口：

```python
await manager.call_tool(server_name, tool_name, arguments)
```

用途：

- `CodeSearch` 调用 `zvec_grep_search` 和 `zvec_grep_rg`。
- `/index` 命令调用索引管理工具。
- 管理工具不必暴露给模型。
- 复用现有重连和错误处理逻辑。

### 8.3 工具注册

修改：

```text
lantu/runtime/builder.py
lantu/tools/__init__.py
lantu/agents/tool_filter.py
```

修改前：

- 注册 `Grep` 供模型直接调用。
- MCP 工具统一包装并默认标记为 deferred。

修改后：

- 注册 `CodeSearch` 为主要模型工具。
- `Grep` 实例仍存在，但标记为内部 fallback，或从公共 registry 分离为依赖对象。
- zvec-grep 的 search/rg/index/status 原始 MCP 工具不重复暴露给模型。
- 其他 MCP Server 的注册行为保持不变。

### 8.4 删除 RepoMap 运行链路

修改：

```text
lantu/agent.py
lantu/runtime/builder.py
lantu/remote.py
lantu/__main__.py
lantu/context/repo_map.py
lantu/commands/handlers/repo_map.py
```

修改前：

- Runtime 根据配置构建 RepoMap。
- Agent 持有 RepoMap。
- `_get_system_prompt()` 注入完整地图。
- `/repo-map refresh` 触发全仓重扫。

修改后：

- Runtime 不再构建 RepoMap。
- Agent 不再持有 RepoMap。
- `_get_system_prompt()` 只注入固定的 Repository Search 说明。
- 删除 `/repo-map` 命令，索引操作统一进入 `/index`。
- 删除 RepoMap 实现或先保留一个版本但完全断开运行引用；最终应删除，避免形成第二套索引。

### 8.5 新增索引命令

建议新增：

```text
lantu/commands/handlers/index.py
```

并在命令注册表中注册。命令只通过指定的 `zvec_grep` MCP Server 执行，不直接拼接 shell 命令。

### 8.6 配置和文档

修改：

```text
lantu/config.py
lantu/validator.py
.lantu/config.yaml.example
README.md
.gitignore
```

补充 Node.js 22+、zvec-grep 安装、本地 Server 启动、full toolset、首次索引和隐私说明。

## 9. 前后差异

| 场景 | 修改前 | 修改后 |
| --- | --- | --- |
| Runtime 启动 | 可选全仓扫描构建 RepoMap | 只连接本地 MCP，不扫描仓库 |
| System Prompt | 可携带最多约 4000 token 符号地图 | 仅携带不超过 100 token 的固定检索说明 |
| 概念搜索 | 多次 Grep/ReadFile 猜位置 | zvec-grep 混合索引定位 |
| 精确搜索 | Python 遍历并逐文件读取 | 优先 managed ripgrep，失败时 Python Grep |
| 索引存储 | RepoMap 仅在内存 | `.zvec-grep/` 持久化 |
| 更新方式 | RepoMap 全量 refresh | zvec-grep 增量更新/后台 autoUpdate |
| 搜索结果 | 文本匹配行 | 排名片段或穷举匹配，带 freshness |
| 修改前证据 | ReadFile | 仍然必须 ReadFile |
| Worktree | 切换时重建 RepoMap | 每个 worktree 使用独立索引 root |

## 10. 测试计划

### 10.1 单元测试

- 默认 `exact`，省略 `mode` 时走精确搜索。
- `semantic` 正确映射到 `zvec_grep_search` 参数。
- `exact` 正确构造安全的 rg 参数，不通过 shell 执行。
- MCP 连接失败、超时、工具缺失和错误响应触发精确搜索降级。
- 语义查询无可靠锚点时不会伪装成等价 Grep 结果。
- 返回元数据正确标记 backend、degraded 和 freshness。
- root 始终为当前工作目录或 worktree 的规范化绝对路径。
- 模型工具列表不暴露索引删除/重建工具。
- RepoMap 内容不再进入 System Prompt。
- Repository Search 固定说明保持字节级稳定。

### 10.2 集成测试

- 使用假的 Streamable HTTP MCP Server 验证连接、列举工具、搜索和重连。
- 有索引时完成语义搜索并通过 `ReadFile` 获取当前代码。
- 无索引时语义搜索返回明确状态，精确搜索仍成功。
- Server 离线时 exact 自动降级，Turn 不崩溃。
- 修改文件后，搜索可以返回 possibly_stale，但 ReadFile 返回最新内容。
- 切换 worktree 后 MCP 参数使用新 root，不复用旧 root。
- `/index build/update/rebuild/status` 调用正确的 MCP 工具。

### 10.3 性能验收

使用现有 `bench/lantu_validate.py` 扩充 A/B 场景，对比：

- Runtime 启动耗时。
- 首轮 System Prompt token。
- 完成仓库定位任务的工具调用数。
- 搜索总耗时。
- 读取和输入 token 数。
- 任务成功率。
- MCP 不可用时的降级成功率。

最低验收标准：

- RepoMap 固定 Prompt 开销归零，仅保留不超过 100 token 的说明。
- 索引已建立时，语义定位任务不发生 Python 全仓扫描。
- exact 查询在 zg 可用时不调用 Python Grep。
- zg 不可用时 exact 查询仍能完成。
- 任务成功率不低于当前基线。

## 11. 实施阶段

### 阶段一：MCP 能力

- 配置并验证 zvec-grep full MCP toolset。
- 为 MCP Manager 增加内部工具调用接口。
- 更新示例配置和 `.gitignore`。

完成条件：LANTU 可以连接 zvec-grep 并调用搜索工具；索引管理继续由 zvec-grep 原生能力负责。

### 阶段二：统一 CodeSearch

- 实现 `CodeSearch`。
- 接入 `zvec_grep_search` 和 `zvec_grep_rg`。
- 接入现有 Grep fallback。
- 统一错误、超时、结果元数据和 freshness。

完成条件：正常、降级和无索引三条路径均有自动化测试。

### 阶段三：移除 RepoMap

- 删除 RepoMap 构建和 System Prompt 注入。
- 加入固定 Repository Search 说明。
- 删除 `/repo-map`；索引管理继续使用 zvec-grep 原生 CLI/MCP。
- 增加旧配置弃用提示。

完成条件：启动不再扫描仓库，System Prompt 不含符号地图。

### 阶段四：评测和清理

- 扩充 benchmark。
- 验证 Windows、本地模式、远程 UI 和 worktree。
- 根据评测删除无引用 RepoMap 代码和过渡兼容逻辑。

完成条件：满足性能验收，且不存在两套活跃的 Python 仓库索引。

## 12. 风险与约束

- zvec-grep 当前要求 Node.js 22+，这是新增的运行环境依赖。
- 使用本地 embedding 时会产生首次模型下载、CPU、内存和磁盘开销。
- 使用远程 embedding 会传输仓库内容，必须由用户明确配置和授权；LANTU 默认不启用。
- `zvec_grep_search` 是排名检索，不保证穷举。
- `zvec_grep_rg` 不依赖索引，但依赖本地 MCP Server；因此仍需 Python Grep fallback。
- MCP 工具输出仍会受 LANTU 工具结果和上下文长度限制。
- 索引可能暂时过期，任何代码修改都必须基于 ReadFile 的当前内容。
- 多 Runtime 可以共享同一 Server 和同一 root 索引，但索引更新并发由 zvec-grep 负责；LANTU 不自行实现文件锁。

## 13. 开放问题

实现前需要最终确认：

1. `CodeSearch` 是否完全替代模型可见的 `Grep`，还是保留一个显式高级入口。本文默认完全替代。
2. zvec-grep Server 由用户独立启动，还是由 LANTU 提供可选的进程托管。本文默认用户独立启动。
3. 旧 `context.repo_map` 配置保留一个弃用周期还是直接删除。本文默认保留一个弃用周期。
4. 本地 embedding 模型由安装文档推荐但不强制。建议代码仓库优先使用 zvec-grep 官方代码模型默认值。

## 14. 最终验收场景

用户提问：“认证权限是在什么地方校验的？”

预期执行：

1. `CodeSearch(mode=semantic)`，模型判定为语义/跨文件问题。
2. 调用 `zvec_grep_search`，获得相关文件、符号、行段和 freshness。
3. 模型根据结果调用 `ReadFile` 读取候选代码的当前行段。
4. 回答以 ReadFile 内容为事实依据。

用户提问：“列出 `build_repo_map` 的所有调用位置。”

预期执行：

1. `CodeSearch(mode=exact)`，模型判定为精确穷举问题。
2. 调用 `zvec_grep_rg`。
3. 若 MCP 失败，自动调用 Python Grep。
4. 需要解释调用逻辑时，再用 `ReadFile` 读取对应位置。

这两个场景都不依赖完整 RepoMap，也不会把仓库全文或完整索引加载进模型上下文。
