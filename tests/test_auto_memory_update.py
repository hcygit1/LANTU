import json
from pathlib import Path

import pytest

from lantu.conversation import ConversationManager, Message
from lantu.memory.auto_memory import MemoryManager, parse_frontmatter
from lantu.tools.base import TextDelta


@pytest.fixture
def memory(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, 'home', classmethod(lambda cls: tmp_path / 'home'))
    manager = MemoryManager(str(tmp_path / 'project'))
    manager.load()
    path = manager.project_mem_dir / 'database.md'
    path.write_text('---\nname: database\ndescription: database setup\ntype: project\n---\n\nUses SQLite.\nBackups at midnight.\n', encoding='utf-8')
    index = path.parent / 'MEMORY.md'
    index.write_text('- [database](database.md) — database setup\n', encoding='utf-8')
    return manager, path, index


class Client:
    def __init__(self, merged, hook=None, target='database.md'):
        self.prompts = []
        self.merged = merged
        self.hook = hook
        self.target = target

    async def stream(self, conversation, **kwargs):
        self.prompts.append(conversation.history[0].content)
        if len(self.prompts) == 1:
            yield TextDelta(text=f'MEMORY_ACTION: update\nMEMORY_TARGET: {self.target}\nMEMORY_NAME: database-new\nMEMORY_TYPE: project\nMEMORY_DESC: updated database\nMEMORY_BODY: Uses PostgreSQL.\n---')
        else:
            if self.hook:
                self.hook()
            if isinstance(self.merged, Exception):
                raise self.merged
            yield TextDelta(text=self.merged)


async def extract(manager, client, message="We now use PostgreSQL instead of SQLite."):
    conversation = ConversationManager()
    conversation.history = [Message(role='user', content=message)]
    await manager.extract(client, conversation, 'openai')


def result(body='Uses PostgreSQL.\nBackups at midnight.', **changes):
    return json.dumps(dict(name='database-new', description='updated database', type='reference', body=body, **changes))


@pytest.mark.asyncio
async def test_update_preserves_details_and_corrects_conclusion(memory):
    manager, path, index = memory
    client = Client(result())
    await extract(manager, client)
    text = path.read_text()
    assert 'Backups at midnight.' in text
    assert 'Uses PostgreSQL.' in text
    assert 'SQLite' not in text
    assert len(client.prompts) == 2
    assert 'Backups at midnight.' not in client.prompts[0]
    assert 'Backups at midnight.' in client.prompts[1]
    assert 'SQLite' in client.prompts[1]
    assert parse_frontmatter(text).name == 'database-new'
    assert parse_frontmatter(text).type == 'reference'
    assert index.read_text() == '- [database-new](database.md) — updated database\n'


@pytest.mark.asyncio
async def test_duplicate_information_is_merged_once(memory):
    manager, path, _ = memory
    client = Client(result('Uses PostgreSQL.\nBackups at midnight.'))
    await extract(manager, client, 'We now use PostgreSQL. Backups at midnight. Backups at midnight.')
    # 合并协议要求去重；完整输出（包括第二行）应原样落盘。
    assert 'Remove duplicate information' in client.prompts[1]
    assert path.read_text().count('Backups at midnight.') == 1
    assert 'explicitly corrects or replaces' in client.prompts[1]
    assert 'even when not mentioned' in client.prompts[1]


@pytest.mark.asyncio
@pytest.mark.parametrize('response', [RuntimeError('offline'), 'invalid', '{}', '[]',
    '{"name":"x","description":"x","type":"project","body":""}',
    '{"name":"x","description":"x","type":"unknown","body":"x"}',
    '{"name":"x\\ntype: user","description":"x","type":"project","body":"x"}'])
async def test_failed_merge_preserves_files(memory, response):
    manager, path, index = memory
    before = path.read_bytes(), index.read_bytes()
    await extract(manager, Client(response))
    assert (path.read_bytes(), index.read_bytes()) == before
    assert sorted(p.name for p in path.parent.glob('*.md')) == ['MEMORY.md', 'database.md']


@pytest.mark.asyncio
@pytest.mark.parametrize('target', ['missing.md', '', '../database.md', 'MEMORY.md'])
async def test_invalid_target_never_creates_duplicate(memory, target):
    manager, path, index = memory
    before = path.read_bytes(), index.read_bytes()
    client = Client(result(), target=target)
    await extract(manager, client)
    assert (path.read_bytes(), index.read_bytes()) == before
    assert len(client.prompts) == 1
    assert not (path.parent / 'database-new.md').exists()


@pytest.mark.asyncio
@pytest.mark.parametrize('changed', ['body', 'index', 'deleted'])
async def test_concurrent_changes_are_not_overwritten(memory, changed):
    manager, path, index = memory
    def mutate():
        if changed == 'deleted':
            path.unlink()
        else:
            (path if changed == 'body' else index).write_text('new concurrent content')
    expected = {}
    def hook():
        mutate()
        expected['body'] = path.read_bytes() if path.exists() else None
        expected['index'] = index.read_bytes()
    await extract(manager, Client(result(), hook=hook))
    assert (path.read_bytes() if path.exists() else None) == expected['body']
    assert index.read_bytes() == expected['index']


@pytest.mark.asyncio
async def test_index_write_failure_rolls_back_body(memory, monkeypatch):
    import os
    manager, path, index = memory
    before = path.read_bytes(), index.read_bytes()
    replace = os.replace
    def fail_index(source, destination):
        if destination == index:
            raise OSError('index unavailable')
        replace(source, destination)
    monkeypatch.setattr(os, 'replace', fail_index)
    await extract(manager, Client(result()))
    assert (path.read_bytes(), index.read_bytes()) == before
    assert not list(path.parent.glob('.memory-*'))


@pytest.mark.asyncio
async def test_ambiguous_target_is_skipped(memory):
    manager, path, index = memory
    other = manager.user_mem_dir / path.name
    other.write_text('User-level memory')
    before = path.read_bytes(), index.read_bytes(), other.read_bytes()
    client = Client(result())
    await extract(manager, client)
    assert (path.read_bytes(), index.read_bytes(), other.read_bytes()) == before
    assert len(client.prompts) == 1


@pytest.mark.asyncio
async def test_type_change_keeps_existing_directory(memory):
    manager, path, index = memory
    merged = json.loads(result())
    merged['type'] = 'user'
    await extract(manager, Client(json.dumps(merged)))
    assert parse_frontmatter(path.read_text()).type == 'user'
    assert 'database-new' in index.read_text()
    assert not (manager.user_mem_dir / path.name).exists()


@pytest.mark.asyncio
async def test_recorder_records_both_requests(memory):
    manager, path, _ = memory
    client = Client(result())
    class Recorder:
        def __init__(self):
            self.calls = []
        def stream(self, client, conversation, **kwargs):
            self.calls.append(kwargs.pop('call_kind'))
            return client.stream(conversation, **kwargs)
    recorder = Recorder()
    conversation = ConversationManager()
    conversation.history = [Message(role='user', content='Use PostgreSQL.')]
    await manager.extract(client, conversation, 'openai', recorder)
    assert recorder.calls == ['memory_extraction', 'memory_extraction']
    assert 'Backups at midnight.' in path.read_text()


@pytest.mark.asyncio
@pytest.mark.parametrize('mtype', ['user', 'feedback', 'project', 'reference'])
async def test_create_routing_and_multiline_body(memory, mtype):
    manager, _, _ = memory
    class Creator:
        async def stream(self, *args, **kwargs):
            yield TextDelta(text=f'MEMORY_ACTION: create\nMEMORY_TARGET:\nMEMORY_NAME: new-memory\nMEMORY_TYPE: {mtype}\nMEMORY_DESC: new memory\nMEMORY_BODY: first line\nsecond line\n---')
    await extract(manager, Creator())
    root = manager.user_mem_dir if mtype in {'user', 'feedback'} else manager.project_mem_dir
    assert 'first line\nsecond line' in (root / 'new-memory.md').read_text()
    assert 'new-memory.md' in (root / 'MEMORY.md').read_text()


@pytest.mark.asyncio
async def test_create_cannot_overwrite_existing_memory(memory):
    manager, path, index = memory
    before = path.read_bytes(), index.read_bytes()
    class Creator:
        async def stream(self, *args, **kwargs):
            yield TextDelta(text='MEMORY_ACTION: create\nMEMORY_TARGET:\nMEMORY_NAME: database\nMEMORY_TYPE: project\nMEMORY_DESC: new\nMEMORY_BODY: replacement\n---')
    await extract(manager, Creator())
    assert (path.read_bytes(), index.read_bytes()) == before
