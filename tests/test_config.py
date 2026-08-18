from __future__ import annotations

import pytest

from lantu.config import AppConfig, ProviderConfig, UIConfig, _merge_config, load_config
from lantu.validator import ConfigError, validate_config_structure, validate_providers


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


def test_validate_provider_accepts_low_reasoning_effort() -> None:
    providers = validate_providers(
        [{
            "name": "bailian",
            "protocol": "openai-compat",
            "base_url": "https://example.com/v1",
            "model": "glm-5.2",
            "reasoning_effort": "low",
        }]
    )

    assert providers[0]["reasoning_effort"] == "low"


def test_validate_provider_rejects_unknown_reasoning_effort() -> None:
    with pytest.raises(ConfigError, match="reasoning_effort"):
        validate_providers(
            [{
                "name": "bailian",
                "protocol": "openai-compat",
                "base_url": "https://example.com/v1",
                "model": "glm-5.2",
                "reasoning_effort": "extreme",
            }]
        )


def test_tool_loading_mode_defaults_to_standard(tmp_path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text(
        """
providers:
  - name: test
    protocol: openai-compat
    base_url: https://example.com/v1
    model: test-model
""".strip(),
        encoding="utf-8",
    )

    assert load_config(path).tool_loading_mode == "standard"


@pytest.mark.parametrize("mode", ["standard", "progressive"])
def test_tool_loading_mode_accepts_supported_values(tmp_path, mode) -> None:
    path = tmp_path / "config.yaml"
    path.write_text(
        f"""
providers:
  - name: test
    protocol: openai-compat
    base_url: https://example.com/v1
    model: test-model
tool_loading_mode: {mode}
""".strip(),
        encoding="utf-8",
    )

    assert load_config(path).tool_loading_mode == mode


def test_tool_loading_mode_rejects_unknown_value() -> None:
    with pytest.raises(ConfigError, match="tool_loading_mode"):
        validate_config_structure(
            {
                "providers": [
                    {
                        "name": "test",
                        "protocol": "openai-compat",
                        "base_url": "https://example.com/v1",
                        "model": "test-model",
                    }
                ],
                "tool_loading_mode": "lazy",
            }
        )


def test_load_config_defaults_to_hidden_thinking(tmp_path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text(
        """
providers:
  - name: test
    protocol: openai-compat
    base_url: https://example.com/v1
    model: test-model
""".strip(),
        encoding="utf-8",
    )

    assert load_config(path).ui.show_thinking is False


def test_load_config_accepts_show_thinking(tmp_path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text(
        """
providers:
  - name: test
    protocol: openai-compat
    base_url: https://example.com/v1
    model: test-model
ui:
  show_thinking: true
""".strip(),
        encoding="utf-8",
    )

    assert load_config(path).ui.show_thinking is True


def test_repo_map_defaults_to_disabled(tmp_path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text(
        """
providers:
  - name: test
    protocol: openai-compat
    base_url: https://example.com/v1
    model: test-model
""".strip(),
        encoding="utf-8",
    )

    config = load_config(path)

    assert config.context.repo_map.enabled is False
    assert config.context.repo_map.max_tokens == 4_000


def test_repo_map_accepts_enabled_and_token_budget(tmp_path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text(
        """
providers:
  - name: test
    protocol: openai-compat
    base_url: https://example.com/v1
    model: test-model
context:
  repo_map:
    enabled: true
    max_tokens: 2500
""".strip(),
        encoding="utf-8",
    )

    config = load_config(path)

    assert config.context.repo_map.enabled is True
    assert config.context.repo_map.max_tokens == 2_500


def test_repo_map_rejects_invalid_token_budget() -> None:
    with pytest.raises(ConfigError, match="context.repo_map.max_tokens"):
        validate_config_structure(
            {
                "providers": [
                    {
                        "name": "test",
                        "protocol": "openai-compat",
                        "base_url": "https://example.com/v1",
                        "model": "test-model",
                    }
                ],
                "context": {"repo_map": {"max_tokens": 0}},
            }
        )


def test_validate_config_rejects_non_boolean_show_thinking() -> None:
    with pytest.raises(ConfigError, match="ui.show_thinking"):
        validate_config_structure(
            {
                "providers": [
                    {
                        "name": "test",
                        "protocol": "openai-compat",
                        "base_url": "https://example.com/v1",
                        "model": "test-model",
                    }
                ],
                "ui": {"show_thinking": "yes"},
            }
        )


def test_explicit_lower_layer_can_disable_show_thinking() -> None:
    base = AppConfig(
        providers=[],
        ui=UIConfig(
            show_thinking=True,
            _explicit_fields=frozenset({"show_thinking"}),
        ),
    )
    override = AppConfig(
        providers=[],
        ui=UIConfig(
            show_thinking=False,
            _explicit_fields=frozenset({"show_thinking"}),
        ),
    )

    assert _merge_config(base, override).ui.show_thinking is False
