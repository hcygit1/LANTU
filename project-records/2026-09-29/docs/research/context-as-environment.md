# Context as an Environment 调研

## 来源

- 论文原文（arXiv HTML）：[Context as an Environment: Programmatic Context Management for Long-Horizon Agents](https://arxiv.org/html/2608.21690)
- 论文摘要页：[arXiv:2608.21690](https://arxiv.org/abs/2608.21690)
- 官方复现代码入口（论文实验配置中给出）：[QwenPaw `scroll-research`](https://github.com/niceIrene/QwenPaw/tree/scroll-research)

论文作者为 Yin Lin、Elaine Ang、Erkang Zhu、Bolin Ding 和 Jingren Zhou，提出的系统叫 **Scroll**。

## 一句话结论

Scroll 不是把历史压成一段摘要，而是把历史保存在可查询、可执行的外部 Session Environment 中；模型每轮用代码查询和计算，只把明确打印出的结果放进当前上下文。

## 论文到底反对什么

论文认为，传统压缩和写入式外部记忆都有同一个问题：在未来需求还不知道时，就提前决定哪些信息保留。被摘要或提取过程遗漏的细节，即使原始日志还在，也无法再被模型直接使用。

论文并没有声称任何摘要都无用，而是指出摘要不能作为历史事实的唯一表示。Scroll 仍然可以生成紧凑的索引和工作视图，但原始事件和大 payload 必须保留，并且能够按稳定地址恢复。

## Scroll 的真实结构

论文将会话状态建模为：

```text
S_t = (L_t, P_t, V_t)

L_t：追加写入的事件日志，保存用户消息、模型响应、工具调用和工具结果
P_t：事件引用的原始 payload，大结果可放在外部文件中
V_t：持久化运行时中的变量命名空间和派生状态
```

模型每次真正看到的只是有预算限制的 working view，而不是全部 `S_t`。

### 1. Append-only Event Log

每个事件有不可变、单调递增的 `seq` 地址和角色、会话、时间、工具状态等元数据。论文实现使用 SQLite；大 payload 外置保存，事件行保留预览和恢复指针。这样日志是事实来源，视图怎么变化都不会改写历史。

### 2. Persistent Python Kernel

模型通过 `exec` 在沙箱 Python 内操作状态。工具结果、历史查询结果和派生对象可以绑定成带类型和 provenance 的变量，跨模型调用继续存在，不必每轮重新序列化进 prompt。

### 3. 查询、恢复、计算、暴露四步

```text
ms.search(...)       → 按 BM25、类型、时间和范围找到事件 seq
ms.expand(seq/range) → 按地址恢复精确事件或外部 payload
Python 计算         → 过滤、合并、排序、聚合、处理状态变化
print(value)         → 只有明确打印的投影进入下一轮 working view
```

这就是“context as an environment”的关键：上下文不是固定文本，而是模型从外部状态中编程构造出的视图。

### 4. Eviction，而不是删除历史

working view 超过预算时，Scroll 保护当前轮、最近尾部和最新工具结果；优先把已完成的大工具 payload 折叠成 `seq` 指针，再从最早的已完成区间逐步移出视图。移出的内容仍在 Event Log 中，模型可以通过 `seq` 恢复。

同时维护 eviction index：每段被移出的区间有简短 headline 和精确地址。索引按层级合并，近期历史细，远期历史粗，避免索引本身线性膨胀。

## 论文是否“反摘要”

准确说法是：**反对不可逆的摘要替换，不是禁止摘要。**

论文的消融实验直接比较了“原始记录被摘要替换并丢弃”的版本。该版本在 BEAM_10M 总分降到 **19.9**，涉及精确值、时间推理和知识更新的类别接近失效。完整 Scroll 保留原始记录后，总分为 **73.1**。

在 LOCA 上，四种上下文策略结果如下（Qwen3.8-Max）：

| 方法 | 128K | 256K | 从 128K 到 256K 的下降 |
| --- | ---: | ---: | ---: |
| 摘要 Agent | 86.7 | 65.3 | -21.4 |
| Retrieval Agent | 88.0 | 66.7 | -21.3 |
| CodeAct Agent | 89.3 | 85.3 | -4.0 |
| Scroll | 89.3 | 86.7 | -2.6 |

论文报告的主结果是：LongMemEval_S **94.8%**、BEAM_10M **73.1**、LOCA_256K **86.7%**。这些结果是在论文自己的模型、提示词和评测协议下得到的，不应直接当作 LANTU 的可比结果。

## 视频总结与论文的对应关系

### 一致的部分

- 不把摘要当作唯一事实来源。
- 原始历史放在上下文之外，需要时再查询和恢复。
- 当前窗口只暴露需要的工作视图，避免把全部历史重新塞给模型。
- 被移出当前视图的内容仍然可以恢复，而不是永久丢失。
- 状态和历史分离：历史保存原始证据，当前上下文只保存工作状态和投影。

### 视频的延伸或改写

视频说的“硬切窗口 + 外部记忆”可以作为工程上的简化描述，但论文没有把核心机制定义成“开一个全新的对话窗口”。论文的术语是 **working view eviction**：清理当前视图，保留同一个持久化 Session Environment、Event Log、Python kernel 和恢复索引。

视频中的 `notes`、`history`、`State Patch`、`State Projection` 不是论文中完整对应的四个正式组件：

- `history` 对应论文的 Event Log 和 Durable Payload Storage。
- `notes` 类似模型维护的 headline、索引和 resident namespace，但论文没有要求一个固定的 notes 文件。
- `State Projection` 与论文的 `print(value)` 工作视图相近：把外部状态编译成当前需要的投影。
- `State Patch` 不是论文提出的正式算法。论文允许在 Python namespace 中计算和更新派生状态，但不定义一个叫 State Patch 的通用补丁协议。

因此，面试时不要把视频里的术语说成论文原话。

## 与 LANTU 当前设计的关系

LANTU 已经具备一些相同方向的部件：

```text
Session Journal       → 追加式事实记录
工具结果落盘          → 大 payload 外置保存
File Ledger           → 文件版本与读取范围索引
结构化摘要            → 当前任务状态的紧凑表示
Appendix 增量更新     → 只把发生变化的动态块追加到上下文
Lens                  → 查看事件、缓存断点和诊断证据
```

主要差距是：LANTU 的压缩仍然把摘要放回 conversation，模型不能像 Scroll 一样通过统一的 `search → expand → compute → print` 接口查询任意旧事件。也就是说，LANTU 目前是“保留日志 + 摘要/文件索引辅助恢复”，Scroll 是“保留日志 + 程序化构造 working view”。

## 对 LANTU 最值得借鉴的低复杂度改进

不建议现在重写成完整 Scroll。面试前优先做下面三个低复杂度点：

### 1. 把 Journal 的稳定序号暴露为恢复指针

大工具结果、被压缩消息和文件读取记录都带 `journal_seq` 或等价稳定 ID。上下文只放预览和指针，Agent 需要细节时通过一个只读 `journal_search` / `journal_expand` 工具恢复原文。

### 2. 给压缩结果增加“可恢复边界”

结构化摘要中不要声称它替代了历史；记录被压缩的起止事件序号、涉及文件版本和相关工具调用。这样摘要只负责导航，事实仍由 Journal 提供。

### 3. 只输出查询结果，不把整段历史重新注入

恢复工具先做过滤、范围选择和裁剪，只把模型当前需要的字段返回。不要让 Agent 读取整个 session 文件，否则会再次制造上下文污染和缓存抖动。

这些改动可以复用 LANTU 现有 Journal、Lens 和工具结果落盘逻辑，不需要立即引入持久 Python kernel、完整沙箱或新评测框架。

## 不应直接照搬的部分

- Scroll 的 `exec` 沙箱和持久 Python namespace：工程量和安全边界都很大，不适合作为面试前的快速改造。
- 论文中的 LongMemEval、BEAM、LOCA 分数：这是 Scroll 的实验结果，不是 LANTU 的结果。
- “token 消耗降低九成、准确率 0.84 到 0.94”：提供的视频总结没有给出可核验的原始实验来源，不能当作这篇论文的结果。论文给的是上面的 benchmark 分数和中位输入 token 等指标。

## 面试版总结

> 传统压缩的问题不是上下文变短，而是提前决定了未来可能需要什么，导致被压掉的事实无法恢复。Scroll 把 session 变成可查询的外部执行环境：Event Log 保存不可变原始事件，payload 可以外置，持久运行时保存带类型的变量，模型通过代码搜索、恢复和计算，只有显式打印的投影进入当前上下文。上下文超限时只驱逐 working view，不删除事实，并用带事件地址的索引支持恢复。LANTU 当前已有 Journal、落盘、File Ledger 和结构化压缩，下一步可以先补稳定 journal 指针和只读查询/恢复工具，把摘要从“事实来源”降级为“导航索引”。

