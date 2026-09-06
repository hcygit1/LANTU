# Conversation 上下文管理进度

更新时间：2026-09-07

## 已完成

- 在现有 Lens 中增加请求组装层、Client 载荷层和逐消息指纹。
- 统一记录模型请求生命周期、`model_call_id`、usage 和缓存诊断。
- Lens CLI 和 Web Viewer 支持缓存报告、命中率、变化原因、公共消息数和首个差异位置。
- `Message` 增加 `Frozen`、`Appendix`、`Ephemeral` 生命周期元数据。
- 用户/Assistant/工具调用结果、环境初始化和长期记忆默认归类为 Frozen。
- Plan、Deferred Tools、MCP、Hook prompt、运行时记忆召回归类为 Appendix。
- Hook notification 使用 Ephemeral，并在模型请求结束后清理。
- 后台任务和团队 mailbox 结果显式归类为 Frozen。
- 接入 `git_status` 和 `task_progress` keyed Appendix：相同状态不重复追加，变化状态追加新版本。
- 分类字段只用于 Lens 观测，不参与请求 digest，不改变历史消息顺序。

## 当前不做

- 不修改压缩算法和压缩边界。
- 不重写 File Ledger 和工具结果落盘机制。
- 不对固定工具 Schema 做逐工具 Hash。
- 不创建独立观测系统。

## 待完成

1. 在 Lens 中展示每条消息的 `kind` 和 `appendix_key`，并统计缓存断点对应的内容类别。
2. 将当前内部记忆 selector 改造成“代码预筛选 + 主模型按需调用 `memory_search`”。工具调用和结果进入 Frozen 历史。
3. 完善 Appendix Delta 的稳定渲染和状态量化。
4. 基于真实缓存数据进行 A/B 对比后，再优化压缩 watermark 和重建边界。
5. 评估 CVM 和信息素机制是否有足够数据支撑引入。

## 验证

```text
分类与运行时 Appendix 回归：通过
完整测试：981 passed，4 failed
```

剩余失败来自未修改的 `tests/fixtures/run_inline_fake.py`，其中 `FakeSession` 缺少 `start_turn`。

## 推进顺序

```text
Lens 分类观测
→ memory_search 混合召回
→ Appendix Delta 稳定化
→ A/B 缓存实验
→ 压缩优化
→ CVM / 信息素评估
```
