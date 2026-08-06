# 上下文压缩只追加投影边界

上下文压缩不会删除或复制 Session Journal 中的原始消息，而是追加 `context.compacted`，保存摘要和需要保留的稳定 `message_id`。恢复 Conversation 时从最后一个压缩事件构建摘要、引用的保留消息及其后的新消息；审计和 CCWhat 仍可读取完整历史。
