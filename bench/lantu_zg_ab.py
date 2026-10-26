"""Repeatable local rg versus zvec-grep MCP retrieval comparison."""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any

from lantu.config import MCPServerConfig
from lantu.mcp.manager import MCPManager
from lantu.tools.code_search import SemanticSearch, SemanticSearchParams
from lantu.tools.grep import Grep, Params as GrepParams


ROOT = Path(__file__).resolve().parent.parent
OUTPUT = ROOT / "bench/results/ab/zg_retrieval.json"
REPORT = ROOT / "bench/results/ab/zg_retrieval.txt"
QUERIES = (
    ("where context window rollover preserves session state", "semantic"),
    ("how file read ranges are deduplicated after tool execution", "semantic"),
    ("class FileLedger", "exact"),
    ("window_rollover", "exact"),
)


async def _run(tool: SemanticSearch | Grep, query: str, mode: str) -> dict[str, Any]:
    started = time.perf_counter()
    if mode == "semantic":
        result = await tool.execute(
            SemanticSearchParams(query=query, path="lantu", include="lantu/**", limit=5)
        )
    else:
        result = await tool.execute(
            GrepParams(pattern=query, path="lantu", include="lantu/**")
        )
    elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
    output = result.output
    return {
        "query": query,
        "mode": mode,
        "backend": (result.meta or {}).get("backend"),
        "degraded": (result.meta or {}).get("degraded", False),
        "mcp_error": (result.meta or {}).get("semantic_error", ""),
        "is_error": result.is_error,
        "found": output != "No matches found." and not result.is_error,
        "output_chars": len(output),
        "elapsed_ms": elapsed_ms,
        "preview": output[:500],
    }


async def main() -> None:
    manager = MCPManager()
    manager.load_configs(
        [MCPServerConfig(name="zvec_grep", url="http://127.0.0.1:7999/mcp")]
    )
    connection = await manager.connect_all()
    if connection.errors:
        raise RuntimeError("; ".join(connection.errors))
    try:
        baseline = {
            "semantic": SemanticSearch(str(ROOT)),
            "exact": Grep(str(ROOT)),
        }
        optimized = {
            "semantic": SemanticSearch(str(ROOT)),
            "exact": Grep(str(ROOT)),
        }
        for tool in optimized.values():
            tool.set_mcp_manager(manager)
        baseline_rows = [await _run(baseline[mode], q, mode) for q, mode in QUERIES]
        optimized_rows = [await _run(optimized[mode], q, mode) for q, mode in QUERIES]
    finally:
        await manager.shutdown()

    data = {
        "root": str(ROOT),
        "queries": len(QUERIES),
        "mcp_tools": sorted(tool.name for tool in connection.tools),
        "baseline": baseline_rows,
        "optimized": optimized_rows,
    }
    OUTPUT.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = ["ZG retrieval A/B", f"queries: {len(QUERIES)}"]
    for name, records in (("baseline", baseline_rows), ("optimized", optimized_rows)):
        lines.extend(
            [
                "",
                f"[{name}]",
                f"found: {sum(item['found'] for item in records)}/{len(records)}",
                f"elapsed_ms: {sum(item['elapsed_ms'] for item in records):.2f}",
                "backends: " + ", ".join(str(item["backend"]) for item in records),
            ]
        )
    REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    asyncio.run(main())
