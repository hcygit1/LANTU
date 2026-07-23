from __future__ import annotations

import os

import pytest

from lantu.client import OpenAICompatClient
from lantu.config import ProviderConfig


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
