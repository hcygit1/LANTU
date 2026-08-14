# 百炼 GLM-5.2 思考控制

日期：2026-08-13

## 结论

- 百炼托管版 `glm-5.2` 是**混合思考模型，默认开启思考**。LANTU 当前不传思考控制字段，因此百炼会采用默认值，这就是简单任务仍产生大量 `reasoning_content` 的直接原因。
- 可以完全关闭：在 OpenAI-compatible Chat Completions 请求体中传 `enable_thinking: false`。
- 可以调低思考强度：传 `reasoning_effort: "low"`。百炼 GLM 专属文档列出的档位是 `none / minimal / low / medium / high / xhigh / max`；`none` 不进行推理，`max` 最高。
- 也可以保留思考但限制长度：同时传 `enable_thinking: true` 和 `thinking_budget: <整数>`。百炼说明超过预算后模型会立即进入最终回复。
- `enable_thinking`、`thinking_budget` 和 `reasoning_effort` 都是服务端扩展字段。使用 OpenAI Python SDK 时，为兼容不同 SDK 版本，宜通过 `extra_body` 透传；最终 HTTP JSON 中它们位于请求体顶层。
- 百炼模型卡给出 `glm-5.2` 的最大输出长度和最大思维链长度均为 `131072` Token。未设置 `thinking_budget` 时，默认允许使用模型最大思维链长度，因此约 69K 思考 Token 并未超出服务端能力上限。
- `enable_thinking=false` 的优先级高于 `reasoning_effort`。完全关闭思考时不需要再设置强度。

## 百炼 OpenAI 兼容请求

### 关闭思考

```python
from openai import OpenAI

client = OpenAI(
    api_key="YOUR_BAILIAN_API_KEY",
    base_url="https://{WorkspaceId}.cn-beijing.maas.aliyuncs.com/compatible-mode/v1",
)

stream = client.chat.completions.create(
    model="glm-5.2",
    messages=[{"role": "user", "content": "完成当前编码任务"}],
    extra_body={"enable_thinking": False},
    stream=True,
)
```

发送到 HTTP 接口的实际 JSON 字段位于请求体顶层：

```json
{
  "model": "glm-5.2",
  "messages": [{"role": "user", "content": "完成当前编码任务"}],
  "enable_thinking": false,
  "stream": true
}
```

### 限制思考 Token

```python
stream = client.chat.completions.create(
    model="glm-5.2",
    messages=[{"role": "user", "content": "完成当前编码任务"}],
    extra_body={
        "enable_thinking": True,
        "thinking_budget": 4096,
    },
    stream=True,
)
```

`4096` 只是可用于验证的策略值，不是百炼官方针对 GLM-5.2 给出的推荐值或下限。正式默认值应通过评测确定。

### 调低思考强度

```python
stream = client.chat.completions.create(
    model="glm-5.2",
    messages=[{"role": "user", "content": "完成当前编码任务"}],
    extra_body={
        "enable_thinking": True,
        "reasoning_effort": "low",
    },
    stream=True,
)
```

这最适合当前 Harbor 问题：保留模型推理能力，但避免默认 `max` 强度在简单任务上生成极长思维链。如果 `low` 仍然超时，再叠加 `thinking_budget` 做硬上限。

## 接口兼容性

| 调用端点 | 关闭思考 | 调节思考量 | 备注 |
| --- | --- | --- | --- |
| 百炼 `glm-5.2`，OpenAI-compatible Chat Completions | `extra_body={"enable_thinking": False}` | `reasoning_effort` 调强度；`thinking_budget` 限制思考 Token | `extra_body` 是 OpenAI Python SDK 的透传机制，线上 JSON 字段在顶层 |
| 智谱直连 `glm-5.2`，Chat Completions | `thinking={"type": "disabled"}`，或 `reasoning_effort="none"` / `"minimal"` | `reasoning_effort` | 这是智谱服务的协议，不应直接用于百炼托管版 |

百炼还支持 `max_completion_tokens`，它限制的是“思考 + 最终回答”的总输出。不要用它代替 `thinking_budget` 来解决本次问题，因为总上限会同时挤压最终回答和工具调用空间，可能导致 `finish_reason=length`。

## 对 LANTU 的含义

当前 `OpenAICompatClient` 只发送 `model`、`messages`、`max_tokens`、`stream`、工具等字段，没有发送 `enable_thinking` 或 `thinking_budget`。所以 `config.thinking = false` 目前不会关闭百炼 GLM-5.2 的服务端默认思考。

合理的后续方案是给 OpenAI-compatible provider 增加可透传的思考配置，而不是根据模型名硬编码：

```yaml
providers:
  - type: openai_compatible
    model: glm-5.2
    thinking: true
    reasoning_effort: low
    thinking_budget: 4096
```

当前 Harbor 应先只设置 `reasoning_effort: low` 重跑同一任务，以验证长耗时是否来自默认 `max` 强度；不要同时改变多个参数。若仍然过慢，再加入 `thinking_budget`。是否长期默认关闭、强度或预算设为多少，需要用同一批任务对比成功率、超时率和输出 Token 后决定。

## 官方来源

1. [阿里云百炼：深度思考](https://help.aliyun.com/zh/model-studio/deep-thinking)
   官方列出 `glm-5.2` 为“混合思考模式，默认开启思考模式”；说明 `enable_thinking=false` 可关闭思考；说明 OpenAI Python SDK 需通过 `extra_body` 传入；说明 `thinking_budget` 适用于阿里云直供 GLM，并限制思考过程最大 Token 数。
2. [阿里云百炼：GLM](https://help.aliyun.com/zh/model-studio/glm)
   官方列出 `glm-5.2` 的 `reasoning_effort` 档位及 `enable_thinking` 的优先级。
3. [阿里云百炼：GLM-5.2 模型卡](https://help.aliyun.com/zh/model-studio/glm-5-2)
   官方给出最大输出长度和最大思维链长度均为 131072 Token。
4. [阿里云百炼：OpenAI 兼容 Chat Completions](https://help.aliyun.com/zh/model-studio/qwen-api-via-openai-chat-completions)
   官方定义 `max_completion_tokens` 为思维链与模型回答合计的最大长度，并说明该字段支持 GLM-5 及之后的 GLM 系列。
5. [阿里云百炼：模型列表](https://help.aliyun.com/zh/model-studio/models)
   官方确认百炼托管版模型 ID 是 `glm-5.2`，并支持 OpenAI-compatible 接口。
6. [智谱：GLM-5.2](https://docs.bigmodel.cn/cn/guide/models/text/glm-5.2)
   智谱直连示例使用 `thinking.type` 与 `reasoning_effort`。
7. [智谱：对话补全 API](https://docs.bigmodel.cn/api-reference/%E6%A8%A1%E5%9E%8B-api/%E5%AF%B9%E8%AF%9D%E8%A1%A5%E5%85%A8)
   官方定义 `reasoning_effort` 的枚举、默认值及映射行为。
