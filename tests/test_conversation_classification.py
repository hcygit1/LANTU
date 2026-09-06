from lantu.conversation import ConversationManager, MessageKind
from lantu.tools.lens.fingerprint import build_assembly_fingerprint


def test_regular_messages_are_frozen_and_append_in_order() -> None:
    conversation = ConversationManager()
    conversation.add_user_message("question")
    conversation.add_assistant_message("answer")

    assert [message.kind for message in conversation.history] == [
        MessageKind.FROZEN,
        MessageKind.FROZEN,
    ]
    assert [message.content for message in conversation.get_messages()] == [
        "question",
        "answer",
    ]


def test_appendix_is_keyed_and_only_appends_when_changed() -> None:
    conversation = ConversationManager()

    assert conversation.add_appendix("git_status", "clean")
    assert not conversation.add_appendix("git_status", "clean")
    assert conversation.add_appendix("git_status", "modified")

    assert [message.kind for message in conversation.history] == [
        MessageKind.APPENDIX,
        MessageKind.APPENDIX,
    ]
    assert [message.content for message in conversation.history] == [
        "clean",
        "modified",
    ]


def test_ephemeral_is_not_added_to_history() -> None:
    conversation = ConversationManager()
    conversation.add_user_message("question")
    conversation.add_ephemeral("one request hint")

    assert len(conversation.history) == 1
    assert [message.kind for message in conversation.get_messages()] == [
        MessageKind.FROZEN,
        MessageKind.EPHEMERAL,
    ]

    conversation.clear_ephemeral()
    assert len(conversation.get_messages()) == 1


def test_classification_metadata_does_not_change_wire_fingerprint() -> None:
    first = ConversationManager()
    first.add_user_message("same")

    second = ConversationManager()
    second.add_user_message("same")
    second.history[0].kind = MessageKind.APPENDIX
    second.history[0].appendix_key = "status"

    left = build_assembly_fingerprint("system", [], first.get_messages())
    right = build_assembly_fingerprint("system", [], second.get_messages())

    assert left.digest == right.digest
    assert left.messages[0].kind == MessageKind.FROZEN.value
    assert right.messages[0].kind == MessageKind.APPENDIX.value
