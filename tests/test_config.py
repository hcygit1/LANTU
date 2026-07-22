from __future__ import annotations

import pytest

from lantu.config import ProviderConfig


def make_provider(api_key: str) -> ProviderConfig:
    return ProviderConfig(
        name="deepseek",
        protocol="openai-compat",
        base_url="https://api.deepseek.com",
        model="deepseek-chat",
        api_key=api_key,
    )


def test_resolve_api_key_expands_explicit_environment_template(monkeypatch) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "secret-key")

    assert make_provider("${DEEPSEEK_API_KEY}").resolve_api_key() == "secret-key"


def test_resolve_api_key_returns_empty_for_missing_explicit_environment(
    monkeypatch,
) -> None:
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)

    assert make_provider("${DEEPSEEK_API_KEY}").resolve_api_key() == ""


@pytest.mark.parametrize(
    ("api_key", "environment"),
    [
        ("prefix-${MISSING_KEY}", {}),
        ("${PRESENT_KEY}-${MISSING_KEY}", {"PRESENT_KEY": "value"}),
        ("${OUTER_KEY}", {"OUTER_KEY": "${INNER_KEY}"}),
    ],
)
def test_resolve_api_key_rejects_any_remaining_environment_template(
    monkeypatch, api_key, environment
) -> None:
    for name in ("MISSING_KEY", "PRESENT_KEY", "OUTER_KEY", "INNER_KEY"):
        monkeypatch.delenv(name, raising=False)
    for name, value in environment.items():
        monkeypatch.setenv(name, value)

    assert make_provider(api_key).resolve_api_key() == ""


def test_resolve_api_key_keeps_literal_key(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "fallback-key")

    assert make_provider("literal-key").resolve_api_key() == "literal-key"


def test_openai_compat_empty_api_key_uses_protocol_environment(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "fallback-key")

    assert make_provider("").resolve_api_key() == "fallback-key"
