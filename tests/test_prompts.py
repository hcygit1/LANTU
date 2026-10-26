import re

from lantu.prompts import build_system_prompt
from lantu.tools import create_default_registry
from lantu.tools.impl.tool_search import ToolSearchTool


def test_build_system_prompt_includes_dynamic_context_protocol() -> None:
    prompt = build_system_prompt()
    assert "Dynamic context updates are cumulative" in prompt


def _registry_with_tool_search(loading_mode: str):
    registry = create_default_registry(loading_mode=loading_mode)
    registry.register(ToolSearchTool(registry, protocol="anthropic"))
    return registry


def test_prompt_only_names_tools_visible_in_both_loading_modes() -> None:
    """系统提示词提到的工具，在两种加载模式下都必须对模型可见。

    提示词按名字点名工具，就等于向模型承诺「这个工具你能用」。标准模式不会
    下发被隐藏工具的 schema，所以点名一个隐藏工具就会让模型去找一个它拿不到
    的东西（历史上 Grep / ToolSearch 就踩过这个坑）。
    """
    prompt = build_system_prompt()
    registries = {
        mode: _registry_with_tool_search(mode)
        for mode in ("standard", "progressive")
    }
    named = {
        tool.name
        for tool in registries["standard"].list_tools()
        if re.search(rf"\b{re.escape(tool.name)}\b", prompt)
    }
    # 防止解析失效导致用例空转
    assert "Grep" in named
    assert "SemanticSearch" in named

    for name in sorted(named):
        for mode, registry in registries.items():
            assert registry.is_model_visible(name), (
                f"提示词点名了 '{name}'，但它在 {mode} 模式下对模型不可见"
            )


def test_standard_mode_hidden_tool_cannot_be_called_by_name() -> None:
    """标准模式下未公示的工具，模型不能凭名字猜着调用。"""
    registry = _registry_with_tool_search("standard")

    assert registry.is_model_visible("Grep")
    assert registry.is_model_visible("SemanticSearch")
    assert not registry.is_model_visible("ToolSearch")
