"""Repeated complex Agent trajectory A/B for rg versus zvec-grep."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from statistics import mean, pstdev
from typing import Any

import bench.lantu_zg_agent_ab as trajectory


ROOT = Path(__file__).resolve().parent.parent
OUTPUT = ROOT / "bench/results/ab/zg_agent_complex_trajectory.json"
REPORT = ROOT / "bench/results/ab/zg_agent_complex_trajectory.txt"
RUNS = 3
COMPLEX_TASKS = (
    ("Trace the complete path from YAML provider configuration loading to the final OpenAI-compatible chat completion request. Name the key functions and files.", ("lantu/config.py", "lantu/client.py")),
    ("Trace how MCP server configuration becomes a connected manager and is injected into CodeSearch at runtime.", ("lantu/config.py", "lantu/runtime/lifecycle.py", "lantu/tools/code_search.py")),
    ("Explain how FileLedger state changes after ReadFile, after EditFile, and after a Session resume. Cite the implementation files.", ("lantu/agent.py", "lantu/memory/file_ledger.py", "lantu/memory/session.py")),
    ("Trace the decision path from context pressure measurement through stale tool cleanup and structured compaction to window rollover.", ("lantu/context/manager.py",)),
    ("Explain how an oversized tool result is persisted, previewed in Conversation, and later made recoverable. Cite the relevant functions.", ("lantu/context/manager.py",)),
    ("Trace how a tool schema view is recorded as a Schema Epoch and how that state is persisted for later requests or resume.", ("lantu/agent.py", "lantu/memory/session.py")),
    ("Trace how Session Journal events rebuild Conversation and FileLedger during resume, including the functions that replay events.", ("lantu/memory/session.py", "lantu/memory/file_ledger.py")),
    ("Trace the complete CodeSearch fallback chain when zvec semantic or exact MCP search fails, down to native rg and Python Grep.", ("lantu/tools/code_search.py", "lantu/tools/grep.py")),
)


def summarize(runs: list[dict[str, Any]]) -> dict[str, Any]:
    metrics = (
        "tasks_passed", "elapsed_seconds", "total_tool_calls", "read_file_calls",
        "input_tokens", "cache_read_tokens", "output_tokens",
    )
    summary: dict[str, Any] = {"runs": len(runs)}
    for metric in metrics:
        values = [float(run[metric]) for run in runs]
        summary[metric] = {
            "values": values,
            "mean": round(mean(values), 3),
            "stddev": round(pstdev(values), 3),
        }
    summary["errors"] = sum(len(run["errors"]) for run in runs)
    summary["tasks_total_per_run"] = len(COMPLEX_TASKS)
    return summary


async def main() -> None:
    trajectory.TASKS = COMPLEX_TASKS
    baseline_runs: list[dict[str, Any]] = []
    optimized_runs: list[dict[str, Any]] = []
    for index in range(1, RUNS + 1):
        print(f"paired run {index}/{RUNS}: baseline", flush=True)
        baseline_runs.append(await trajectory.run_variant(f"baseline_rg_{index}", use_zg=False))
        print(f"paired run {index}/{RUNS}: optimized", flush=True)
        optimized_runs.append(await trajectory.run_variant(f"optimized_zg_{index}", use_zg=True))

    data = {
        "date": "2026-09-10",
        "model": "deepseek-v4-flash",
        "runs_per_variant": RUNS,
        "tasks_per_run": len(COMPLEX_TASKS),
        "baseline": {"summary": summarize(baseline_runs), "runs": baseline_runs},
        "optimized": {"summary": summarize(optimized_runs), "runs": optimized_runs},
    }
    OUTPUT.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = ["ZG complex Agent trajectory A/B", f"paired_runs: {RUNS}", f"tasks_per_run: {len(COMPLEX_TASKS)}"]
    for name in ("baseline", "optimized"):
        summary = data[name]["summary"]
        lines.extend(["", f"[{name}]"])
        for metric in ("tasks_passed", "elapsed_seconds", "total_tool_calls", "read_file_calls", "input_tokens", "cache_read_tokens", "output_tokens"):
            item = summary[metric]
            lines.append(f"{metric}: mean={item['mean']} stddev={item['stddev']} values={item['values']}")
        lines.append(f"errors: {summary['errors']}")
    REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    asyncio.run(main())
