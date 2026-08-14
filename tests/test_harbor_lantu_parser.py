from pathlib import Path

import pytest

from evals.harbor.install import (
    container_proxy_url,
    discover_host_proxy,
    discover_install_proxy,
    prepare_ca_bundle,
    proxy_env,
)
from evals.harbor.parser import build_task_instruction, parse_metrics


def test_parse_metrics_prefers_final_result(tmp_path: Path) -> None:
    output = tmp_path / "stream.jsonl"
    output.write_text(
        '{"type":"usage","input_tokens":10,"output_tokens":2}\n'
        '{"type":"result","duration_ms":1234,"usage":{"input_tokens":42,"output_tokens":7}}\n',
        encoding="utf-8",
    )

    assert parse_metrics(output).input_tokens == 42
    assert parse_metrics(output).output_tokens == 7
    assert parse_metrics(output).duration_ms == 1234


def test_parse_metrics_ignores_invalid_lines_and_falls_back_to_usage(tmp_path: Path) -> None:
    output = tmp_path / "stream.jsonl"
    output.write_text(
        "banner\nnot-json\n"
        '{"type":"usage","input_tokens":3,"output_tokens":4}\n',
        encoding="utf-8",
    )

    metrics = parse_metrics(output)
    assert (metrics.input_tokens, metrics.output_tokens, metrics.duration_ms) == (3, 4, 0)


def test_parse_metrics_missing_file_is_empty(tmp_path: Path) -> None:
    metrics = parse_metrics(tmp_path / "missing.jsonl")
    assert metrics.input_tokens == metrics.output_tokens == metrics.duration_ms == 0


def test_build_task_instruction_adds_completion_boundary() -> None:
    prompt = build_task_instruction("Write /app/answer.txt")

    assert prompt.startswith("Write /app/answer.txt\n\n")
    assert "Complete only the requested task." in prompt
    assert "stop immediately" in prompt
    assert "Do not add extra tests" in prompt


def test_container_proxy_translates_host_loopback() -> None:
    assert (
        container_proxy_url("http://127.0.0.1:7897")
        == "http://host.docker.internal:7897"
    )
    assert (
        container_proxy_url("http://user:pass@localhost:7890")
        == "http://user:pass@host.docker.internal:7890"
    )


def test_container_proxy_preserves_remote_proxy() -> None:
    assert container_proxy_url("http://proxy.example:8080") == "http://proxy.example:8080"


def test_container_proxy_requires_port() -> None:
    with pytest.raises(ValueError, match="include a port"):
        container_proxy_url("http://localhost")


def test_discover_install_proxy_prefers_explicit_setting() -> None:
    values = {"LANTU_INSTALL_PROXY": "127.0.0.1:7897"}
    assert discover_host_proxy(values.get) == "http://127.0.0.1:7897"
    assert discover_install_proxy(values.get) == "http://host.docker.internal:7897"


def test_proxy_env_sets_upper_and_lower_case() -> None:
    proxy = "http://host.docker.internal:7897"
    assert proxy_env(proxy) == {
        "HTTP_PROXY": proxy,
        "HTTPS_PROXY": proxy,
        "http_proxy": proxy,
        "https_proxy": proxy,
        "NO_PROXY": (
            "localhost,127.0.0.1,::1,archive.ubuntu.com,security.ubuntu.com,"
            ".aliyuncs.com"
        ),
        "no_proxy": (
            "localhost,127.0.0.1,::1,archive.ubuntu.com,security.ubuntu.com,"
            ".aliyuncs.com"
        ),
    }


def test_prepare_ca_bundle_contains_public_roots() -> None:
    bundle = prepare_ca_bundle()
    content = bundle.read_text(encoding="ascii")
    assert content.count("-----BEGIN CERTIFICATE-----") > 100
