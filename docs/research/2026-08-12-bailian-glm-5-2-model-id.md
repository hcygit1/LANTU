# 百炼 GLM-5.2 在 Harbor/LANTU 中的模型标识

日期：2026-08-12

## 结论

- 百炼托管版 GLM-5.2 的 API 模型 ID 是 `glm-5.2`。百炼模型列表还单独列出三方直供版 `ZHIPU/GLM-5.2`；二者不能混用。普通百炼 GLM-5.2 应选前者。[阿里云百炼模型列表](https://help.aliyun.com/zh/model-studio/models)
- 北京地域当前推荐的 OpenAI 兼容 Base URL 是 `https://{WorkspaceId}.cn-beijing.maas.aliyuncs.com/compatible-mode/v1`，其中 `{WorkspaceId}` 替换成百炼业务空间 ID。官方已建议北京地域从旧域名 `https://dashscope.aliyuncs.com` 迁移到业务空间专属域名。[OpenAI 兼容接口](https://help.aliyun.com/zh/model-studio/compatibility-of-openai-with-dashscope)
- 智谱官方直连接口同样使用模型 ID `glm-5.2`，但 Base URL 属于智谱服务，不应与百炼 API Key 混用。[智谱 GLM-5.2 文档](https://docs.bigmodel.cn/cn/guide/models/text/glm-5.2)
- LANTU Harbor adapter 要求 Harbor 模型参数为 `provider/model`，随后丢弃 `provider` 并把后半段原样传给 LANTU。因此百炼托管版应传 `--model openai/glm-5.2`；这里的 `openai` 表示 OpenAI 兼容协议，并不表示调用 OpenAI。

## PowerShell 命令

```powershell
$env:OPENAI_API_KEY = "你的百炼 API Key"
$env:OPENAI_BASE_URL = "https://你的WorkspaceId.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"

harbor run `
  --dataset terminal-bench-sample@2.0 `
  --include-task-name regex-log `
  --agent evals.harbor:LantuAgent `
  --model openai/glm-5.2 `
  --n-concurrent 1
```

当前仓库 adapter 的拆分逻辑见 [`evals/harbor/lantu_agent.py`](../../evals/harbor/lantu_agent.py)，仓库示例也使用 `openai/glm-5.2`，见 [`evals/harbor/README.md`](../../evals/harbor/README.md)。如 Harbor 没有把 `OPENAI_BASE_URL` 注入 model connection，可额外设置同值的 `$env:LANTU_BASE_URL`；adapter 会优先读取它。
