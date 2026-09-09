from lantu.agent import Agent
from lantu.conversation import ConversationManager, MessageKind


def test_runtime_appendix_is_keyed_and_deduplicated(tmp_path) -> None:
    agent = Agent.__new__(Agent)
    agent.work_dir = str(tmp_path)
    agent.permission_mode = type("Mode", (), {"value": "default"})()
    agent._refresh_runtime_appendix = Agent._refresh_runtime_appendix.__get__(agent)

    conversation = ConversationManager()
    agent._refresh_runtime_appendix(conversation)
    assert conversation.flush_appendix()
    agent._refresh_runtime_appendix(conversation)
    assert conversation.flush_appendix() is False

    appendix = [m for m in conversation.history if m.kind == MessageKind.APPENDIX]
    assert [m.appendix_key for m in appendix] == ["context-update"]
    assert len(appendix) == 1
    assert 'key="git_status"' in appendix[0].content
    assert "task_progress" not in appendix[0].content


def test_runtime_appendix_does_not_append_when_git_state_is_unchanged(tmp_path) -> None:
    agent = Agent.__new__(Agent)
    agent.work_dir = str(tmp_path)
    agent.permission_mode = type("Mode", (), {"value": "default"})()
    agent._refresh_runtime_appendix = Agent._refresh_runtime_appendix.__get__(agent)

    conversation = ConversationManager()
    agent._refresh_runtime_appendix(conversation)
    assert conversation.flush_appendix()
    agent._refresh_runtime_appendix(conversation)
    assert conversation.flush_appendix() is False

    assert [m.appendix_key for m in conversation.history] == ["context-update"]
