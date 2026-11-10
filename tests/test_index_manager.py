from __future__ import annotations

from types import SimpleNamespace

import pytest

from lantu.mcp.index_manager import IndexManager


def result(data: object, error: bool = False) -> object:
    if isinstance(data, dict):
        return SimpleNamespace(is_error=error, content=[], structured_content=data)
    return SimpleNamespace(
        is_error=error,
        content=[SimpleNamespace(text=str(data))],
        structured_content=None,
    )


class FakeManager:
    def __init__(self, responses: list[object]) -> None:
        self.responses = iter(responses)
        self.root_key = "root"
        self.calls: list[tuple[str, dict]] = []

    async def get_client(self, server: str):
        return self

    async def list_tools(self):
        return [SimpleNamespace(name=name, input_schema={"properties": {
            **({self.root_key: {"type": "string"}} if self.root_key else {}),
            "wait": {"type": "boolean"},
        }}) for name in ("zvec_grep_index_status", "zvec_grep_index")]

    async def call_tool(self, server: str, name: str, arguments: dict) -> object:
        self.calls.append((name, arguments))
        response = next(self.responses)
        if isinstance(response, Exception):
            raise response
        return response


@pytest.mark.asyncio
async def test_ensure_ready_builds_missing_index() -> None:
    manager = FakeManager(
        [
            result({"indexed": False, "index_policy": "undecided"}),
            result("indexing started"),
            result({"indexed": True, "runtime": {"job_state": "succeeded"}}),
        ]
    )
    index = IndexManager(manager, poll_interval=0)

    status = await index.ensure_ready("/repo")

    assert status.state == "ready"
    assert [name for name, _ in manager.calls] == [
        "zvec_grep_index_status",
        "zvec_grep_index",
        "zvec_grep_index_status",
    ]
    assert manager.calls[1][1] == {"root": "/repo", "wait": True}


@pytest.mark.asyncio
async def test_ensure_ready_does_not_rebuild_ready_index() -> None:
    manager = FakeManager([result({"indexed": True, "runtime": {"job_state": "succeeded"}})])
    index = IndexManager(manager)

    status = await index.ensure_ready("/repo")

    assert status.state == "ready"
    assert [name for name, _ in manager.calls] == ["zvec_grep_index_status"]


@pytest.mark.asyncio
async def test_status_building_while_job_running() -> None:
    manager = FakeManager([result({"indexed": True, "runtime": {"job_state": "running"}})])
    index = IndexManager(manager)

    status = await index.status("/repo")

    assert status.state == "building"


@pytest.mark.asyncio
async def test_missing_index_tool_is_non_fatal() -> None:
    manager = FakeManager([RuntimeError("unknown tool")])
    index = IndexManager(manager)

    status = await index.ensure_ready("/repo")

    assert status.state == "unavailable"


@pytest.mark.asyncio
@pytest.mark.parametrize('job_state', ['running', 'pending', 'queued'])
async def test_first_build_waits_without_resubmitting(job_state):
    manager = FakeManager([
        result({'indexed': False, 'runtime': {'job_state': job_state}}),
        result({'indexed': True, 'runtime': {'job_state': 'succeeded'}}),
    ])
    status = await IndexManager(manager, poll_interval=0).ensure_ready('/repo')
    assert status.state == 'ready'
    assert all(name == 'zvec_grep_index_status' for name, _ in manager.calls)


@pytest.mark.asyncio
@pytest.mark.parametrize('data', ['not_initialized', {}, {'indexed': 'false'}, {'runtime': None}])
async def test_unknown_status_does_not_claim_ready_or_build(data):
    manager = FakeManager([result(data)])
    status = await IndexManager(manager).ensure_ready('/repo')
    assert status.state == 'unknown'
    assert len(manager.calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('root_key', ['root', 'path', 'repo'])
async def test_workspace_parameter_comes_from_schema(root_key, tmp_path):
    manager = FakeManager([result({'indexed': True})])
    manager.root_key = root_key
    assert (await IndexManager(manager).status(str(tmp_path))).state == 'ready'
    assert manager.calls[0][1] == {root_key: str(tmp_path.resolve())}


@pytest.mark.asyncio
async def test_no_workspace_parameter_never_calls_tool():
    manager = FakeManager([])
    manager.root_key = None
    status = await IndexManager(manager).ensure_ready('/repo')
    assert status.state == 'unavailable'
    assert not manager.calls


@pytest.mark.asyncio
async def test_relative_workspace_never_calls_tool():
    manager = FakeManager([])
    status = await IndexManager(manager).ensure_ready('relative/repo')
    assert status.state == 'unavailable'
    assert not manager.calls


@pytest.mark.asyncio
async def test_status_json_text_uses_structured_fields():
    # 冒烟报告中的字段形状，非完整原始响应。
    manager = FakeManager([result(
        '{"indexed":true,"runtime":{"job_state":"succeeded"},"watcher_active":true}'
    )])
    assert (await IndexManager(manager).status('/repo')).state == 'ready'


@pytest.mark.asyncio
async def test_remote_uses_agent_workspace_not_process_cwd(monkeypatch, tmp_path):
    from unittest.mock import AsyncMock, MagicMock
    from lantu.remote import RemoteServer
    from lantu.mcp.index_manager import IndexStatus

    manager = MagicMock()
    manager.register_all_tools = AsyncMock(return_value=SimpleNamespace(
        tools=[], servers=[], errors=[]))
    monkeypatch.setattr('lantu.remote.MCPManager', lambda: manager)
    ensure = AsyncMock(return_value=IndexStatus('ready'))
    monkeypatch.setattr(IndexManager, 'ensure_ready', ensure)
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    monkeypatch.chdir(tmp_path)
    server = RemoteServer(providers=[], mcp_servers=[SimpleNamespace()])
    server.registry = MagicMock()
    server.registry.get.return_value = None
    server.agent = SimpleNamespace(work_dir=str(workspace))
    await server._init_mcp()
    ensure.assert_awaited_once_with(str(workspace))


@pytest.mark.asyncio
@pytest.mark.parametrize('text_only', [False, True])
async def test_captured_zg_0_2_2_status(text_only):
    from pathlib import Path
    from mcp.types import CallToolResult

    fixture = Path(__file__).parent / 'fixtures/zg/index_status_0_2_2.json'
    response = CallToolResult.model_validate_json(fixture.read_text())
    if text_only:
        response.structured_content = None
    manager = FakeManager([response])
    status = await IndexManager(manager).ensure_ready('/tmp/astropy')
    assert status.state == 'ready'
    assert len(manager.calls) == 1
