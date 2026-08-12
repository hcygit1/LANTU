# LANTU Harbor Adapter

The adapter runs the pinned LANTU revision in a Harbor task container and
stores its stream output and Session Journal under `/logs/agent`.

Use an OpenAI-compatible Harbor model name and pass the endpoint credentials
through the environment:

```bash
export OPENAI_API_KEY="$DASHSCOPE_API_KEY"
export OPENAI_BASE_URL="https://dashscope.aliyuncs.com/compatible-mode/v1"
export PYTHONUTF8=1

harbor run \
  -d terminal-bench@2.0 \
  --agent-import-path evals.harbor:LantuAgent \
  -m openai/glm-5.2 \
  --task-ids <task-id>
```

Collected files include:

- `lantu-stream.jsonl`: LANTU `stream-json` output;
- `lantu.stderr.log`: stderr and startup failures;
- `sessions/`: LANTU Session Journal files.
