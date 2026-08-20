"""Repeatable online cache evaluation for LANTU.

The benchmark runs the real LANTU runtime against an OpenAI-compatible
provider. Cache statistics come from the provider's ``cached_tokens`` usage
field; they are not estimated from characters.

Run from the repository root::

    uv run python bench/lantu_live_cache.py
    uv run python bench/lantu_live_cache.py --runs 3 --turns 12

The API key is read from the normal ``OPENAI_API_KEY`` environment variable.
The script never writes the key to the result files.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path
from statistics import mean
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bench.lantu_validate import SESSION
from lantu.agent import (
    ErrorEvent,
    LoopComplete,
    PermissionRequest,
    PermissionResponse,
    StreamText,
    ToolUseEvent,
    TurnComplete,
)
from lantu.config import load_config
from lantu.memory.journal import SessionJournal
from lantu.permissions import PermissionMode
from lantu.runtime import build_interactive_runtime


DEFAULT_OUTPUT = Path("bench/results/lantu_live_cache.json")
DEFAULT_REPORT = Path("bench/results/lantu_live_cache.txt")


def summarize_usage(usages: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate provider usage records without making token estimates."""
    records = list(usages)
    prompt_tokens = sum(
        int(item.get("input_tokens", 0)) + int(item.get("cache_read_tokens", 0))
        for item in records
    )
    cached_tokens = sum(int(item.get("cache_read_tokens", 0)) for item in records)
    output_tokens = sum(int(item.get("output_tokens", 0)) for item in records)
    warm_records = records[1:]
    warm_prompt_tokens = sum(
        int(item.get("input_tokens", 0)) + int(item.get("cache_read_tokens", 0))
        for item in warm_records
    )
    warm_cached_tokens = sum(
        int(item.get("cache_read_tokens", 0)) for item in warm_records
    )
    return {
        "model_requests": len(records),
        "prompt_tokens": prompt_tokens,
        "cached_tokens": cached_tokens,
        "uncached_tokens": prompt_tokens - cached_tokens,
        "output_tokens": output_tokens,
        "overall_cache_hit_rate": round(cached_tokens / prompt_tokens, 4)
        if prompt_tokens
        else 0.0,
        "warm_cache_hit_rate": round(warm_cached_tokens / warm_prompt_tokens, 4)
        if warm_prompt_tokens
        else 0.0,
    }


def _persist_new_messages(runtime: Any, seen: set[int]) -> None:
    for message in runtime.conversation.history:
        if id(message) not in seen:
            runtime.session.append(message)
            seen.add(id(message))


async def run_once(
    root: Path,
    *,
    turns: int,
    mode: str,
    reasoning_effort: str,
    max_output_tokens: int,
) -> dict[str, Any]:
    config = load_config()
    provider = next((item for item in config.providers if item.model == "glm-5.2"), None)
    if provider is None:
        provider = config.providers[0]
    provider.reasoning_effort = reasoning_effort
    provider.max_output_tokens = max_output_tokens
    config.tool_loading_mode = mode
    config.context.repo_map.enabled = False

    runtime = await build_interactive_runtime(
        config,
        provider,
        PermissionMode("bypassPermissions"),
        None,
        root,
    )
    session_id = runtime.session.session_id
    turn_records: list[dict[str, Any]] = []

    try:
        for index, bench_turn in enumerate(SESSION[:turns], start=1):
            turn_id = runtime.session.start_turn("user")
            prompt = f"{bench_turn.user} Answer briefly."
            runtime.conversation.add_user_message(prompt)
            runtime.session.append(runtime.conversation.history[-1])
            seen = {id(message) for message in runtime.conversation.history}
            tool_calls: list[str] = []
            errors: list[str] = []
            answer_parts: list[str] = []
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
                    elif isinstance(event, PermissionRequest):
                        if not event.future.done():
                            event.future.set_result(PermissionResponse.ALLOW)
                    elif isinstance(event, (TurnComplete, LoopComplete)):
                        _persist_new_messages(runtime, seen)
                        if isinstance(event, LoopComplete):
                            runtime.session.complete_turn(event.total_turns)
                            completed = True
                            break
            except Exception as exc:  # Keep the failed turn in the report.
                errors.append(f"{type(exc).__name__}: {exc}")

            if not completed and runtime.session.turn_id is not None:
                runtime.session.interrupt_turn("benchmark_error")
            turn_records.append(
                {
                    "turn": index,
                    "turn_id": turn_id,
                    "elapsed_seconds": round(time.perf_counter() - started, 3),
                    "tool_calls": tool_calls,
                    "answer_chars": len("".join(answer_parts)),
                    "errors": errors,
                }
            )
    finally:
        await runtime.close()

    journal_path = root / ".lantu" / "sessions" / f"{session_id}.jsonl"
    events = SessionJournal.read_file(journal_path)
    usage_events = [event for event in events if event.type == "usage.recorded"]
    usage_by_turn: dict[str | None, list[dict[str, Any]]] = defaultdict(list)
    for event in usage_events:
        usage_by_turn[event.turn_id].append(dict(event.payload))
    for record in turn_records:
        record["usage"] = usage_by_turn.get(record["turn_id"], [])
        record.pop("turn_id", None)

    usage_summary = summarize_usage(event.payload for event in usage_events)
    all_errors = [error for record in turn_records for error in record["errors"]]
    tool_counts = Counter(
        name for record in turn_records for name in record["tool_calls"]
    )
    return {
        "session_id": session_id,
        "model": provider.model,
        "mode": mode,
        "repo_map": False,
        "reasoning_effort": reasoning_effort,
        "turns": len(turn_records),
        "elapsed_seconds": round(
            sum(record["elapsed_seconds"] for record in turn_records), 3
        ),
        "tool_calls": dict(tool_counts),
        "errors": all_errors,
        **usage_summary,
        "turn_details": turn_records,
    }


def build_report(data: dict[str, Any]) -> str:
    runs = data["runs"]
    lines = [
        "LANTU live cache validation",
        f"model: {data['config']['model']}",
        f"mode: {data['config']['mode']}",
        f"turns per run: {data['config']['turns']}",
        f"runs: {len(runs)}",
        "",
        "run | requests | prompt tokens | cached tokens | overall hit | warm hit | errors",
        "--- | ---: | ---: | ---: | ---: | ---: | ---:",
    ]
    for index, run in enumerate(runs, start=1):
        lines.append(
            f"{index} | {run['model_requests']} | {run['prompt_tokens']} | "
            f"{run['cached_tokens']} | {run['overall_cache_hit_rate']:.1%} | "
            f"{run['warm_cache_hit_rate']:.1%} | {len(run['errors'])}"
        )
    if runs:
        lines.extend(
            [
                "",
                f"mean overall hit: {mean(run['overall_cache_hit_rate'] for run in runs):.1%}",
                f"mean warm hit: {mean(run['warm_cache_hit_rate'] for run in runs):.1%}",
                f"total errors: {sum(len(run['errors']) for run in runs)}",
            ]
        )
    return "\n".join(lines) + "\n"


async def run_benchmark(args: argparse.Namespace) -> dict[str, Any]:
    root = args.root.resolve()
    previous = Path.cwd()
    os.chdir(root)
    try:
        runs = []
        for index in range(args.runs):
            print(f"run {index + 1}/{args.runs}: starting", flush=True)
            result = await run_once(
                root,
                turns=args.turns,
                mode=args.mode,
                reasoning_effort=args.reasoning_effort,
                max_output_tokens=args.max_output_tokens,
            )
            runs.append(result)
            print(
                f"run {index + 1}/{args.runs}: "
                f"hit={result['overall_cache_hit_rate']:.1%}, "
                f"warm={result['warm_cache_hit_rate']:.1%}, "
                f"errors={len(result['errors'])}",
                flush=True,
            )
    finally:
        os.chdir(previous)

    return {
        "meta": {
            "network": True,
            "usage_source": "provider usage.recorded cache_read_tokens",
            "token_estimator": "provider reported tokens",
            "note": "Warm rate excludes the first model request of each run; provider cache may persist across sessions.",
        },
        "config": {
            "model": "glm-5.2",
            "mode": args.mode,
            "repo_map": False,
            "turns": args.turns,
            "runs": args.runs,
            "reasoning_effort": args.reasoning_effort,
            "max_output_tokens": args.max_output_tokens,
        },
        "runs": runs,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--turns", type=int, default=len(SESSION))
    parser.add_argument("--mode", choices=["standard", "progressive"], default="standard")
    parser.add_argument("--reasoning-effort", default="low")
    parser.add_argument("--max-output-tokens", type=int, default=2048)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    args = parser.parse_args()
    if args.runs < 1 or args.turns < 1 or args.turns > len(SESSION):
        parser.error(f"runs must be positive and turns must be between 1 and {len(SESSION)}")
    if not os.environ.get("OPENAI_API_KEY"):
        parser.error("OPENAI_API_KEY is not set")

    data = asyncio.run(run_benchmark(args))
    output = args.output if args.output.is_absolute() else ROOT / args.output
    report = args.report if args.report.is_absolute() else ROOT / args.report
    output.parent.mkdir(parents=True, exist_ok=True)
    report.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    report.write_text(build_report(data), encoding="utf-8")
    print(build_report(data))


if __name__ == "__main__":
    main()
