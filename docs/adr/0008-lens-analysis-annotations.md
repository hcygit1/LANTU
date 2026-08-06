# 将 Lens 分析标注与 Session Journal 分离

Session Journal 只保存 Agent 执行和恢复所需的不可修改事实。LANTU Lens 推导出的 Task 边界、人工校正、诊断结论和 Dataset 标签属于后处理分析标注，单独保存，以避免分析修改污染执行历史或影响 Session 恢复。

Lens 默认根据主题、时间间隔和工具行为自动将相近的 Turn 组成 Task，并保存切分置信度。用户可以修改 Task 边界；自动结果和人工修正都属于分析标注，不改变 Journal 中的原始 Turn。

Dataset 导出默认执行脱敏，至少处理凭据、身份信息和本地绝对路径；Prompt、代码和工具结果默认保留。导出前显示脱敏统计，不提供无确认的原始全部导出。
