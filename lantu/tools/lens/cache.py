from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Iterable

from lantu.memory.journal import JournalEvent
from lantu.tools.lens.fingerprint import RequestFingerprint, compare_fingerprints


@dataclass(frozen=True)
class CacheCall:
    sequence: int
    model_call_id: str
    call_kind: str
    provider: str
    model: str
    change: str
    common_message_count: int
    common_message_chars: int
    first_divergence_message: int | None
    prompt_tokens: int
    cache_read_tokens: int
    cache_creation_tokens: int
    cache_hit_rate: float


@dataclass(frozen=True)
class CacheReport:
    session_id: str
    calls: tuple[CacheCall, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "calls": [asdict(call) for call in self.calls],
        }

    def render_text(self) -> str:
        lines = [
            f"Session: {self.session_id}",
            "Seq | Kind | Change | Cache hit | Common messages",
        ]
        for call in self.calls:
            lines.append(
                f"{call.sequence} | {call.call_kind} | {call.change} | "
                f"{call.cache_hit_rate:.1%} | {call.common_message_count}"
            )
        return "\n".join(lines)


def build_cache_report(
    session_id: str,
    events: Iterable[JournalEvent],
) -> CacheReport:
    by_call: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    compact_sequences: list[int] = []

    for event in events:
        if event.type == "context.compacted":
            compact_sequences.append(event.sequence)
            continue
        call_id = str(event.payload.get("model_call_id", ""))
        if not call_id:
            continue
        record = by_call.setdefault(call_id, {})
        if event.type == "model.request.started":
            if call_id not in order:
                order.append(call_id)
            record["started"] = event
        elif event.type == "model.request.prepared":
            record["prepared"] = event
        elif event.type == "usage.recorded":
            record["usage"] = event

    previous_by_lane: dict[
        tuple[str, str, str, str],
        tuple[int, RequestFingerprint, RequestFingerprint | None, int],
    ] = {}
    calls: list[CacheCall] = []
    for call_id in order:
        record = by_call[call_id]
        started = record.get("started")
        if not isinstance(started, JournalEvent):
            continue
        assembly_payload = started.payload.get("assembly")
        if not isinstance(assembly_payload, dict):
            continue
        assembly = RequestFingerprint.from_payload(assembly_payload)
        prepared = record.get("prepared")
        payload_fingerprint: RequestFingerprint | None = None
        if isinstance(prepared, JournalEvent):
            raw_payload = prepared.payload.get("payload")
            if isinstance(raw_payload, dict):
                payload_fingerprint = RequestFingerprint.from_payload(raw_payload)

        call_kind = str(started.payload.get("call_kind", "unknown"))
        provider = str(started.payload.get("provider", ""))
        model = str(started.payload.get("model", ""))
        lane = (
            str(started.payload.get("agent_id", "")),
            call_kind,
            provider,
            model,
        )
        usage = record.get("usage")
        usage_payload = usage.payload if isinstance(usage, JournalEvent) else {}
        input_tokens = int(usage_payload.get("input_tokens", 0) or 0)
        cache_read = int(usage_payload.get("cache_read_tokens", 0) or 0)
        cache_creation = int(usage_payload.get("cache_creation_tokens", 0) or 0)
        prompt_tokens = input_tokens + cache_read + cache_creation
        hit_rate = cache_read / prompt_tokens if prompt_tokens else 0.0

        previous = previous_by_lane.get(lane)
        change = "cold_start"
        common_count = 0
        common_chars = 0
        divergence: int | None = None
        if previous is not None:
            previous_sequence, previous_assembly, previous_payload, previous_cache_read = previous
            comparison = compare_fingerprints(previous_assembly, assembly)
            change = comparison.change
            common_count = comparison.common_message_count
            common_chars = comparison.common_message_chars
            divergence = comparison.first_divergence_message
            if previous_payload is not None and payload_fingerprint is not None:
                payload_comparison = compare_fingerprints(
                    previous_payload,
                    payload_fingerprint,
                )
                if (
                    comparison.change in {"unchanged", "append_only"}
                    and payload_comparison.change == "history_rewritten"
                ):
                    change = "client_serialization_changed"
                    divergence = payload_comparison.first_divergence_message
            if (
                change == "history_rewritten"
                and any(previous_sequence < seq < started.sequence for seq in compact_sequences)
            ):
                change = "expected_compaction_reset"

        if (
            previous is not None
            and change in {"unchanged", "append_only"}
            and previous_cache_read > 0
            and prompt_tokens
            and cache_read == 0
        ):
            change = "provider_miss_candidate"

        calls.append(
            CacheCall(
                sequence=started.sequence,
                model_call_id=call_id,
                call_kind=call_kind,
                provider=provider,
                model=model,
                change=change,
                common_message_count=common_count,
                common_message_chars=common_chars,
                first_divergence_message=divergence,
                prompt_tokens=prompt_tokens,
                cache_read_tokens=cache_read,
                cache_creation_tokens=cache_creation,
                cache_hit_rate=hit_rate,
            )
        )
        previous_by_lane[lane] = (
            started.sequence,
            assembly,
            payload_fingerprint,
            cache_read,
        )

    return CacheReport(session_id=session_id, calls=tuple(calls))
