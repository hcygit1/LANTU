# LANTU Harbor Adapter

The adapter packages the current LANTU workspace into a Harbor task container and
stores its stream output and Session Journal under `/logs/agent`.

Use an OpenAI-compatible Harbor model name and pass the endpoint credentials
through the environment:

```bash
export OPENAI_API_KEY="$DASHSCOPE_API_KEY"
export OPENAI_BASE_URL="https://dashscope.aliyuncs.com/compatible-mode/v1"
export PYTHONUTF8=1
# Optional. Loopback addresses are translated for Docker automatically.
export LANTU_INSTALL_PROXY="http://127.0.0.1:7897"

harbor run \
  -d terminal-bench@2.0 \
  --agent-import-path evals.harbor:LantuAgent \
  -m openai/glm-5.2 \
  --task-ids <task-id>
```

If `LANTU_INSTALL_PROXY` is not set, the adapter checks the host's common
local proxy ports (`7897`, `7890`, `10809`, `10808`). The adapter downloads and
caches the Linux `uv` binary on the host, then uploads both `uv` and the pinned
LANTU source into the task container. The container does not need `apt`, curl,
Git, or direct GitHub access.

Collected files include:

- `lantu-stream.jsonl`: LANTU `stream-json` output;
- `lantu.stderr.log`: stderr and startup failures;
- `sessions/`: LANTU Session Journal files.
