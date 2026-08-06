from __future__ import annotations

import os
import shutil
import socket
import subprocess
import time
from pathlib import Path


class CaptureUnavailable(RuntimeError):
    pass


class CaptureProxy:
    def __init__(
        self, work_dir: str | Path, port: int = 7788, session_id: str | None = None
    ) -> None:
        self.work_dir = Path(work_dir).resolve()
        self.port = port
        self.session_id = session_id
        self.process: subprocess.Popen[bytes] | None = None
        self._old_env: dict[str, str | None] = {}

    def start(self) -> None:
        executable = shutil.which("mitmdump")
        if executable is None:
            raise CaptureUnavailable("mitmdump is not installed; install lantu[capture]")
        ca_cert = Path.home() / ".mitmproxy" / "mitmproxy-ca-cert.pem"
        if not ca_cert.is_file():
            raise CaptureUnavailable(
                f"mitmproxy CA certificate not found: {ca_cert}; run mitmdump once first"
            )
        self._check_port()
        if self.session_id:
            error_path = (
                self.work_dir
                / ".lantu"
                / "lens"
                / "capture"
                / f"{self.session_id}.error"
            )
            error_path.unlink(missing_ok=True)
        addon = Path(__file__).parent / "addons" / "recorder.py"
        capture_dir = self.work_dir / ".lantu" / "lens" / "capture"
        env = os.environ.copy()
        env["LANTU_CAPTURE_DIR"] = str(capture_dir)
        self.process = subprocess.Popen(
            [
                executable,
                "--listen-host", "127.0.0.1",
                "--listen-port", str(self.port),
                "-s", str(addon),
                "--flow-detail", "0",
                "-q",
            ],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=None,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        proxy = f"http://127.0.0.1:{self.port}"
        for name, value in {
            "HTTP_PROXY": proxy,
            "HTTPS_PROXY": proxy,
            "SSL_CERT_FILE": str(ca_cert),
        }.items():
            self._old_env[name] = os.environ.get(name)
            os.environ[name] = value
        self._wait_until_ready()

    def stop(self) -> None:
        if self.process is not None:
            self.process.terminate()
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=3)
            self.process = None
        for name, old_value in self._old_env.items():
            if old_value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = old_value
        self._old_env.clear()

    def failure(self) -> str | None:
        if not self.session_id:
            return None
        path = (
            self.work_dir
            / ".lantu"
            / "lens"
            / "capture"
            / f"{self.session_id}.error"
        )
        try:
            return path.read_text(encoding="utf-8") if path.is_file() else None
        except OSError as exc:
            return str(exc)

    def _check_port(self) -> None:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            try:
                probe.bind(("127.0.0.1", self.port))
            except OSError as exc:
                raise CaptureUnavailable(f"capture port {self.port} is unavailable") from exc

    def _wait_until_ready(self) -> None:
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            if self.process is not None and self.process.poll() is not None:
                self.stop()
                raise CaptureUnavailable("mitmdump exited before becoming ready")
            try:
                with socket.create_connection(("127.0.0.1", self.port), timeout=0.1):
                    return
            except OSError:
                time.sleep(0.05)
        self.stop()
        raise CaptureUnavailable("mitmdump did not become ready within 3 seconds")
