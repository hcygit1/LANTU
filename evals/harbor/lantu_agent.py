"""Harbor adapter for running LANTU in Terminal-Bench containers.

Usage with Harbor::

    harbor run -d terminal-bench@2.0 \
      --agent-import-path evals.harbor:LantuAgent \
      -m openai/glm-5.2

The model is passed as ``provider/model``.  For an OpenAI-compatible endpoint
(including Bailian), pass ``OPENAI_API_KEY`` and ``OPENAI_BASE_URL`` through
Harbor's environment configuration.
"""

from __future__ import annotations

import json
import shlex
from pathlib import Path
from typing import override

from harbor.agents.installed.base import BaseInstalledAgent, with_prompt_template
from harbor.agents.model_connection import ModelConnectionSpec
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext

from .install import (
    container_proxy_url,
    discover_host_proxy,
    prepare_ca_bundle,
    prepare_install_assets,
    proxy_env,
)
from .parser import build_task_instruction, parse_metrics
from .settings import resolve_reasoning_effort


LANTU_REPOSITORY = "https://github.com/hcygit1/LANTU.git"
LANTU_COMMIT = "00a4ea0317c14c2c44cfc736a85408157a016442"
DEFAULT_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
class LantuAgent(BaseInstalledAgent):
    """Run the LANTU CLI in non-interactive, stream-json mode."""

    MODEL_CONNECTION = ModelConnectionSpec(passthrough=True)
    _OUTPUT_FILENAME = "lantu-stream.jsonl"
    _STDERR_FILENAME = "lantu.stderr.log"

    @staticmethod
    @override
    def name() -> str:
        return "lantu"

    @override
    async def install(self, environment: BaseEnvironment) -> None:
        """Install uv and the pinned LANTU revision for reproducible trials."""
        host_proxy = discover_host_proxy(self._get_env)
        container_proxy = (
            host_proxy.replace("127.0.0.1", "host.docker.internal")
            .replace("localhost", "host.docker.internal")
            if host_proxy
            else None
        )
        uv_binary, source_archive = prepare_install_assets(
            Path(__file__).resolve().parents[2], LANTU_COMMIT, host_proxy
        )
        await environment.upload_file(uv_binary, "/tmp/lantu-uv")
        await environment.upload_file(source_archive, "/tmp/lantu-source.tar")
        await environment.upload_file(prepare_ca_bundle(), "/tmp/lantu-ca-bundle.pem")
        install_env = proxy_env(container_proxy)
        install_env.update(
            {
                "SSL_CERT_FILE": "/tmp/lantu-ca-bundle.pem",
                "REQUESTS_CA_BUNDLE": "/tmp/lantu-ca-bundle.pem",
                "CURL_CA_BUNDLE": "/tmp/lantu-ca-bundle.pem",
                "UV_TLS_CACERT": "/tmp/lantu-ca-bundle.pem",
            }
        )
        # Harbor merges this environment into every later exec, including the
        # task verifier, so all phases share one proxy configuration.
        environment._persistent_env.update(install_env)
        install_env["UV_HTTP_TIMEOUT"] = "60"
        await self.exec_as_agent(
            environment,
            command=(
                "set -euo pipefail; "
                "chmod +x /tmp/lantu-uv; "
                "rm -rf /tmp/lantu-source; mkdir -p /tmp/lantu-source; "
                "tar -xf /tmp/lantu-source.tar -C /tmp/lantu-source; "
                "timeout 300 /tmp/lantu-uv tool install --force "
                "--from /tmp/lantu-source lantu; "
                'export PATH="$HOME/.local/bin:$PATH"; '
                "lantu --help >/dev/null"
            ),
            env=install_env,
            timeout_sec=360,
        )

    @override
    @with_prompt_template
    async def run(
        self,
        instruction: str,
        environment: BaseEnvironment,
        context: AgentContext,
    ) -> None:
        if not self.model_name or "/" not in self.model_name:
            raise ValueError("Model name must be in the format provider/model_name")

        _, model = self.model_name.split("/", 1)
        access = self.model_connection
        api_key = access.api_key or self._get_env("OPENAI_API_KEY") or ""
        base_url = (
            self._get_env("LANTU_BASE_URL")
            or access.configured_base_url
            or DEFAULT_BASE_URL
        )
        output_path = f"/logs/agent/{self._OUTPUT_FILENAME}"
        stderr_path = f"/logs/agent/{self._STDERR_FILENAME}"
        config_home = "/tmp/lantu-home"
        config_path = f"{config_home}/.lantu/config.yaml"
        reasoning_effort = resolve_reasoning_effort(
            self._get_env("LANTU_REASONING_EFFORT")
        )
        config = {
            "providers": [
                {
                    "name": "harbor",
                    "protocol": "openai-compat",
                    "base_url": base_url,
                    "model": model,
                    "api_key": "${LANTU_API_KEY}",
                    "reasoning_effort": reasoning_effort,
                    "max_output_tokens": 32000,
                }
            ],
            "permission_mode": "bypassPermissions",
        }
        host_proxy = discover_host_proxy(self._get_env)
        container_proxy = container_proxy_url(host_proxy) if host_proxy else None
        await environment.upload_file(prepare_ca_bundle(), "/tmp/lantu-ca-bundle.pem")
        env = {
            **access.env,
            **proxy_env(container_proxy),
            "SSL_CERT_FILE": "/tmp/lantu-ca-bundle.pem",
            "REQUESTS_CA_BUNDLE": "/tmp/lantu-ca-bundle.pem",
            "CURL_CA_BUNDLE": "/tmp/lantu-ca-bundle.pem",
            "LANTU_API_KEY": api_key,
        }
        config_text = json.dumps(config, ensure_ascii=False)
        write_config = (
            f"mkdir -p {shlex.quote(config_home + '/.lantu')}; "
            f"printf '%s' {shlex.quote(config_text)} > {shlex.quote(config_path)}"
        )
        escaped_instruction = shlex.quote(build_task_instruction(instruction))

        await self.exec_as_agent(
            environment,
            command=(
                f"export PATH=\"$HOME/.local/bin:$PATH\"; "
                f"LANTU_BIN=\"$(command -v lantu)\"; "
                f"mkdir -p /logs/agent; {write_config}; "
                f"HOME={shlex.quote(config_home)} \"$LANTU_BIN\" "
                f"--mode bypassPermissions --output-format stream-json "
                f"-p {escaped_instruction} "
                f"2>{shlex.quote(stderr_path)} | tee {shlex.quote(output_path)}; "
                "agent_status=${PIPESTATUS[0]}; "
                # Keep LANTU's source-of-truth journal with Harbor artifacts.
                "if [ -d .lantu/sessions ]; then "
                "  mkdir -p /logs/agent/sessions; "
                "  cp -a .lantu/sessions/. /logs/agent/sessions/; "
                "fi; "
                "exit $agent_status"
            ),
            env=env,
        )

    @override
    def populate_context_post_run(self, context: AgentContext) -> None:
        metrics = parse_metrics(self.logs_dir / self._OUTPUT_FILENAME)
        context.n_input_tokens = metrics.input_tokens
        context.n_output_tokens = metrics.output_tokens
        context.n_cache_tokens = metrics.cache_tokens
        if metrics.duration_ms:
            context.metadata = {
                **(context.metadata or {}),
                "duration_ms": metrics.duration_ms,
            }
