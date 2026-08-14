# 开源 Coding Agent 的前缀缓存与上下文组织模式

调研日期：2026-08-14

范围：只核查成熟开源 Coding Agent 的官方仓库、源码和官方文档，重点包括 OpenCode、Pi、Aider、Kimi CLI/Kimi Code。

## 结论

WhalePod 提出的六项原则都不是孤立设计，在成熟开源 Coding Agent 中都能找到对应实践。但它们通常分散在不同项目中，并不是某个项目完整采用同一套方案。

| 原则 | 是否已有成熟实践 | 最直接的官方证据 |
| --- | --- | --- |
| 稳定前缀 | 是 | OpenCode 将一个 Context Epoch 内首次渲染的 System Context 定义为不可变的 provider-cache baseline；Aider固定组织 system prompt、只读文件、Repo Map、可编辑文件以利用缓存。 |
| 追加式历史 | 是 | Pi 将 Session 保存为 JSONL，消息、模型变更、压缩记录等都通过 append 方法新增条目，并用 `id/parentId` 保留分支。 |
| 压缩后重建上下文 | 是 | Pi 追加 `CompactionEntry` 后，以 `summary + firstKeptEntryId 之后的消息` 重建；OpenCode 完成压缩后启动新的 Context Epoch 并重新渲染完整 baseline。 |
| 文件/内容去重 | 是，但实现范围要说清楚 | Aider 的 Repo Map 不再重复放入已经作为 chat file 提供的文件；Kimi CLI 曾明确加入基于内容哈希的缓存附件去重。它们不能证明“所有文件读取结果都应该全局按哈希去重”。 |
| Provider 缓存亲和 | 是 | Kimi Code 为 Kimi、Anthropic、OpenAI 和 OpenAI Responses 传递 Session prompt cache key；Pi 的 provider 参数也提供 `sessionId`，用于缓存、路由或其他 session-aware 行为。 |
| Repo Map | 是，但不是行业统一路线 | Aider 会分析整个代码库，按 token 预算选取重要定义并生成 Repo Map；OpenCode、Pi、Kimi 更偏向按需搜索/读取和项目说明文件，未发现它们采用 Aider 式全局 Repo Map 的同等公开证据。 |

## 逐项证据

### 1. 稳定前缀

OpenCode 的官方上下文设计将 `Context Epoch` 定义为：从首次渲染 System Context 开始，到压缩、Session 移动或不兼容上下文变化为止；这段时间内 baseline 保持不可变。上下文变化以按时间追加的 system message 表示，而不是回头修改旧前缀。这正是“稳定前缀 + 增量尾部”的设计。

来源：[OpenCode CONTEXT.md](https://github.com/anomalyco/opencode/blob/dev/CONTEXT.md)

Aider 的官方缓存文档也明确说明，它专门组织聊天内容，以缓存 system prompt、只读文件、Repo Map 和可编辑文件。这属于较早、已落地的稳定大块前缀实践。

来源：[Aider Prompt caching](https://aider.chat/docs/usage/caching.html)

### 2. 追加式历史

Pi 的 Session 是 JSONL 文件。除 Header 外，每一项带 `id` 和 `parentId`；消息、压缩、模型切换和自定义状态都有对应的 `append*` 方法。回到旧节点不会覆盖旧记录，而是从旧节点产生另一条分支。

来源：[Pi Session File Format](https://github.com/badlogic/pi-mono/blob/main/packages/coding-agent/docs/session.md)

因此，追加式事实历史不是 WhalePod 独有；Pi 已把它用于会话恢复、分支和压缩记录。

### 3. 压缩后重建上下文

Pi 的压缩流程是：找到切点、生成摘要、追加 `CompactionEntry`，然后为下一次请求重建上下文。模型看到的是 system prompt、摘要和 `firstKeptEntryId` 之后的近期消息，完整旧历史仍保留在 Session 文件中。

来源：[Pi Compaction](https://github.com/badlogic/pi-mono/blob/main/packages/coding-agent/docs/compaction.md)

OpenCode 的设计也明确要求：只有完成的压缩事件才成为模型可见检查点；随后重新加载 projected history，并为下一次 provider attempt 渲染新的完整 baseline。失败或中断的压缩不改变原历史边界。

来源：[OpenCode Session V2 Spec](https://github.com/anomalyco/opencode/blob/dev/specs/v2/session.md)

这说明“压缩是新增检查点，然后重新投影上下文”，比直接改写历史更符合成熟实现。

### 4. 文件/内容去重

Aider 生成 Repo Map 时会跳过已经作为 chat file 加入上下文的文件，避免同一份代码既以完整文件出现，又在 Repo Map 中重复出现。Repo Map 本身也按文件集合、提及内容和修改时间缓存计算结果。

来源：[Aider repomap.py](https://github.com/Aider-AI/aider/blob/main/aider/repomap.py)

Kimi CLI 的官方 Changelog 记录过“基于内容哈希对缓存附件去重”。这是明确的内容寻址去重案例。

来源：[Kimi CLI CHANGELOG](https://github.com/MoonshotAI/kimi-cli/blob/main/CHANGELOG.md)

需要限制结论：这些证据支持“同一内容不要在缓存或同一请求结构里无意义重复”，但不支持把所有重复 `Read` 结果自动删除。文件可能已经变化，工具结果的发生时间和因果关系也可能重要。

### 5. Provider 缓存亲和

Kimi Code 的官方 Release Notes 说明，它把 Session prompt cache key 传给 OpenAI 和 OpenAI Responses；此前同一意图已传给 Kimi 与 Anthropic。其仓库规则也明确说明 `sessionId` 可以作为请求提示映射到 provider 的 `prompt_cache_key`。

来源：

- [Kimi Code Releases](https://github.com/MoonshotAI/kimi-code/releases)
- [Kimi Code AGENTS.md](https://github.com/MoonshotAI/kimi-code/blob/main/AGENTS.md)

Pi 的 provider 公共参数也包含 `sessionId`，官方注释明确写明 provider 可用它启用 prompt caching、request routing 或其他 session-aware 能力。

来源：[Pi AI types](https://github.com/badlogic/pi-mono/blob/main/packages/ai/src/types.ts)

所以“稳定请求前缀”和“稳定 Session 路由键”是两件互补的事：前者提高内容匹配概率，后者提高请求落到相同缓存池的概率。

### 6. Repo Map

Aider 是最明确的成熟实现。它用 tree-sitter 提取定义和引用关系，对符号做图排序，再按 token 预算选取重要代码结构。已有完整文件在聊天中时，Map 不再重复展示它们。

来源：

- [Aider Repository map](https://aider.chat/docs/repomap.html)
- [Aider repomap.py](https://github.com/Aider-AI/aider/blob/main/aider/repomap.py)

Repo Map 有价值，但不是所有 Coding Agent 的必选架构。OpenCode、Pi 和 Kimi 的公开设计更强调 `AGENTS.md`/上下文文件，以及由 Agent 按需调用 Glob、Grep、Read 等工具。因而 WhalePod 可以把 Repo Map 作为可选上下文源，而不是 Session 或前缀缓存正确性的硬依赖。

## 对 WhalePod 的直接判断

这套方案的核心组合是合理的：

1. 事实历史只追加，保证恢复和审计。
2. 每次请求从事实历史投影出模型上下文。
3. 压缩只新增检查点，不覆盖原事实。
4. 一个压缩周期内尽量保持前缀不变，变化追加到尾部。
5. 使用稳定 Session key 提高 Provider 缓存亲和。
6. 内容哈希用于避免明确可判定的重复载荷；Repo Map 作为可选的代码库摘要层。

真正需要谨慎的是“文件/内容去重”：只能删除语义上确实重复、且不承担时序证据的内容，不能仅凭哈希相同就从永久历史中移除事件。
