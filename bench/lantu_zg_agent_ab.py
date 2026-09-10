"""End-to-end Agent trajectory A/B for local rg versus zvec-grep MCP."""

from __future__ import annotations

import asyncio
import copy
import json
import time
from collections import Counter
from pathlib import Path
from typing import Any

from lantu.agent import ErrorEvent, LoopComplete, PermissionRequest, PermissionResponse, StreamText, ToolUseEvent, TurnComplete
from lantu.config import MCPServerConfig, load_config
from lantu.memory.journal import SessionJournal
from lantu.permissions import PermissionMode
from lantu.runtime import build_interactive_runtime


ROOT = Path(__file__).resolve().parent.parent
OUTPUT = ROOT / "bench/results/ab/zg_agent_trajectory.json"
REPORT = ROOT / "bench/results/ab/zg_agent_trajectory.txt"
TASKS = (
    ("Locate the FileLedger class and explain in one sentence what state it stores.", ("lantu/memory/file_ledger.py",)),
    ("Find where repeated visible file ranges are removed before tool results enter conversation history.", ("lantu/agent.py",)),
    ("Trace how project model configuration is loaded and then used to create the model client. Name both key files.", ("lantu/config.py", "lantu/client.py")),
    ("Locate the policy that decides when hard context pressure becomes a window rollover.", ("lantu/context/manager.py",)),
    ("Find how CodeSearch connects semantic repository search to the MCP manager.", ("lantu/tools/code_search.py", "lantu/runtime/lifecycle.py")),
)


def _persist_new_messages(runtime: Any, seen: set[int]) -> None:
    for message in runtime.conversation.history:
        if id(message) not in seen:
            runtime.session.append(message)
            seen.add(id(message))


async def run_variant(name: str, *, use_zg: bool) -> dict[str, Any]:
    config = copy.deepcopy(load_config())
    config.mcp_servers = (
        [MCPServerConfig(name="zvec_grep", url="http://127.0.0.1:7999/mcp")]
        if use_zg
        else []
    )
    provider = config.providers[0]
    provider.reasoning_effort = "low"
    provider.max_output_tokens = 1024
    runtime = await build_interactive_runtime(
        config, provider, PermissionMode("bypassPermissions"), None, ROOT
    )
    if runtime.mcp_task is not None:
        await runtime.mcp_task
    records: list[dict[str, Any]] = []
    try:
        for index, (task, expected_paths) in enumerate(TASKS, start=1):
            prompt = task + " Use repository tools, cite the exact file path, and answer briefly."
            turn_id = runtime.session.start_turn("user")
            runtime.conversation.add_user_message(prompt)
            runtime.session.append(runtime.conversation.history[-1])
            seen = {id(message) for message in runtime.conversation.history}
            answer_parts: list[str] = []
            tool_calls: list[str] = []
            errors: list[str] = []
            started = time.perf_counter()
            completed = False
            try:
                async for event in runtime.agent.run(runtime.conversation):
                    if isinstance(event, StreamText):
                        answer_parts.append(event.text)
                    elif isinstance(event, ToolUseEvent):
                        tool_calls.append(event.tool_name)
                    elif isinstance(event, ErrorEvent):
                        errors.append(event.message)
                    elif isinstance(event, PermissionRequest) and not event.future.done():
                        event.future.set_result(PermissionResponse.ALLOW)
                    elif isinstance(event, (TurnComplete, LoopComplete)):
                        _persist_new_messages(runtime, seen)
                        if isinstance(event, LoopComplete):
                            runtime.session.complete_turn(event.total_turns)
                            completed = True
                            break
            except Exception as exc:
                errors.append(f"{type(exc).__name__}: {exc}")
            if not completed and runtime.session.turn_id is not None:
                runtime.session.interrupt_turn("benchmark_error")
            answer = "".join(answer_parts)
            normalized = answer.replace("\\", "/").lower()
            records.append(
                {
                    "task": index,
                    "prompt": task,
                    "expected_paths": list(expected_paths),
                    "passed": all(path.lower() in normalized for path in expected_paths),
                    "elapsed_seconds": round(time.perf_counter() - started, 3),
                    "tool_calls": tool_calls,
                    "answer": answer,
                    "errors": errors,
                    "turn_id": turn_id,
                }
            )
    finally:
        await runtime.close()

    events = SessionJournal.read_file(ROOT / ".lantu/sessions" / f"{runtime.session.session_id}.jsonl")
    usages = [event for event in events if event.type == "usage.recorded"]
    input_tokens = sum(int(event.payload.get("input_tokens", 0)) for event in usages)
    cache_tokens = sum(int(event.payload.get("cache_read_tokens", 0)) for event in usages)
    output_tokens = sum(int(event.payload.get("output_tokens", 0)) for event in usages)
    calls = Counter(call for record in records for call in record["tool_calls"])
    for record in records:
        record.pop("turn_id", None)
    return {
        "variant": name,
        "model": provider.model,
        "tasks_passed": sum(record["passed"] for record in records),
        "tasks_total": len(records),
        "elapsed_seconds": round(sum(record["elapsed_seconds"] for record in records), 3),
        "tool_calls": dict(calls),
        "total_tool_calls": sum(calls.values()),
        "read_file_calls": calls.get("ReadFile", 0),
        "input_tokens": input_tokens,
        "cache_read_tokens": cache_tokens,
        "output_tokens": output_tokens,
        "errors": [error for record in records for error in record["errors"]],
        "tasks": records,
    }


async def main() -> None:
    baseline = await run_variant("baseline_rg", use_zg=False)
    optimized = await run_variant("optimized_zg", use_zg=True)
    data = {"date": "2026-09-10", "runs_per_variant": 1, "baseline": baseline, "optimized": optimized}
    OUTPUT.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = ["ZG Agent trajectory A/B"]
    for result in (baseline, optimized):
        lines.extend([
            "", f"[{result['variant']}]",
            f"tasks_passed: {result['tasks_passed']}/{result['tasks_total']}",
            f"elapsed_seconds: {result['elapsed_seconds']}",
            f"total_tool_calls: {result['total_tool_calls']}",
            f"read_file_calls: {result['read_file_calls']}",
            f"input_tokens: {result['input_tokens']}",
            f"cache_read_tokens: {result['cache_read_tokens']}",
            f"output_tokens: {result['output_tokens']}",
            f"errors: {len(result['errors'])}",
            f"tool_calls: {result['tool_calls']}",
        ])
    REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    asyncio.run(main())
