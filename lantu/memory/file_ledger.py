from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class FileLedgerEntry:
    """The latest durable observation of one file in a Session."""

    path: str
    content_hash: str
    size: int
    line_count: int
    operation: str
    offset: int = 0
    limit: int | None = None
    mtime_ns: int | None = None
    tool_call_id: str | None = None

    def to_payload(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "content_hash": self.content_hash,
            "size": self.size,
            "line_count": self.line_count,
            "operation": self.operation,
            "offset": self.offset,
            "limit": self.limit,
            "mtime_ns": self.mtime_ns,
            "tool_call_id": self.tool_call_id,
        }

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> FileLedgerEntry | None:
        path = payload.get("path")
        content_hash = payload.get("content_hash")
        operation = payload.get("operation")
        if not all(isinstance(value, str) for value in (path, content_hash, operation)):
            return None
        try:
            return cls(
                path=path,
                content_hash=content_hash,
                size=max(0, int(payload.get("size", 0))),
                line_count=max(0, int(payload.get("line_count", 0))),
                operation=operation,
                offset=max(0, int(payload.get("offset", 0))),
                limit=(
                    None
                    if payload.get("limit") is None
                    else max(0, int(payload["limit"]))
                ),
                mtime_ns=(
                    None
                    if payload.get("mtime_ns") is None
                    else int(payload["mtime_ns"])
                ),
                tool_call_id=(
                    payload.get("tool_call_id")
                    if isinstance(payload.get("tool_call_id"), str)
                    else None
                ),
            )
        except (TypeError, ValueError):
            return None

    @property
    def range_start(self) -> int:
        return self.offset + 1

    @property
    def range_end(self) -> int:
        if self.line_count <= 0 or self.offset >= self.line_count:
            return self.line_count
        if self.limit is None:
            return self.line_count
        return min(self.line_count, self.offset + self.limit)


@dataclass(frozen=True)
class FileReadObservation:
    """One tool call's exact file version and requested line range."""

    tool_call_id: str
    path: str
    content_hash: str
    range_start: int
    range_end: int


class FileLedger:
    """Session-local projection of file state and context visibility.

    File versions and read observations are durable facts. Visible ranges are
    only a projection of the current conversation and can be cleared on compact.
    """

    def __init__(self, entries: list[FileLedgerEntry] | None = None) -> None:
        self._entries: dict[str, FileLedgerEntry] = {}
        self._observations: dict[str, FileReadObservation] = {}
        self._visible_ranges: dict[tuple[str, str], list[tuple[int, int]]] = {}
        for entry in entries or []:
            self.apply(entry)

    @staticmethod
    def normalize_path(path: str | Path) -> str:
        return str(Path(path).expanduser().resolve())

    @staticmethod
    def build_entry(
        path: str | Path,
        content: str,
        *,
        operation: str,
        offset: int = 0,
        limit: int | None = None,
        mtime_ns: int | None = None,
        tool_call_id: str | None = None,
    ) -> FileLedgerEntry:
        encoded = content.encode("utf-8")
        return FileLedgerEntry(
            path=FileLedger.normalize_path(path),
            content_hash=hashlib.sha256(encoded).hexdigest(),
            size=len(encoded),
            line_count=len(content.splitlines()),
            operation=operation,
            offset=max(0, offset),
            limit=None if limit is None else max(0, limit),
            mtime_ns=mtime_ns,
            tool_call_id=tool_call_id,
        )

    def apply(self, entry: FileLedgerEntry) -> None:
        self._entries[entry.path] = entry
        if entry.tool_call_id and entry.operation == "read":
            self._observations[entry.tool_call_id] = FileReadObservation(
                tool_call_id=entry.tool_call_id,
                path=entry.path,
                content_hash=entry.content_hash,
                range_start=entry.range_start,
                range_end=entry.range_end,
            )

    def apply_payload(self, payload: dict[str, Any]) -> FileLedgerEntry | None:
        entry = FileLedgerEntry.from_payload(payload)
        if entry is not None:
            self.apply(entry)
        return entry

    def get(self, path: str | Path) -> FileLedgerEntry | None:
        return self._entries.get(self.normalize_path(path))

    def entries(self) -> list[FileLedgerEntry]:
        return list(self._entries.values())

    def as_payloads(self) -> list[dict[str, Any]]:
        return [entry.to_payload() for entry in self.entries()]

    def observation(self, tool_call_id: str) -> FileReadObservation | None:
        return self._observations.get(tool_call_id)

    def mark_visible(
        self,
        path: str | Path,
        content_hash: str,
        range_start: int,
        range_end: int,
    ) -> None:
        if range_end < range_start:
            return
        key = (self.normalize_path(path), content_hash)
        ranges = self._visible_ranges.setdefault(key, [])
        ranges.append((max(1, range_start), max(1, range_end)))
        ranges.sort()
        merged: list[tuple[int, int]] = []
        for start, end in ranges:
            if not merged or start > merged[-1][1] + 1:
                merged.append((start, end))
            else:
                merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        self._visible_ranges[key] = merged

    def is_visible(
        self,
        path: str | Path,
        content_hash: str,
        range_start: int,
        range_end: int,
    ) -> bool:
        if range_end < range_start:
            return True
        key = (self.normalize_path(path), content_hash)
        return any(
            start <= range_start and end >= range_end
            for start, end in self._visible_ranges.get(key, [])
        )

    def invalidate(self, path: str | Path) -> None:
        normalized = self.normalize_path(path)
        for key in list(self._visible_ranges):
            if key[0] == normalized:
                del self._visible_ranges[key]

    def clear_visible(self) -> None:
        self._visible_ranges.clear()

    def visible_ranges(self) -> dict[tuple[str, str], list[tuple[int, int]]]:
        return {key: list(ranges) for key, ranges in self._visible_ranges.items()}
