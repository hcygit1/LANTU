from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Any, Iterable

from lantu.conversation import Message, MessageKind


@dataclass(frozen=True)
class ItemFingerprint:
    digest: str
    chars: int
    role: str = ""
    kind: str = MessageKind.FROZEN.value
    appendix_key: str | None = None

    def to_payload(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RequestFingerprint:
    digest: str
    system_hash: str
    tools_hash: str
    messages_hash: str
    parameters_hash: str
    chars: int
    messages: tuple[ItemFingerprint, ...]

    def to_payload(self) -> dict[str, Any]:
        return {
            "digest": self.digest,
            "system_hash": self.system_hash,
            "tools_hash": self.tools_hash,
            "messages_hash": self.messages_hash,
            "parameters_hash": self.parameters_hash,
            "chars": self.chars,
            "messages": [item.to_payload() for item in self.messages],
        }

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> RequestFingerprint:
        return cls(
            digest=str(payload.get("digest", "")),
            system_hash=str(payload.get("system_hash", "")),
            tools_hash=str(payload.get("tools_hash", "")),
            messages_hash=str(payload.get("messages_hash", "")),
            parameters_hash=str(payload.get("parameters_hash", "")),
            chars=int(payload.get("chars", 0) or 0),
            messages=tuple(
                ItemFingerprint(
                    digest=str(item.get("digest", "")),
                    chars=int(item.get("chars", 0) or 0),
                    role=str(item.get("role", "")),
                    kind=str(item.get("kind", MessageKind.FROZEN.value)),
                    appendix_key=item.get("appendix_key"),
                )
                for item in payload.get("messages", [])
                if isinstance(item, dict)
            ),
        )


@dataclass(frozen=True)
class FingerprintComparison:
    change: str
    common_message_count: int
    common_message_chars: int
    first_divergence_message: int | None


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _message_value(message: Message) -> dict[str, Any]:
    return {
        "role": message.role,
        "content": message.content,
        "tool_uses": [asdict(item) for item in message.tool_uses],
        "tool_results": [asdict(item) for item in message.tool_results],
        "thinking_blocks": [asdict(item) for item in message.thinking_blocks],
    }


def _fingerprint_messages(
    messages: Iterable[dict[str, Any]],
    metadata: Iterable[tuple[str, str | None]] | None = None,
) -> tuple[ItemFingerprint, ...]:
    result: list[ItemFingerprint] = []
    metadata_iter = iter(metadata) if metadata is not None else None
    for message in messages:
        encoded = _canonical_json(message)
        if metadata_iter is not None:
            kind, appendix_key = next(
                metadata_iter, (MessageKind.FROZEN.value, None)
            )
        else:
            kind, appendix_key = MessageKind.FROZEN.value, None
        result.append(
            ItemFingerprint(
                digest=hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
                chars=len(encoded),
                role=str(message.get("role", message.get("type", ""))),
                kind=kind,
                appendix_key=appendix_key,
            )
        )
    return tuple(result)


def _build_fingerprint(
    system: Any,
    tools: Any,
    messages: list[dict[str, Any]],
    *,
    full_value: Any,
    parameters: Any = None,
    message_metadata: Iterable[tuple[str, str | None]] | None = None,
) -> RequestFingerprint:
    encoded = _canonical_json(full_value)
    return RequestFingerprint(
        digest=hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
        system_hash=_digest(system),
        tools_hash=_digest(tools),
        messages_hash=_digest(messages),
        parameters_hash=_digest(parameters),
        chars=len(encoded),
        messages=_fingerprint_messages(messages, message_metadata),
    )


def build_assembly_fingerprint(
    system: str,
    tools: list[dict[str, Any]],
    messages: Iterable[Message],
) -> RequestFingerprint:
    message_list = list(messages)
    message_values = [_message_value(message) for message in message_list]
    message_metadata = [
        (getattr(message.kind, "value", str(message.kind)), message.appendix_key)
        for message in message_list
    ]
    value = {"system": system, "tools": tools, "messages": message_values}
    return _build_fingerprint(
        system,
        tools,
        message_values,
        full_value=value,
        parameters=None,
        message_metadata=message_metadata,
    )


def build_payload_fingerprint(kwargs: dict[str, Any]) -> RequestFingerprint:
    payload = {key: value for key, value in kwargs.items() if key != "extra_headers"}
    messages = payload.get("messages", payload.get("input", []))
    if not isinstance(messages, list):
        messages = []
    message_values = [item for item in messages if isinstance(item, dict)]
    system = payload.get("system", payload.get("instructions", ""))
    tools = payload.get("tools", [])
    parameters = {
        key: value
        for key, value in payload.items()
        if key not in {"system", "instructions", "tools", "messages", "input"}
    }
    return _build_fingerprint(
        system,
        tools,
        message_values,
        full_value=payload,
        parameters=parameters,
    )


def compare_fingerprints(
    previous: RequestFingerprint,
    current: RequestFingerprint,
) -> FingerprintComparison:
    common_count = 0
    common_chars = 0
    for old, new in zip(previous.messages, current.messages):
        if old.digest != new.digest:
            break
        common_count += 1
        common_chars += old.chars

    first_divergence = (
        common_count
        if common_count < min(len(previous.messages), len(current.messages))
        else None
    )
    if previous.system_hash != current.system_hash:
        change = "system_changed"
    elif previous.tools_hash != current.tools_hash:
        change = "tools_changed"
    elif previous.parameters_hash != current.parameters_hash:
        change = "parameters_changed"
    elif previous.digest == current.digest:
        change = "unchanged"
    elif common_count == len(previous.messages) and len(current.messages) >= len(previous.messages):
        change = "append_only"
    else:
        change = "history_rewritten"

    return FingerprintComparison(
        change=change,
        common_message_count=common_count,
        common_message_chars=common_chars,
        first_divergence_message=first_divergence,
    )
