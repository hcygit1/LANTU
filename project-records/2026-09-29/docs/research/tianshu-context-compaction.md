# Tianshu-harness 上下文压缩源码调研

调研范围：`Tianshu-harness` 当前工作树源码；未采用 README、博客或推测。

## 结论

天枢不是一次性把整段 Conversation 换成摘要，而是按压力分三层处理：

```text
请求达到一定体积
├─ 请求副本折叠：只改本次发给模型的旧工具结果，不改 Session 历史
├─ 部分压缩：保留开头 2 条锚点和最后 60 条消息，旧区换成摘要
└─ 检查点/新窗口：保留开头 2 条锚点，其他历史换成摘要 + 可回读归档引用
```

因此，“尽量保持前缀缓存”不是不压缩，而是先延迟会改写历史的动作；确实要改时，保留最早的缓存锚点，并把被丢弃的原文存为可回读 artifact。

## 1. 触发条件

### 1.1 请求副本折叠（不改持久 Conversation）

- 仅当上下文窗口至少 200K、非 side-path 请求、估算占用至少 50% 时运行。
- 每跨过一个新的 50K token 档位，才推进一次折叠边界；边界为“至少 8 个用户 Turn 之前”的消息末尾。
- 50%～85% 只删旧 Assistant 的 `reasoning_content`、折叠被后续同目标读取/搜索替代的工具结果；85% 以上才将旧工具结果折为语义摘要。

证据：`src/prompt/engine.ts` 的 `COLLAPSE_FLOOR_FILL_RATIO`、`FULL_COLLAPSE_FILL_RATIO`、`buildOaiRequest()`、`computeCollapseBoundary()`、`requestTimeCollapse()`。

### 1.2 自动持久压缩

- 由 `decideCompactAction()` 根据 `estimatedTokens / contextWindow`、服务商缓存类型、最近缓存命中率和精度上限选择动作。
- 对 1M 窗口且“按 Token 计费 + 精确前缀缓存”的模型，LLM 部分压缩/全量压缩阈值上移到 75% / 85%；硬上限仍是 95%。缓存热时 `CacheAdvisor` 还能推迟非强制压缩。
- 1M 窗口到 86% 会先尝试 Session split；95% 触发强制 checkpoint，避免请求超过模型窗口。

证据：`src/context/compact-policy.ts` 的 `decideCompactAction()`；`src/context/compact-policy.ts` 的 `CACHE_PRESERVING_LLM_ACTION_RATIOS`；`src/agent/compaction-controller.ts` 的 `maybeCompact()`、`trySessionSplit()`、`enforceContextCeiling()`。

## 2. 压缩什么，保留什么

### 请求副本折叠

- 只处理折叠水位线之前的 Assistant reasoning 和 Tool result。
- 最近至少 8 个用户 Turn 不动；真正的 `SessionContext` 消息数组也不动。
- 同一工具对同一目标的旧 `grep/search/read_file` 结果会被替换为“已被后续读取替代”；85% 以上，旧工具结果按工具类型变成简短摘要。

证据：`src/prompt/engine.ts` 的 `requestTimeCollapse()`；`src/compact/context-collapse.ts` 的 `collapseToolResult()`。

### 部分压缩

消息分段为：

```text
前 2 条消息（cache anchor） + old zone（生成摘要） + 最近 60 条（原样保留）
```

- 切点会后退到完整的“assistant tool_calls + 对应 tool results”组边界，避免产生 OpenAI API 不接受的孤儿工具调用。
- 最近区中，最近 10 个“历史归档回读”保持原文；更早的回读再次折成一行指针。

证据：`src/compact/constants.ts` 的 `CACHE_ANCHOR_MESSAGES`；`src/agent/compaction-controller.ts` 的 `findSafeSplitPoint()`、`tryPartialCompact()`、`foldAgedRecallBlocks()`。

### 全量 checkpoint / Session split

```text
前 2 条消息（cache anchor） + <compact-summary> 或 <session-handoff> + task anchor
```

- 除前 2 条之外的旧历史会被归档成 `compact-history artifact`；摘要尾部放 artifact 的 `read_section` 引用。
- 如果 LLM 摘要失败或不覆盖关键状态，使用从轨迹确定性提取的 handoff 作为后备。

证据：`src/agent/compaction-controller.ts` 的 `replaceWithCheckpoint()`、`archiveDiscardedHistory()`、`buildHandoffFromState()`、`summaryCoversState()`。

## 3. 摘要提示词与摘要注入方式

部分压缩提示词要求模型保留：用户意图链、全部写文件动作、技术决策及原因、文件与变更、错误与修复；丢弃工具输出细节、探索过程和重复状态。全量压缩另加当前状态、待办和下一步。两者都要求仅输出 Markdown 摘要且有字符预算。

第一次摘要直接替换 old zone；多次压缩时若 old zone 已含 `<compact-summary>`、`<partial-compact-summary>`、`<session-handoff>` 或 `<checkpoint-resume>`，会额外要求“无损保留旧摘要所有信息，再合并新消息”，而不是重写后丢掉早期信息。

- 部分压缩：插入一个 assistant 消息 `<partial-compact-summary ...>`，保留 recent zone，最后再追加权威 task anchor。
- checkpoint：插入一个 user 消息 `<compact-summary ...>` / `<session-handoff ...>`，随后追加 task anchor。
- 每次真实历史替换后，调用 `resetAppendixBaseline()`，使动态 appendix 在新历史上重建基线。

证据：`src/agent/compaction-controller.ts` 的 `mergeClause()`、`tryPartialCompact()`、`llmCompact()`、`replaceWithCheckpoint()`。

## 4. 前缀稳定性与多次压缩

1. 请求副本折叠以**水位线**而不是滑动窗口确定边界；同一个 token 档内请求字节不继续变化，避免每一轮都破坏前缀。
2. 真实历史改写前，先用 reclaim gate 计算回收是否值得缓存重建；不值得则不提交。
3. 真实压缩保留最早 2 条缓存锚点，但摘要是新消息，所以压缩后的第一次主请求仍会在摘要处产生一次缓存重建；之后只要继续追加，新的前缀可再次变热。
4. 原文不会只剩摘要：old zone/丢弃区先归档，摘要保存回读引用；归档失败时，对需要截断的工具结果采取 fail-open，保留原文而不是不可恢复地删除。
5. 多次压缩通过“旧摘要合并提示 + artifact 回读 + task anchor”降低摘要越压越丢信息的风险，但源码没有证明它能完全无损，仍属于有后备的有损压缩。

证据：`src/prompt/engine.ts` 的 `collapseWatermark`、`requestTimeCollapse()`；`src/agent/compaction-controller.ts` 的 `mergeClause()`、`archiveDiscardedHistory()`、`replaceWithCheckpoint()`、`tryPartialCompact()`；`src/compact/boundary-archive.ts`。

## 对 LANTU 的直接启发

最适合借鉴的不是复制天枢全部多层策略，而是两点：

1. **压缩摘要走 side-path**：在完整历史末尾追加摘要指令生成摘要，不替换 system prompt；摘要请求可复用原会话前缀。
2. **部分压缩保留近期区**：摘要只替换旧区，最近消息保留原文；多次压缩时把旧结构化摘要作为必须合并的输入，并保留原始历史的可访问引用。

这些是天枢“压缩后仍能继续任务”的核心；请求副本折叠、artifact 归档、reclaim gate 可作为后续增强，而不是面试前必须一次实现的前置条件。
