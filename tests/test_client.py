from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

from lantu.client import OpenAICompatClient
from lantu.config import ProviderConfig
from lantu.conversation import ConversationManager
from lantu.tools.base import StreamEnd


@pytest.mark.asyncio
async def test_openai_compat_accepts_ipv6_loopback_cidr_in_no_proxy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    value = "127.0.0.1,localhost,::1,127.0.0.0/8,::1/128"
    monkeypatch.setenv("NO_PROXY", value)
    monkeypatch.setenv("no_proxy", value)
    provider = ProviderConfig(
        name="deepseek",
        protocol="openai-compat",
        base_url="https://api.deepseek.com",
        model="deepseek-v4-pro",
        api_key="test-key",
    )

    client = OpenAICompatClient(provider)

    assert "::1/128" not in os.environ["NO_PROXY"]
    assert "::1/128" not in os.environ["no_proxy"]
    await client._client.close()


@pytest.mark.asyncio
async def test_openai_compat_preserves_output_length_stop_reason() -> None:
    provider = ProviderConfig(
        name="bailian",
        protocol="openai-compat",
        base_url="https://example.com/v1",
        model="glm-5.2",
        api_key="test-key",
    )
    client = OpenAICompatClient(provider)

    async def response_stream():
        yield SimpleNamespace(
            choices=[
                SimpleNamespace(
                    delta=SimpleNamespace(content=None, tool_calls=None),
                    finish_reason="length",
                )
            ],
            usage=None,
        )
        yield SimpleNamespace(
            choices=[],
            usage=SimpleNamespace(
                prompt_tokens=10,
                completion_tokens=8193,
                prompt_tokens_details=None,
            ),
        )

    async def create(**_kwargs):
        return response_stream()

    original_client = client._client
    client._client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )
    try:
        events = [event async for event in client.stream(ConversationManager())]
    finally:
        client._client = original_client
        await client.aclose()

    end = next(event for event in events if isinstance(event, StreamEnd))
    assert end.stop_reason == "max_tokens"


@pytest.mark.asyncio
async def test_openai_compat_passes_reasoning_effort_in_extra_body() -> None:
    provider = ProviderConfig(
        name="bailian",
        protocol="openai-compat",
        base_url="https://example.com/v1",
        model="glm-5.2",
        api_key="test-key",
        reasoning_effort="low",
    )
    client = OpenAICompatClient(provider)
    captured: dict = {}

    async def response_stream():
        yield SimpleNamespace(
            choices=[],
            usage=SimpleNamespace(
                prompt_tokens=1,
                completion_tokens=1,
                prompt_tokens_details=None,
            ),
        )

    async def create(**kwargs):
        captured.update(kwargs)
        return response_stream()

    original_client = client._client
    client._client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )
    try:
        _ = [event async for event in client.stream(ConversationManager())]
    finally:
        client._client = original_client
        await client.aclose()

    assert captured["extra_body"] == {
        "enable_thinking": True,
        "reasoning_effort": "low",
    }
