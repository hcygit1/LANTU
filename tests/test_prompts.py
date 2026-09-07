from lantu.prompts import build_system_prompt


def test_build_system_prompt_includes_dynamic_context_protocol() -> None:
    prompt = build_system_prompt()
    assert "Dynamic context updates are cumulative" in prompt
