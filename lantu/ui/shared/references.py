from __future__ import annotations

import os
import re
from pathlib import Path


MAX_AT_REF_BYTES = 10240

_PATH_SEGMENT_PATTERN = r"\.?[\w-]+(?:\.[\w-]+)*"
_PATH_SEGMENT_RE = re.compile(_PATH_SEGMENT_PATTERN)
_AT_REF_TERMINATORS = ".,!?;:)]}>\"'，。！？；：、）】》〉」』〕”’…"
_AT_REF_BOUNDARY_PATTERN = rf"(?=$|\s|[{re.escape(_AT_REF_TERMINATORS)}])"
_AT_REF_RE = re.compile(
    rf"@({_PATH_SEGMENT_PATTERN}(?:/{_PATH_SEGMENT_PATTERN})*)"
    rf"{_AT_REF_BOUNDARY_PATTERN}"
)
_SKIP_DIRS = {
    ".git",
    "node_modules",
    ".venv",
    "__pycache__",
    ".lantu",
    "build",
    ".gradle",
}


def _has_terminal_controls(value: str) -> bool:
    return any(ord(character) < 0x20 or 0x7F <= ord(character) <= 0x9F for character in value)


def _has_skipped_component(path: Path) -> bool:
    return any(part.startswith(".") or part in _SKIP_DIRS for part in path.parts)


def _is_supported_reference_path(path: str) -> bool:
    parts = path.removesuffix("/").split("/")
    return bool(parts) and all(_PATH_SEGMENT_RE.fullmatch(part) for part in parts)


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _resolve_root(work_dir: str) -> Path | None:
    try:
        root = Path(work_dir).expanduser().resolve(strict=True)
    except OSError:
        return None
    return root if root.is_dir() else None


def _read_ref_file(full_path: Path, root: Path) -> str | None:
    try:
        with full_path.open("rb") as file:
            opened_stat = os.fstat(file.fileno())
            verified_path = full_path.resolve(strict=True)
            if not _is_within(verified_path, root):
                return None
            path_stat = verified_path.stat()
            if (opened_stat.st_dev, opened_stat.st_ino) != (
                path_stat.st_dev,
                path_stat.st_ino,
            ):
                return None
            content = file.read(MAX_AT_REF_BYTES)
    except OSError:
        return None
    return content.decode("utf-8", errors="replace")


def scan_files(work_dir: str, prefix: str, limit: int = 10) -> list[str]:
    if limit <= 0 or _has_terminal_controls(prefix):
        return []

    root = _resolve_root(work_dir)
    if root is None:
        return []

    prefix_path = Path(prefix)
    if prefix_path.is_absolute() or ".." in prefix_path.parts:
        return []

    if prefix.endswith(("/", os.sep)):
        relative_dir = Path(prefix.rstrip("/"))
        name_prefix = ""
    else:
        relative_dir = prefix_path.parent
        name_prefix = prefix_path.name.lower()

    if _has_skipped_component(relative_dir):
        return []

    try:
        base = (root / relative_dir).resolve(strict=True)
        if (
            not base.is_dir()
            or not _is_within(base, root)
            or _has_skipped_component(base.relative_to(root))
        ):
            return []
        entries = sorted(base.iterdir(), key=lambda item: item.name)
    except OSError:
        return []

    matches: list[str] = []
    for entry in entries:
        name = entry.name
        if (
            name in _SKIP_DIRS
            or name.startswith(".")
            or _has_terminal_controls(name)
            or not name.lower().startswith(name_prefix)
        ):
            continue
        try:
            resolved = entry.resolve(strict=True)
            if (
                not _is_within(resolved, root)
                or _has_skipped_component(resolved.relative_to(root))
            ):
                continue
            is_directory = resolved.is_dir()
        except OSError:
            continue

        relative = entry.relative_to(root).as_posix()
        candidate = relative + "/" if is_directory else relative
        if not _is_supported_reference_path(candidate):
            continue
        matches.append(candidate)
        if len(matches) >= limit:
            break
    return matches


def expand_at_refs(text: str, work_dir: str) -> str:
    root = _resolve_root(work_dir)
    if root is None:
        return text

    def replace(match: re.Match[str]) -> str:
        rel_path = match.group(1)
        relative = Path(rel_path)
        if relative.is_absolute() or ".." in relative.parts:
            return match.group(0)
        try:
            full_path = (root / relative).resolve(strict=True)
            if not _is_within(full_path, root) or not full_path.is_file():
                return match.group(0)
        except OSError:
            return match.group(0)
        content = _read_ref_file(full_path, root)
        if content is None:
            return match.group(0)
        return f"[File: {rel_path}]\n```\n{content}\n```"

    return _AT_REF_RE.sub(replace, text)
