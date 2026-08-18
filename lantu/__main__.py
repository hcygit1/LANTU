from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import time
from pathlib import Path

from lantu.config import ConfigError, load_config
from lantu.hooks import HookConfigError, HookEngine, load_hooks
from lantu.permissions import PermissionMode


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="lantu", description="Lantu AI coding assistant")
    subparsers = parser.add_subparsers(dest="command")
    lens_parser = subparsers.add_parser("lens", help="Read-only Session Journal inspection")
    lens_parser.add_argument(
        "action",
        choices=[
            "list", "events", "search", "tasks", "actions", "diagnose",
            "annotate", "compare", "export", "replay", "evidence", "web",
        ],
        nargs="?",
        default="list",
    )
    lens_parser.add_argument("session_id", nargs="?")
    lens_parser.add_argument("query", nargs="?")
    lens_parser.add_argument("--json", action="store_true", dest="json_output")
    lens_parser.add_argument("--kind", choices=["task_boundary", "diagnosis", "dataset_label"])
    lens_parser.add_argument("--target")
    lens_parser.add_argument("--value", help="Annotation value as a JSON object")
    lens_parser.add_argument(
        "--unsafe-no-redact",
        action="store_true",
        help="Export raw sensitive values (not recommended)",
    )
    lens_parser.add_argument(
        "--execute",
        action="store_true",
        help="Explicitly send an isolated replay request",
    )
    lens_parser.add_argument("--port", type=int, default=18889)
    parser.add_argument(
        "--mode",
        choices=[m.value for m in PermissionMode],
        default=None,
        help="Permission mode (overrides config.yaml)",
    )
    parser.add_argument(
        "--tool-loading-mode",
        choices=["standard", "progressive"],
        default=None,
        help="Tool Schema loading mode (overrides config.yaml)",
    )
    parser.add_argument(
        "-p",
        metavar="PROMPT",
        default=None,
        help="Run non-interactively: execute the prompt and print the result to stdout",
    )
    parser.add_argument(
        "--output-format",
        choices=["text", "stream-json"],
        default="text",
        help="Output format for -p mode: 'text' (default) prints final text, 'stream-json' emits NDJSON events",
    )
    parser.add_argument(
        "--remote",
        action="store_true",
        default=False,
        help="Start in remote mode: WebSocket server on 0.0.0.0:18888 with browser UI",
    )
    parser.add_argument("--capture", action="store_true", help="Capture model HTTP traffic")
    parser.add_argument("--capture-port", type=int, default=7788)
    return parser


def run_lens(
    action: str,
    session_id: str | None,
    json_output: bool = False,
    query: str | None = None,
    annotation_kind: str | None = None,
    annotation_target: str | None = None,
    annotation_value: str | None = None,
    unsafe_no_redact: bool = False,
    execute_replay: bool = False,
    web_port: int = 18889,
) -> None:
    from dataclasses import asdict

    from lantu.tools.lens import LensReader

    reader = LensReader(os.getcwd())
    if action == "list":
        for item in reader.session_ids():
            print(item)
        return
    if action == "web":
        from lantu.tools.lens import run_lens_web

        run_lens_web(os.getcwd(), web_port)
        return
    if action == "annotate":
        from lantu.tools.lens import AnnotationStore

        if not all((session_id, annotation_kind, annotation_target, annotation_value)):
            raise SystemExit(
                "Usage: lantu lens annotate <session_id> --kind <kind> "
                "--target <id> --value <json>"
            )
        try:
            value = json.loads(annotation_value)
        except json.JSONDecodeError as exc:
            raise SystemExit(f"Invalid annotation JSON: {exc}") from exc
        if not isinstance(value, dict):
            raise SystemExit("Annotation value must be a JSON object")
        annotation = AnnotationStore(os.getcwd()).add(
            session_id, annotation_kind, annotation_target, value
        )
        print(json.dumps(asdict(annotation), ensure_ascii=False))
        return
    if action == "compare":
        if not session_id or not query:
            raise SystemExit("Usage: lantu lens compare <left_session_id> <right_session_id>")
        comparison = reader.compare(session_id, query)
        if json_output:
            print(json.dumps(asdict(comparison), ensure_ascii=False))
        else:
            print(f"Compare: {comparison.left.session_id} -> {comparison.right.session_id}")
            for key, delta in comparison.deltas.items():
                if delta:
                    print(f"{key}: {delta:+d}")
        return
    if action == "export":
        if not session_id or not query:
            raise SystemExit("Usage: lantu lens export <session_id> <output.jsonl>")
        count = reader.export_dataset(
            session_id, query, redact_sensitive=not unsafe_no_redact
        )
        print(f"Exported {count} task(s) to {query}")
        return
    if action == "replay":
        if not session_id or not query:
            raise SystemExit("Usage: lantu lens replay <session_id> <task_id>")
        if not execute_replay:
            plan = reader.replay_plan(session_id, query)
            print(json.dumps(asdict(plan), ensure_ascii=False, indent=2))
            return
        config = load_config()
        captures = reader.evidence_links(session_id)
        plan = reader.replay_plan(session_id, query)
        call_ids = {str(item.get("model_call_id", "")) for item in plan.model_calls}
        exact = next(
            (link.capture for link in captures if link.confidence == "exact" and link.model_call_id in call_ids),
            None,
        )
        if exact is None:
            raise SystemExit("Replay requires exact capture evidence for this Task")
        provider = next(
            (item for item in config.providers if item.model == exact.model),
            config.providers[0],
        )
        api_key = provider.resolve_api_key()
        if not api_key:
            raise SystemExit("Replay provider API key is unavailable")
        result = reader.execute_replay(session_id, query, api_key)
        print(json.dumps(asdict(result), ensure_ascii=False))
        return
    if action == "evidence":
        if not session_id:
            raise SystemExit("Usage: lantu lens evidence <session_id> [--json]")
        for link in reader.evidence_links(session_id):
            if json_output:
                print(json.dumps(asdict(link), ensure_ascii=False))
            else:
                print(
                    f"model_call_id={link.model_call_id} "
                    f"journal_sequence={link.journal_sequence} confidence={link.confidence}"
                )
        return
    if action == "diagnose":
        if not session_id:
            raise SystemExit("Usage: lantu lens diagnose <session_id> [--json]")
        report = reader.report(session_id)
        if json_output:
            print(json.dumps(report.to_dict(), ensure_ascii=False))
        else:
            print(report.render_text())
        return
    if action == "tasks":
        if not session_id:
            raise SystemExit("Usage: lantu lens tasks <session_id> [--json]")
        tasks = reader.tasks(session_id)
        for task in tasks:
            if json_output:
                print(json.dumps(asdict(task), ensure_ascii=False))
            else:
                print(
                    f"{task.task_id} sequences={task.start_sequence}-{task.end_sequence} "
                    f"events={len(task.events)}"
                )
        return
    if action == "actions":
        if not session_id:
            raise SystemExit("Usage: lantu lens actions <session_id> [--json]")
        for task, graph in zip(reader.tasks(session_id), reader.action_graphs(session_id)):
            for node in graph.nodes:
                if json_output:
                    print(
                        json.dumps(
                            {"task_id": task.task_id, **asdict(node)}, ensure_ascii=False
                        )
                    )
                else:
                    print(
                        f"{task.task_id} {node.kind} {node.status} "
                        f"sequences={node.start_sequence}-{node.end_sequence} "
                        f"id={node.action_id}"
                    )
        return
    if not session_id:
        if action == "search" and query:
            events = reader.search(query)
        else:
            raise SystemExit("Usage: lantu lens events <session_id> [--json]")
    elif action == "search":
        if not query:
            raise SystemExit("Usage: lantu lens search [session_id] <query> [--json]")
        events = reader.search(query, session_id)
    else:
        events = ((session_id, event) for event in reader.read(session_id))
    for current_id, event in events:
        if json_output:
            print(json.dumps({"session_id": current_id, **asdict(event)}, ensure_ascii=False))
        else:
            print(f"{current_id} {event.sequence:04d} {event.timestamp} {event.type}")


def run_inline(config, permission_mode, hook_engine) -> None:
    from lantu.runtime import build_interactive_runtime
    from lantu.ui.inline import InlineApp
    from lantu.ui.inline.session import select_provider

    async def start() -> None:
        provider = await select_provider(config.providers)
        runtime = await build_interactive_runtime(
            config,
            provider,
            permission_mode,
            hook_engine,
            os.getcwd(),
        )
        try:
            await InlineApp(
                runtime,
                show_thinking=config.ui.show_thinking,
            ).run()
        finally:
            await runtime.close()

    asyncio.run(start())


def run_interactive(config, permission_mode, hook_engine) -> None:
    run_inline(config, permission_mode, hook_engine)


def _main() -> None:
    # 先确保 .lantu/ 目录存在，否则下面写 debug.log 会因目录不存在而崩溃
    Path(".lantu").mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(message)s",
        filename=".lantu/debug.log",
        filemode="w",
    )

    args = build_parser().parse_args()

    if args.command == "lens":
        run_lens(
            args.action,
            args.session_id,
            args.json_output,
            args.query,
            args.kind,
            args.target,
            args.value,
            args.unsafe_no_redact,
            args.execute,
            args.port,
        )
        return

    if args.capture:
        os.environ["LANTU_CAPTURE_ENABLED"] = "1"
        os.environ["LANTU_CAPTURE_PORT"] = str(args.capture_port)

    try:
        config = load_config()
    except ConfigError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    if args.tool_loading_mode is not None:
        config.tool_loading_mode = args.tool_loading_mode
    tool_loading_mode = getattr(config, "tool_loading_mode", "standard")

    mode_str = args.mode if args.mode else config.permission_mode
    permission_mode = PermissionMode(mode_str)

    try:
        hooks = load_hooks(config.raw_hooks)
    except HookConfigError as e:
        print(f"Hook config error: {e}", file=sys.stderr)
        sys.exit(1)

    hook_engine = HookEngine(hooks) if hooks else None

    if args.p is not None:
        output_format = getattr(args, "output_format", "text")
        asyncio.run(_run_prompt(config, permission_mode, hook_engine, args.p, output_format))
        return

    # Remote 模式：启动 WebSocket 服务器，浏览器访问 http://localhost:18888
    if args.remote:
        from lantu.remote import RemoteServer

        server = RemoteServer(
            providers=config.providers,
            mcp_servers=config.mcp_servers,
            hook_engine=hook_engine,
            show_thinking=config.ui.show_thinking,
            tool_loading_mode=tool_loading_mode,
            repo_map_config=getattr(getattr(config, "context", None), "repo_map", None),
        )
        asyncio.run(server.run())
        return

    if not sys.stdin.isatty() or not sys.stdout.isatty():
        print(
            'Error: interactive mode requires a TTY; use `lantu -p "prompt"` instead.',
            file=sys.stderr,
        )
        sys.exit(1)

    run_interactive(config, permission_mode, hook_engine)


def main() -> None:
    try:
        _main()
    except KeyboardInterrupt:
        raise SystemExit(130) from None


async def _run_prompt(config, permission_mode, hook_engine, prompt: str, output_format: str = "text") -> None:
    from lantu.client import create_client
    from lantu.memory import SessionManager
    from lantu.tools.lens.capture_runtime import CaptureProxy, CaptureUnavailable

    provider = config.providers[0]
    session = SessionManager(os.getcwd()).create()
    session.start_runtime("new")
    capture = None
    if os.environ.get("LANTU_CAPTURE_ENABLED") == "1":
        capture = CaptureProxy(
            os.getcwd(),
            int(os.environ.get("LANTU_CAPTURE_PORT", "7788")),
            session.session_id,
        )
        try:
            capture.start()
        except CaptureUnavailable as exc:
            session.close()
            raise SystemExit(f"Capture error: {exc}") from exc
    try:
        client = create_client(provider)
    except BaseException:
        session.close()
        if capture is not None:
            capture.stop()
        raise
    try:
        await _run_prompt_with_client(
            config,
            permission_mode,
            hook_engine,
            prompt,
            output_format,
            provider,
            client,
            session,
        )
    finally:
        try:
            await client.aclose()
        finally:
            if capture is not None:
                capture.stop()
                failure = capture.failure()
                if failure:
                    from lantu.memory.session import ExecutionEvent

                    session.record(
                        ExecutionEvent(
                            "error.occurred",
                            {"phase": "capture", "message": failure},
                        )
                    )
                    print(f"Capture warning: {failure}", file=sys.stderr)
            session.close()


async def _run_prompt_with_client(
    config,
    permission_mode,
    hook_engine,
    prompt: str,
    output_format: str,
    provider,
    client,
    session,
) -> None:
    from lantu.agent import (
        Agent,
        CompactNotification,
        ErrorEvent,
        LoopComplete,
        PermissionRequest,
        PermissionResponse,
        RetryEvent,
        StreamText,
        ThinkingText,
        ToolResultEvent,
        ToolUseEvent,
        TurnComplete,
        UsageEvent,
    )
    from lantu.client import resolve_context_window
    from lantu.conversation import ConversationManager
    from lantu.memory.instructions import load_instructions
    from lantu.permissions import (
        DangerousCommandDetector,
        PathSandbox,
        PermissionChecker,
        RuleEngine,
    )
    from lantu.tools import create_default_registry
    from lantu.agents.loader import AgentLoader
    from lantu.agents.task_manager import TaskManager
    from lantu.agents.trace import TraceManager
    from lantu.tools.agent_tool import AgentTool
    from lantu.tools.impl.tool_search import ToolSearchTool
    from lantu.teams.manager import TeamManager
    from lantu.teams.models import BackendType
    from lantu.tools.team_create import TeamCreateTool
    from lantu.tools.team_delete import TeamDeleteTool
    from lantu.worktree import WorktreeManager
    from lantu.config import WorktreeConfig
    from lantu.context.repo_map import build_repo_map

    is_json = output_format == "stream-json"

    def emit_json(obj: dict) -> None:
        """输出一行 NDJSON 到 stdout"""
        print(json.dumps(obj, ensure_ascii=False), flush=True)

    # 第 2 层：尽力从 provider 自动拉取模型的 context window（缓存在 provider 上）。
    # 不会抛异常或阻塞启动；失败则退化到映射表。
    await resolve_context_window(provider)
    work_dir = os.getcwd()
    home = Path.home()

    checker = PermissionChecker(
        detector=DangerousCommandDetector(),
        sandbox=PathSandbox(work_dir),
        rule_engine=RuleEngine(
            user_rules_path=home / ".lantu" / "permissions.yaml",
            project_rules_path=Path(work_dir) / ".lantu" / "permissions.yaml",
            local_rules_path=Path(work_dir) / ".lantu" / "permissions.local.yaml",
        ),
        mode=permission_mode,
    )

    instructions = load_instructions(work_dir)
    registry = create_default_registry(
        loading_mode=getattr(config, "tool_loading_mode", "standard")
    )
    registry.register(ToolSearchTool(registry, protocol=provider.protocol))

    repo_map_config = getattr(getattr(config, "context", None), "repo_map", None)
    repo_map = None
    if repo_map_config is not None and repo_map_config.enabled:
        repo_map = build_repo_map(
            work_dir,
            max_tokens=repo_map_config.max_tokens,
        )

    agent = Agent(
        client=client,
        registry=registry,
        protocol=provider.protocol,
        work_dir=work_dir,
        permission_checker=checker,
        context_window=provider.get_context_window(),
        instructions_content=instructions,
        hook_engine=hook_engine,
        session=session,
        repo_map=repo_map,
    )

    wt_cfg = config.worktree or WorktreeConfig()
    wt_manager = WorktreeManager(
        repo_root=work_dir,
        symlink_directories=wt_cfg.symlink_directories,
    )
    trace_manager = TraceManager()
    task_manager = TaskManager()
    agent_loader = AgentLoader(work_dir, enable_verification=config.enable_verification_agent)
    agent_loader.load_all()
    team_manager = TeamManager(worktree_manager=wt_manager, trace_manager=trace_manager)

    agent_tool = AgentTool(
        agent_loader=agent_loader,
        task_manager=task_manager,
        trace_manager=trace_manager,
        parent_agent=agent,
        enable_fork=config.enable_fork,
        provider_config=provider,
        worktree_manager=wt_manager,
        team_manager=team_manager,
    )
    registry.register(agent_tool)
    registry.register(TeamCreateTool(
        team_manager=team_manager,
        parent_agent=agent,
        teammate_mode="in-process",
        is_interactive=False,
        enable_coordinator_mode=config.enable_coordinator_mode,
    ))
    registry.register(TeamDeleteTool(team_manager=team_manager, parent_agent=agent))

    def drain_notifications() -> list[str]:
        notes: list[str] = []
        for t in task_manager.poll_completed():
            notes.append(
                f"<task-notification>\n<task_id>{t.id}</task_id>\n"
                f"<status>{t.status}</status>\n<result>{t.result}</result>\n"
                f"</task-notification>"
            )
        notes.extend(team_manager.drain_lead_mailbox())
        return notes

    def drain_mailbox_only() -> list[str]:
        return team_manager.drain_lead_mailbox()

    agent.notification_fn = drain_mailbox_only

    # 使用事件驱动的 agent.run()，支持 text 和 stream-json 两种输出格式
    conv = ConversationManager()
    conv.add_user_message(prompt)
    session.start_turn("user")
    session.commit_message(conv.history[0])
    history_cursor = len(conv.history)
    turn_open = True

    async def recorded_events():
        nonlocal history_cursor, turn_open
        try:
            async for event in agent.run(conv):
                if isinstance(event, CompactNotification) and event.boundary is not None:
                    session.context_compacted(
                        event.boundary.summary,
                        event.boundary.keep,
                    )
                    history_cursor = len(conv.history)
                elif isinstance(event, TurnComplete):
                    for message in conv.history[history_cursor:]:
                        session.commit_message(message)
                    history_cursor = len(conv.history)
                elif isinstance(event, LoopComplete):
                    for message in conv.history[history_cursor:]:
                        session.commit_message(message)
                    history_cursor = len(conv.history)
                    session.complete_turn(event.total_turns)
                    turn_open = False
                yield event
        finally:
            for message in conv.history[history_cursor:]:
                session.commit_message(message)
            if turn_open:
                session.interrupt_turn("agent_error")
                turn_open = False

    start = time.monotonic()
    text_buf = ""
    total_input = 0
    total_output = 0
    tool_calls: list[dict] = []

    event_stream = recorded_events()
    async for event in event_stream:
        if isinstance(event, StreamText):
            text_buf += event.text
            if is_json:
                emit_json({"type": "assistant", "text": event.text})

        elif isinstance(event, ThinkingText):
            if is_json:
                emit_json({"type": "thinking", "text": event.text})

        elif isinstance(event, ToolUseEvent):
            tool_calls.append({"name": event.tool_name, "is_error": False})
            if is_json:
                emit_json({
                    "type": "tool_use",
                    "tool_name": event.tool_name,
                    "tool_id": event.tool_id,
                    "args": event.arguments,
                })

        elif isinstance(event, ToolResultEvent):
            # 回填最后一个同名 tool_call 的 is_error
            if tool_calls:
                tool_calls[-1]["is_error"] = event.is_error
            if is_json:
                emit_json({
                    "type": "tool_result",
                    "tool_name": event.tool_name,
                    "tool_id": event.tool_id,
                    "output": event.output,
                    "is_error": event.is_error,
                    "elapsed": round(event.elapsed, 3),
                })

        elif isinstance(event, UsageEvent):
            total_input = event.input_tokens
            total_output = event.output_tokens
            if is_json:
                emit_json({
                    "type": "usage",
                    "input_tokens": event.input_tokens,
                    "output_tokens": event.output_tokens,
                })

        elif isinstance(event, TurnComplete):
            if is_json:
                emit_json({"type": "turn_complete", "turn": event.turn})

        elif isinstance(event, LoopComplete):
            # 最终结果：stream-json 输出 result 行，text 模式直接打印文本
            elapsed_ms = int((time.monotonic() - start) * 1000)
            if is_json:
                emit_json({
                    "type": "result",
                    "result": text_buf,
                    "duration_ms": elapsed_ms,
                    "num_turns": event.total_turns,
                    "tool_calls": tool_calls,
                    "usage": {
                        "input_tokens": total_input,
                        "output_tokens": total_output,
                    },
                    "stop_reason": "end_turn",
                })
            else:
                print(text_buf, end="", flush=True)
            break

        elif isinstance(event, ErrorEvent):
            if is_json:
                emit_json({"type": "error", "message": event.message})
            else:
                print(f"Error: {event.message}", file=sys.stderr, flush=True)

        elif isinstance(event, CompactNotification):
            if is_json:
                emit_json({"type": "compact", "message": event.message})

        elif isinstance(event, RetryEvent):
            if is_json:
                emit_json({"type": "retry", "reason": event.reason})

        elif isinstance(event, PermissionRequest):
            # -p 非交互模式：自动批准所有权限请求
            event.future.set_result(PermissionResponse.ALLOW)

    await event_stream.aclose()

    # 如果有 team 在运行，轮询等待 teammate 完成
    if not team_manager._teams:
        return

    for i in range(90):
        await asyncio.sleep(2)
        running = {k: not t.done() for k, t in task_manager._async_tasks.items()}
        completed_ids = [t.id for t in task_manager._tasks.values() if t.status != "running"]
        print(f"[poll {i}] running={running} completed={completed_ids} teams={list(team_manager._teams.keys())} queue_size={task_manager._notify_queue.qsize()}", file=sys.stderr, flush=True)
        notes = drain_notifications()
        if not notes:
            has_running = any(v for v in running.values())
            if not has_running:
                print(f"[poll {i}] no running tasks, breaking", file=sys.stderr, flush=True)
                break
            continue
        for note in notes:
            conv.add_system_reminder(note)
        # 后续 team 轮询仍用 run_to_completion，避免重复事件循环
        last_result = await agent.run_to_completion(
            "Teammate notifications received. Process them and continue.", conv
        )
        if is_json:
            emit_json({"type": "assistant", "text": last_result})
        else:
            print(last_result, flush=True)


if __name__ == "__main__":
    main()
