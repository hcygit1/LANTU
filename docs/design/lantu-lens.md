# LANTU Lens

LANTU Lens reads Session Journal files without taking the writer lock or changing
Session recovery data. Lens annotations, capture records, replay plans, and
datasets are derived data.

## Commands

```text
lantu lens list
lantu lens events <session_id> [--json]
lantu lens search <session_id> <query> [--json]
lantu lens tasks <session_id> [--json]
lantu lens actions <session_id> [--json]
lantu lens diagnose <session_id> [--json]
lantu lens evidence <session_id> [--json]
lantu lens compare <left_session_id> <right_session_id> [--json]
lantu lens annotate <session_id> --kind <kind> --target <id> --value <json>
lantu lens export <session_id> <output.jsonl> [--unsafe-no-redact]
lantu lens replay <session_id> <task_id> [--execute]
```

`replay` creates an isolated review plan by default. `--execute` is required to
send a network request and is rejected unless exact capture evidence exists.
The returned body is printed without executing tool calls or writing Journal
events.

## Capture

Install the optional dependency and initialize mitmproxy once:

```text
uv sync --extra capture
mitmdump
```

Then start LANTU with capture enabled:

```text
lantu --capture
lantu --capture -p "prompt"
```

Capture is bound to `127.0.0.1`. LANTU adds temporary model-call and Session
headers, and the proxy removes them before forwarding the request. Authentication
headers are redacted in `.lantu/lens/capture/<session_id>.jsonl`.
