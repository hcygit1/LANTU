# LANTU

LANTU 是一个可恢复的 Coding Agent。以下词汇用于区分持久会话、单次程序运行和单轮交互。

## Language

**Session**:
一段可持久化并跨越多次程序启动恢复的对话。关闭程序不会结束 Session。
_Avoid_: Runtime, 单次运行

**Runtime**:
程序一次启动到退出的运行周期；一个 Session 可以经历多个 Runtime。
_Avoid_: Session

**Turn**:
Session 中由一次用户输入或系统通知触发的一轮 Agent 交互。
_Avoid_: Session, Runtime

**Iteration**:
一个 Turn 内部的一次模型处理循环，通常包含一次模型响应和零个或多个工具执行；它不是独立的用户交互。
_Avoid_: Turn

**Session Journal**:
按发生顺序追加 Session 事实的唯一事实来源，供恢复、诊断和外部观测共同读取。
_Avoid_: Trace, 普通日志

**Conversation**:
当前提供给模型的内存上下文，是 Session Journal 的可重建投影。
_Avoid_: Session Journal, 事实来源

**Interruption**:
动作开始后没有可靠完成记录，因而最终结果未知的状态。它不同于已经明确返回错误的 Failure。
_Avoid_: Failure, 未执行

**LANTU Lens**:
LANTU 内部用于查看、诊断、回放和导出 Session Journal 的观测工具。
_Avoid_: CCWhat, AgentLens, 通用 Coding Agent 分析器

**Task**:
Lens 对一个或多个 Turn 的工作目标范围做出的分析划分；它是后处理结果，不是 Session 恢复所依赖的执行事实。
_Avoid_: Turn, Session

**Lens Annotation**:
用户或 Lens 对 Task 边界、诊断结论和 Dataset 标签做出的后处理标注；它不修改 Session Journal。
_Avoid_: Journal event, 执行事实
