# 分级上下文压缩测试报告

## 实验信息

- **测试日期**：2026-09-10
- **测试目标**：验证旧工具结果清理、结构化压缩和压缩后上下文重建。
- **离线基准**：`uv run python bench/lantu_validate.py --turns 12`
- **回归测试**：`uv run pytest tests/test_context.py -q`

## 结果

离线固定会话：

- Baseline：24 次模型请求，355,363 估算输入 Token，0 次压缩错误。
- Optimized：24 次模型请求，348,449 估算输入 Token，1 次强制压缩，0 个错误。
- 强制压缩场景两组均成功完成，优化组可复用前缀比例为 89.9%。

回归测试：

```text
tests/test_context.py: 81 passed
```

## 结论

压缩逻辑和压缩后状态重建通过离线验证。当前数据主要证明正确性和固定场景下的上下文变化，尚不能单独代表真实 Provider 的缓存命中率。
