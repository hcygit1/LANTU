# 将观测工具作为 LANTU Lens 纳入 Monorepo

原 CCWhat 不再作为通用 Coding Agent 分析器独立发展，而是更名为 LANTU Lens，并迁入 `tools/lens/`。它保留独立 Python 包和 Viewer，但与 LANTU 在同一 Git 仓库中共同修改、测试和发布，通过 Session Journal 契约连接。迁移只复制当前代码，不导入旧 Git 历史；原仓库保留为历史档案。迁移分两阶段：先完成迁入、改名和 Journal 接入并保留旧 Adapter，验证新链路后再删除通用 Agent Adapter 和无关配置。

迁移内容只包括产品代码、测试、运行所需资源、有用文档和依赖清单，例如 `ccwhat/`、`viewer/`、`assets/`、`tests/`、`docs/`、`pyproject.toml`、锁文件及前端包清单。旧仓库的 Git 数据、Agent 配置、设计过程目录、发布记录与脚本、本地缓存和生成数据不迁移。

原有的 `mitmproxy` HTTP/HTTPS 录制能力保留为 LANTU Lens 的可选“双证据模式”。它默认关闭，用于将模型原始请求和响应与 Session Journal 交叉核对，但不作为 Session 恢复或继续执行的事实来源。

HTTP 抓包数据与 Journal 分开存储。用户主动开启抓包后，数据与对应 Session 一起长期保留，并在用户确认删除该 Session 时同步永久删除。

LANTU 的默认安装包含 Lens 的 Journal Reader 和 Viewer，因此安装后可直接使用 `lantu lens`。`mitmproxy`、证书配置及双证据抓包能力作为 `lantu[capture]` 可选依赖提供；未安装该额外组件不影响 Journal 记录、恢复和查看。

`lantu lens` 在前台启动 Viewer，自动选择空闲端口，仅监听 `127.0.0.1` 并自动打开系统浏览器。用户可显式指定端口；`Ctrl+C` 或进程退出时关闭服务。第一版不提供局域网或远程访问。

`lantu --capture` 正常启动 Agent，同时在第一次模型请求前启动该 Session 的可选 HTTP 抓包，交互模式与 `-p` 模式使用相同语义。`lantu lens` 只查看已经保存的 Journal 和抓包数据，不负责启动或包装 Agent。

显式使用 `--capture` 时，LANTU 在启动 Agent 前检查抓包依赖、可用端口和 CA 证书。检查失败时直接退出并给出修复说明，不静默降级为无抓包运行；运行过程中已经完成转发但抓包写入失败时，仍按 Session Journal 设计标记证据不完整并继续执行。

LANTU Lens 的产品能力保留但分阶段接入：第一阶段完成 Session 查看、搜索和事件详情；第二阶段接入 Task 切分、Event Graph、Action Graph、证据定位和报告诊断；第三阶段接入请求回放、对比、人工校正和 Dataset 导出。阶段划分只控制迁移顺序，不删除这些 LANTU 相关能力。
