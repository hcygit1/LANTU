# Session Journal 默认长期保留

Session Journal 默认长期保留，不再由启动流程按 30 天自动清理；只有用户主动删除 Session 时才删除 Journal 和对应的 `.meta` 缓存。这样可以保留长期故障记录和复盘材料，磁盘占用由用户明确控制。
