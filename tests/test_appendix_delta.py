from lantu.conversation import ConversationManager, MessageKind


def test_appendix_delta_groups_changes_and_keeps_old_keys() -> None:
    conversation = ConversationManager()
    conversation.set_appendix_block("git_status", "clean")
    conversation.set_appendix_block("task_progress", "iteration=1")
    assert conversation.flush_appendix()
    first = conversation.history[-1].content
    assert 'mode="baseline"' in first
    assert 'key="git_status"' in first
    assert 'key="task_progress"' in first

    conversation.set_appendix_block("task_progress", "iteration=2")
    assert conversation.flush_appendix()
    second = conversation.history[-1].content
    assert 'mode="delta"' in second
    assert 'key="task_progress"' in second
    assert 'key="git_status"' not in second


def test_appendix_delta_rebuilds_keys_after_restore() -> None:
    original = ConversationManager()
    original.set_appendix_block("git_status", "clean")
    original.flush_appendix()
    restored = ConversationManager(history=list(original.history))
    restored.set_appendix_block("git_status", "clean")
    assert restored.flush_appendix() is False
    restored.set_appendix_block("git_status", "modified")
    assert restored.flush_appendix() is True
    assert 'mode="delta"' in restored.history[-1].content


def test_appendix_reset_sends_baseline_again() -> None:
    conversation = ConversationManager()
    conversation.set_appendix_block("status", "one")
    conversation.flush_appendix()
    conversation.reset_appendix_baseline()
    conversation.set_appendix_block("status", "one")
    assert conversation.flush_appendix()
    assert 'mode="baseline"' in conversation.history[-1].content
