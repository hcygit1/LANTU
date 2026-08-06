"""mitmproxy addon for LANTU model traffic.

This file runs in the mitmdump process and intentionally imports no LANTU code.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from mitmproxy import http


OUTPUT_DIR = Path(os.environ.get("LANTU_CAPTURE_DIR", ".lantu/lens/capture"))
MAX_BODY_BYTES = int(os.environ.get("LANTU_CAPTURE_MAX_BODY_BYTES", str(512 * 1024)))
SENSITIVE_HEADERS = {
    "authorization",
    "cookie",
    "set-cookie",
    "x-api-key",
    "proxy-authorization",
}


class LantuRecorder:
    def __init__(self) -> None:
        self._identity: dict[str, tuple[str, str]] = {}
        self._sse: dict[str, bytearray] = {}

    def request(self, flow: http.HTTPFlow) -> None:
        call_id = flow.request.headers.get("X-LANTU-Model-Call-ID", "").strip()
        session_id = flow.request.headers.get("X-LANTU-Session-ID", "").strip()
        if not call_id or not session_id:
            return
        self._identity[flow.id] = (session_id, call_id)
        flow.request.headers.pop("X-LANTU-Model-Call-ID", None)
        flow.request.headers.pop("X-LANTU-Session-ID", None)

    def responseheaders(self, flow: http.HTTPFlow) -> None:
        if flow.id not in self._identity or flow.response is None:
            return
        if "text/event-stream" not in flow.response.headers.get("content-type", ""):
            return
        self._sse[flow.id] = bytearray()

        def capture_chunk(chunk: bytes) -> bytes:
            buffer = self._sse.get(flow.id)
            if buffer is not None and len(buffer) < MAX_BODY_BYTES:
                buffer.extend(chunk[: MAX_BODY_BYTES - len(buffer)])
            return chunk

        flow.response.stream = capture_chunk

    def response(self, flow: http.HTTPFlow) -> None:
        identity = self._identity.pop(flow.id, None)
        if identity is None or flow.response is None:
            return
        session_id, call_id = identity
        request_body = _body(flow.request.content)
        streamed = self._sse.pop(flow.id, None)
        response_body = _body(bytes(streamed) if streamed is not None else flow.response.content)
        request_json = _json_object(request_body)
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "session_id": session_id,
            "model_call_id": call_id,
            "provider": _provider(flow.request.pretty_host),
            "model": request_json.get("model") if request_json else None,
            "request": {
                "method": flow.request.method,
                "url": flow.request.pretty_url,
                "headers": _headers(flow.request.headers),
                "body": request_body,
            },
            "response": {
                "status": flow.response.status_code,
                "headers": _headers(flow.response.headers),
                "body": response_body,
            },
        }
        try:
            OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
            with (OUTPUT_DIR / f"{session_id}.jsonl").open(
                "a", encoding="utf-8", newline="\n"
            ) as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
                handle.flush()
        except OSError as exc:
            error_path = OUTPUT_DIR / f"{session_id}.error"
            try:
                error_path.write_text(str(exc), encoding="utf-8")
            except OSError:
                pass
            print(f"[lantu capture] write failed: {exc}", file=sys.stderr)


def _body(content: bytes) -> str:
    return content[:MAX_BODY_BYTES].decode("utf-8", errors="replace")


def _headers(headers: Any) -> dict[str, str]:
    return {
        name: "[REDACTED]" if name.lower() in SENSITIVE_HEADERS else value
        for name, value in headers.items()
    }


def _json_object(body: str) -> dict[str, Any]:
    try:
        value = json.loads(body)
        return value if isinstance(value, dict) else {}
    except json.JSONDecodeError:
        return {}


def _provider(host: str) -> str | None:
    lowered = host.lower()
    if "anthropic" in lowered:
        return "anthropic"
    if "openai" in lowered:
        return "openai"
    return None


addons = [LantuRecorder()]
