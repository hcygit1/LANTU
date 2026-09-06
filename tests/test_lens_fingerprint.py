from lantu.conversation import ConversationManager, MessageKind
from lantu.tools.lens.fingerprint import (
    build_assembly_fingerprint,
    build_payload_fingerprint,
    compare_fingerprints,
)


def test_assembly_fingerprint_is_deterministic() -> None:
    conversation = ConversationManager()
    conversation.add_user_message("hello")
    tools = [{"name": "ReadFile", "input_schema": {"type": "object"}}]

    first = build_assembly_fingerprint("system", tools, conversation.get_messages())
    second = build_assembly_fingerprint("system", tools, conversation.get_messages())

    assert first == second


def test_compare_fingerprints_detects_append_only_messages() -> None:
    before = ConversationManager()
    before.add_user_message("hello")
    after = ConversationManager()
    after.add_user_message("hello")
    after.add_assistant_message("hi")

    previous = build_assembly_fingerprint("system", [], before.get_messages())
    current = build_assembly_fingerprint("system", [], after.get_messages())

    comparison = compare_fingerprints(previous, current)

    assert comparison.change == "append_only"
    assert comparison.common_message_count == 1
    assert comparison.first_divergence_message is None


def test_compare_fingerprints_detects_history_rewrite() -> None:
    before = ConversationManager()
    before.add_user_message("original")
    before.add_assistant_message("answer")
    after = ConversationManager()
    after.add_user_message("changed")
    after.add_assistant_message("answer")

    previous = build_assembly_fingerprint("system", [], before.get_messages())
    current = build_assembly_fingerprint("system", [], after.get_messages())

    comparison = compare_fingerprints(previous, current)

    assert comparison.change == "history_rewritten"
    assert comparison.first_divergence_message == 0


def test_fingerprint_reports_message_kind_without_hashing_it() -> None:
    conversation = ConversationManager()
    conversation.add_user_message("hello")
    conversation.history[0].reminder_key = "git_status"
    conversation.history[0].kind = MessageKind.APPENDIX
    conversation.history[0].appendix_key = "git_status"

    fingerprint = build_assembly_fingerprint("system", [], conversation.get_messages())

    assert fingerprint.messages[0].kind == "appendix"
    assert fingerprint.messages[0].appendix_key == "git_status"


def test_payload_fingerprint_excludes_trace_headers() -> None:
    first = build_payload_fingerprint({
        "model": "glm-5.2",
        "messages": [{"role": "user", "content": "hello"}],
        "extra_headers": {"X-LANTU-Model-Call-ID": "one"},
    })
    second = build_payload_fingerprint({
        "model": "glm-5.2",
        "messages": [{"role": "user", "content": "hello"}],
        "extra_headers": {"X-LANTU-Model-Call-ID": "two"},
    })

    assert first == second


def test_payload_fingerprint_detects_request_parameter_change() -> None:
    first = build_payload_fingerprint({
        "model": "glm-5.2",
        "messages": [{"role": "user", "content": "hello"}],
        "max_tokens": 100,
    })
    second = build_payload_fingerprint({
        "model": "glm-5.2",
        "messages": [{"role": "user", "content": "hello"}],
        "max_tokens": 200,
    })

    assert compare_fingerprints(first, second).change == "parameters_changed"
