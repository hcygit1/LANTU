from lantu.agent import Agent
from lantu.conversation import ConversationManager, MessageKind


def test_runtime_appendix_is_keyed_and_deduplicated(tmp_path) -> None:
    agent = Agent.__new__(Agent)
    agent.work_dir = str(tmp_path)
    agent.permission_mode = type("Mode", (), {"value": "default"})()
    agent._refresh_runtime_appendix = Agent._refresh_runtime_appendix.__get__(agent)

    conversation = ConversationManager()
    agent._refresh_runtime_appendix(conversation, 1)
    agent._refresh_runtime_appendix(conversation, 1)

    appendix = [m for m in conversation.history if m.kind == MessageKind.APPENDIX]
    assert [m.appendix_key for m in appendix] == ["git_status", "task_progress"]
    assert len(appendix) == 2


def test_runtime_appendix_changes_only_task_progress(tmp_path) -> None:
    agent = Agent.__new__(Agent)
    agent.work_dir = str(tmp_path)
    agent.permission_mode = type("Mode", (), {"value": "default"})()
    agent._refresh_runtime_appendix = Agent._refresh_runtime_appendix.__get__(agent)

    conversation = ConversationManager()
    agent._refresh_runtime_appendix(conversation, 1)
    agent._refresh_runtime_appendix(conversation, 2)

    assert [m.appendix_key for m in conversation.history] == [
        "git_status",
        "task_progress",
        "task_progress",
    ]
