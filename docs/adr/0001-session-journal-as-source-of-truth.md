# 使用 Session Journal 作为唯一事实来源

Session Journal 以追加事件的方式跨 Runtime 保存 Session，并保存恢复和诊断所需的完整消息、工具参数与工具结果。模型流式文本只供 UI 显示，Journal 只保存最终完整消息；中断产生的半截助手回复不进入恢复上下文。对话恢复、故障诊断和 CCWhat 都读取同一份 Journal，`.meta` 仅作为可重建缓存；其写入失败不阻止 Agent，读取时通过 `journal_sequence` 判断是否过期并按需重建。恢复发现上次 Runtime、Turn 或工具没有完成记录时，追加对应的 interrupted 事件，不改写历史，也不自动重跑结果未知的工具。这避免了 Session 与 Trace 双写不一致，也保留了崩溃事实。原始内容只保存在本地 Journal，导出或外发时才执行脱敏。事件由拥有动作的核心层记录：Runtime 记录生命周期，Agent 与工具执行器记录执行阶段；UI 只展示事件，不承担持久化职责。所有持久消息通过 Session 服务统一提交，业务代码不再分别修改 Conversation 和 Journal。
