# OpenCode 单次输出 Token 上限

日期：2026-08-12

## 结论

OpenCode 当前将单次模型调用的输出上限设为 `32,000` tokens，同时不会超过模型声明的输出能力：

```ts
export const OUTPUT_TOKEN_MAX = 32_000

export function maxOutputTokens(model: Provider.Model, outputTokenMax = OUTPUT_TOKEN_MAX): number {
  return Math.min(model.limit.output, outputTokenMax) || outputTokenMax
}
```

即实际值为：

```text
min(模型输出上限, 32000)
```

因此，至少从 OpenCode 的公开实现看，主流 Coding Agent 并不一定默认使用 `8K` 或 `16K`。LANTU 当前设置的 `64K` 比 OpenCode 的默认硬上限高一倍；如果参考 OpenCode，可先改为 `32K`，再用相同任务比较耗时、成功率和输出截断次数。

## 来源

- OpenCode `provider/transform.ts`，commit `1f94d8a3c86b67f4f49a0e341de74e9188381b3a`：[`OUTPUT_TOKEN_MAX`](https://github.com/anomalyco/opencode/blob/1f94d8a3c86b67f4f49a0e341de74e9188381b3a/packages/opencode/src/provider/transform.ts#L18) 和 [`maxOutputTokens`](https://github.com/anomalyco/opencode/blob/1f94d8a3c86b67f4f49a0e341de74e9188381b3a/packages/opencode/src/provider/transform.ts#L1418-L1420)
- OpenCode 请求组装代码：[`session/llm/request.ts`](https://github.com/anomalyco/opencode/blob/1f94d8a3c86b67f4f49a0e341de74e9188381b3a/packages/opencode/src/session/llm/request.ts)，把该值传给模型请求的 `maxOutputTokens`。
