"""Compact, deterministic repository symbol map for the stable prompt prefix."""

from __future__ import annotations

import ast
import os
import re
from dataclasses import dataclass
from pathlib import Path

from lantu.conversation import Message, estimate_tokens


_IGNORED_DIRS = {
    ".git",
    ".hg",
    ".lantu",
    ".mypy_cache",
    ".next",
    ".pytest_cache",
    ".ruff_cache",
    ".svn",
    ".tox",
    ".venv",
    ".vscode",
    "__pycache__",
    "build",
    "coverage",
    "dist",
    "env",
    "node_modules",
    "target",
    "venv",
    "vendor",
}
_MAX_FILE_BYTES = 2_000_000
_MAX_SYMBOLS = 5_000

_LANGUAGE_BY_SUFFIX = {
    ".c": "c",
    ".cc": "cpp",
    ".cpp": "cpp",
    ".cs": "csharp",
    ".go": "go",
    ".h": "c",
    ".hpp": "cpp",
    ".java": "java",
    ".js": "javascript",
    ".jsx": "javascript",
    ".py": "python",
    ".rs": "rust",
    ".ts": "typescript",
    ".tsx": "typescript",
}

_DECLARATION_PATTERNS: dict[str, tuple[tuple[str, re.Pattern[str]], ...]] = {
    "javascript": (
        ("function", re.compile(r"^\s*(?:export\s+)?(?:async\s+)?function\s+([A-Za-z_$][\w$]*)", re.M)),
        ("class", re.compile(r"^\s*(?:export\s+)?(?:default\s+)?class\s+([A-Za-z_$][\w$]*)", re.M)),
    ),
    "typescript": (
        ("function", re.compile(r"^\s*(?:export\s+)?(?:async\s+)?function\s+([A-Za-z_$][\w$]*)", re.M)),
        ("class", re.compile(r"^\s*(?:export\s+)?(?:default\s+)?class\s+([A-Za-z_$][\w$]*)", re.M)),
        ("interface", re.compile(r"^\s*(?:export\s+)?interface\s+([A-Za-z_$][\w$]*)", re.M)),
        ("type", re.compile(r"^\s*(?:export\s+)?type\s+([A-Za-z_$][\w$]*)", re.M)),
    ),
    "go": (
        ("function", re.compile(r"^\s*func\s+(?:\([^)]*\)\s+)?([A-Za-z_]\w*)", re.M)),
        ("type", re.compile(r"^\s*type\s+([A-Za-z_]\w*)\s+(?:struct|interface)", re.M)),
    ),
    "rust": (
        ("function", re.compile(r"^\s*(?:pub(?:\([^)]*\))?\s+)?(?:async\s+)?fn\s+([A-Za-z_]\w*)", re.M)),
        ("type", re.compile(r"^\s*(?:pub\s+)?(?:struct|enum|trait)\s+([A-Za-z_]\w*)", re.M)),
    ),
    "java": (
        ("type", re.compile(r"^\s*(?:public\s+)?(?:abstract\s+)?(?:class|interface|enum)\s+([A-Za-z_]\w*)", re.M)),
    ),
    "csharp": (
        ("type", re.compile(r"^\s*(?:public\s+|internal\s+)?(?:class|interface|enum|struct)\s+([A-Za-z_]\w*)", re.M)),
    ),
    "c": (
        ("function", re.compile(r"^\s*(?:[A-Za-z_]\w*[\s*]+)+([A-Za-z_]\w*)\s*\([^;{}]*\)\s*\{", re.M)),
    ),
    "cpp": (
        ("type", re.compile(r"^\s*(?:class|struct|enum)\s+([A-Za-z_]\w*)", re.M)),
        ("function", re.compile(r"^\s*(?:[A-Za-z_:~]\w*[\s*&:<>]+)+([A-Za-z_~]\w*)\s*\([^;{}]*\)\s*\{", re.M)),
    ),
}


@dataclass(frozen=True)
class RepoSymbol:
    path: str
    line: int
    kind: str
    name: str


@dataclass(frozen=True)
class RepoMapSnapshot:
    text: str
    files_indexed: int
    symbols_indexed: int
    estimated_tokens: int
    truncated: bool


def _estimate_text(text: str) -> int:
    if not text:
        return 0
    return max(1, estimate_tokens([Message(role="system", content=text)]))


def _line_number(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def _python_symbols(path: str, text: str) -> list[RepoSymbol]:
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return []

    symbols: list[RepoSymbol] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            symbols.append(RepoSymbol(path, node.lineno, "function", node.name))
        elif isinstance(node, ast.ClassDef):
            symbols.append(RepoSymbol(path, node.lineno, "class", node.name))
    return symbols


def _regex_symbols(path: str, language: str, text: str) -> list[RepoSymbol]:
    symbols: list[RepoSymbol] = []
    for kind, pattern in _DECLARATION_PATTERNS.get(language, ()):
        for match in pattern.finditer(text):
            symbols.append(
                RepoSymbol(path, _line_number(text, match.start()), kind, match.group(1))
            )
    return symbols


def _relevance_key(path: str) -> tuple[int, str]:
    return path.count("/"), path


class RepoMap:
    """Own repository scanning, rendering and refresh behind one small interface."""

    def __init__(self, root: str | Path, max_tokens: int = 4_000) -> None:
        self.root = Path(root).resolve()
        self.max_tokens = max_tokens
        self._snapshot = RepoMapSnapshot("", 0, 0, 0, False)

    @property
    def snapshot(self) -> RepoMapSnapshot:
        return self._snapshot

    def refresh(self) -> RepoMapSnapshot:
        symbols, files_indexed = self._scan()
        text, truncated = self._render(symbols)
        self._snapshot = RepoMapSnapshot(
            text=text,
            files_indexed=files_indexed,
            symbols_indexed=len(symbols),
            estimated_tokens=_estimate_text(text),
            truncated=truncated,
        )
        return self._snapshot

    def retarget(self, root: str | Path) -> RepoMapSnapshot:
        resolved = Path(root).resolve()
        if resolved == self.root:
            return self._snapshot
        self.root = resolved
        return self.refresh()

    def prompt_section(self) -> str:
        if not self._snapshot.text:
            return ""
        return (
            "## Repository Map\n"
            "This is a compact, possibly incomplete symbol index. Read files before "
            "making changes.\n\n"
            f"{self._snapshot.text}"
        )

    def _scan(self) -> tuple[list[RepoSymbol], int]:
        candidates: list[tuple[str, Path, str]] = []
        for dirpath, dirnames, filenames in os.walk(self.root):
            dirnames[:] = sorted(
                directory
                for directory in dirnames
                if directory not in _IGNORED_DIRS and not directory.startswith(".")
            )
            for filename in filenames:
                language = _LANGUAGE_BY_SUFFIX.get(Path(filename).suffix.lower())
                if language is None or filename.startswith("."):
                    continue
                source_path = Path(dirpath) / filename
                relative = source_path.relative_to(self.root).as_posix()
                candidates.append((relative, source_path, language))

        symbols: list[RepoSymbol] = []
        files_indexed = 0
        for relative, source_path, language in sorted(
            candidates, key=lambda item: _relevance_key(item[0])
        ):
            if len(symbols) >= _MAX_SYMBOLS:
                break
            try:
                if source_path.stat().st_size > _MAX_FILE_BYTES:
                    continue
                text = source_path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            files_indexed += 1
            extracted = (
                _python_symbols(relative, text)
                if language == "python"
                else _regex_symbols(relative, language, text)
            )
            symbols.extend(extracted[: _MAX_SYMBOLS - len(symbols)])
        return sorted(symbols, key=lambda item: (_relevance_key(item.path), item.line, item.name)), files_indexed

    def _render(self, symbols: list[RepoSymbol]) -> tuple[str, bool]:
        if not symbols:
            return "", False

        lines = [
            f"{symbol.path}:{symbol.line} {symbol.kind} {symbol.name}"
            for symbol in symbols
        ]
        output: list[str] = []
        for line in lines:
            candidate = "\n".join([*output, line])
            if _estimate_text(candidate) > self.max_tokens:
                break
            output.append(line)

        truncated = len(output) < len(lines)
        if truncated:
            note = f"# map truncated: {len(output)} of {len(lines)} symbols shown"
            while output and _estimate_text("\n".join([*output, note])) > self.max_tokens:
                output.pop()
            if _estimate_text(note) <= self.max_tokens:
                output.append(note)
        return "\n".join(output), truncated


def build_repo_map(root: str | Path, max_tokens: int = 4_000) -> RepoMap:
    repo_map = RepoMap(root, max_tokens=max_tokens)
    repo_map.refresh()
    return repo_map
