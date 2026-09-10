"""Repeatable local rg versus zvec-grep MCP retrieval comparison."""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any

from lantu.config import MCPServerConfig
from lantu.mcp.manager import MCPManager
from lantu.tools.code_search import CodeSearch, CodeSearchParams


ROOT = Path(__file__).resolve().parent.parent
OUTPUT = ROOT / "bench/results/ab/zg_retrieval.json"
REPORT = ROOT / "bench/results/ab/zg_retrieval.txt"
QUERIES = (
    ("where context window rollover preserves session state", "semantic"),
    ("how file read ranges are deduplicated after tool execution", "semantic"),
    ("class FileLedger", "exact"),
    ("window_rollover", "exact"),
)


async def _run(tool: CodeSearch, query: str, mode: str) -> dict[str, Any]:
    started = time.perf_counter()
    result = await tool.execute(
        CodeSearchParams(query=query, mode=mode, path="lantu", include="lantu/**", limit=5)
    )
    elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
    output = result.output
    return {
        "query": query,
        "mode": mode,
        "backend": result.meta.get("backend"),
        "degraded": result.meta.get("degraded", False),
        "mcp_error": result.meta.get("mcp_error", ""),
        "is_error": result.is_error,
        "found": output != "No matches found." and not result.is_error,
        "output_chars": len(output),
        "elapsed_ms": elapsed_ms,
        "preview": output[:500],
    }


async def main() -> None:
    baseline_tool = CodeSearch(str(ROOT))
    manager = MCPManager()
    manager.load_configs(
        [MCPServerConfig(name="zvec_grep", url="http://127.0.0.1:7999/mcp")]
    )
    connection = await manager.connect_all()
    if connection.errors:
        raise RuntimeError("; ".join(connection.errors))
    optimized_tool = CodeSearch(str(ROOT))
    optimized_tool.set_mcp_manager(manager)
    try:
        baseline = [await _run(baseline_tool, query, mode) for query, mode in QUERIES]
        optimized = [await _run(optimized_tool, query, mode) for query, mode in QUERIES]
    finally:
        await manager.shutdown()

    data = {
        "root": str(ROOT),
        "queries": len(QUERIES),
        "mcp_tools": sorted(tool.name for tool in connection.tools),
        "baseline": baseline,
        "optimized": optimized,
    }
    OUTPUT.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = ["ZG retrieval A/B", f"queries: {len(QUERIES)}"]
    for name, records in (("baseline", baseline), ("optimized", optimized)):
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
