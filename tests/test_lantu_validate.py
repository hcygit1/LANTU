from __future__ import annotations

from shutil import copyfile

import pytest

from bench.lantu_validate import SESSION, ScriptedClient, run_session
from lantu.conversation import ConversationManager


def test_benchmark_payload_places_stable_prefix_before_history() -> None:
    conversation = ConversationManager()
    conversation.add_user_message("hello")

    payload = ScriptedClient._payload(
        conversation,
        system="SYSTEM-MARKER",
        tools=[{"name": "TOOL-MARKER"}],
    )

    assert payload.index("SYSTEM-MARKER") < payload.index("TOOL-MARKER")
    assert payload.index("TOOL-MARKER") < payload.index("hello")


@pytest.mark.asyncio
async def test_offline_benchmark_runs_real_standard_agent(tmp_path) -> None:
    source_dir = tmp_path / "lantu"
    source_dir.mkdir()
    copyfile("lantu/agent.py", source_dir / "agent.py")

    result = await run_session(tmp_path, SESSION[:2])

    assert result["turns"] == 2
    assert result["model_requests"] == 4
    assert result["reusable_prefix_tokens"] > 0
    assert result["errors"] == []
