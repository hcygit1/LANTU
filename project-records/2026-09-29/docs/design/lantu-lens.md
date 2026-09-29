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

---

## 总流程图

### 数据流总览

```text
运行时（事实产生）
 ├─ 散点埋点：agent.py 当场 session.record(...)
 │    tool.* / permission.decided / turn.* / context.*
 │    ← 疑问：为什么不用 hook 记？
 │      hook 是用户扩展点（可关可改），journal 是系统事实源（恢复依赖），不能外包
 └─ 包裹埋点：LensRequestRecorder.stream() 包住 client.stream()
      model.request.* + usage.recorded（含双指纹）
        │
        │ Session.record(ExecutionEvent) ── journal.append 补信封
        │   ← 疑问：为什么分 Session / Journal 两层？
        │     Session 管业务语义，Journal 管存储机制；Lens 只读只需 Journal
        ▼
.lantu/sessions/<id>.jsonl     事实源 · NDJSON · 必写
  ← 锁：单写者 FileLock，粒度到单个 session 文件，拿不到立刻失败
        │ 可选 --capture：CaptureProxy 拉起 mitmdump（挂 recorder.py）
        ▼
.lantu/lens/capture/<id>.jsonl  原始 HTTP · 脱敏 · model_call_id 关联 journal
  ← 疑问：为什么需要抓包？journal 只存指纹不存字节，重放/看真实报文必须抓包
  ← 工具：只有 mitmproxy 是抓包；WebSocket=远程 UI，Textual=没用上
  ← 原理：MITM 分别跟两端握手拿到两把对称密钥（数据非私钥加密）；
     CA 证书=信任锚（身份+公钥+签名），mitmproxy 首次运行生成
        │
        ▼
LensReader（只读 read_file，不拿写锁）
  normalize → segment_tasks(按 turn) / segment_windows(按窗口) → build_event_graph
  ← normalize=加粗分类 kind；segment=按 turn/window 聚块；graph=补「顺序边+同调用边」
        │
        ├─ diagnose → report      配对 started↔terminal，找 failed/interrupted/incomplete
        ├─ cache + fingerprint    指纹对比 → 变化类型 + 命中率
        ├─ compare                两 session 行为快照对比（实验观测）
        │                          ← 比的是行为统计（步数/失败数），不是对话内容；用于回归/A/B
        ├─ action_graph           调用合并成原子动作（展示 + 统计用）
        │                          ← vs EventGraph：EventGraph 细（喂诊断），ActionGraph 粗（对外展示）
        ├─ replay                 plan(读 journal) → execute(需精确抓包 + api key)
        └─ annotations / dataset  注解 + 导出（redact 脱敏）
        │
        ▼
展示：CLI（lantu lens ...） / Web（lantu lens web，六标签页）
  ← 疑问：为什么部分功能只在 CLI？web 只读零副作用；写文件/发请求/跨 session 的放 CLI
```

### 核心关联键

| 键 | 作用 |
|---|---|
| `session_id` | 一切按它归档（journal / capture / annotations 同名） |
| `runtime_id` / `turn_id` | 上下文切分（turn → task） |
| `tool_call_id` / `model_call_id` | 同一次调用的 started↔completed 配对；`model_call_id` 兼作 journal↔capture 的桥 |
| `sequence` | 信封顺序（排序、分段、查缺口） |

### 数据落盘目录

```text
.lantu/
 ├─ sessions/<id>.jsonl      事实源 · 必写
 └─ lens/
     ├─ capture/<id>.jsonl       抓包 · 可选
     └─ annotations/<id>.jsonl   注解 · 派生
```

### 展示侧分工（Web vs CLI）

| | Web（`lantu lens web`） | CLI（`lantu lens ...`） |
|---|---|---|
| 定位 | 只读 · 单 session · 零副作用 | 完整工具集 |
| 覆盖 | events / tasks / actions / cache / diagnosis / evidence + search | 上述 + compare / export / replay / annotate |
| 写文件 / 发请求 | 无 | export / annotate / replay |
