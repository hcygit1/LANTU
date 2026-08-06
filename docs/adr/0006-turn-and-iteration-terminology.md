# 区分 Turn 与 Iteration

LANTU 将一次用户请求定义为一个 Turn，将其中每次模型响应和工具处理循环定义为 Iteration。Journal 只在完整请求结束时写 `turn.completed`；现有 Agent 的内部 `TurnComplete` 语义应调整为 `iteration.completed`，而 `LoopComplete` 才对应 Turn 完成。
