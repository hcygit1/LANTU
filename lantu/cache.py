from __future__ import annotations

import threading


class FileCache:
    def __init__(self) -> None:
        self._store: dict[str, tuple[str, int | None]] = {}
        self._lock = threading.Lock()

    def get(self, path: str, mtime_ns: int | None = None) -> str | None:
        with self._lock:
            entry = self._store.get(path)
            if entry is None:
                return None
            content, cached_mtime_ns = entry
            if (
                mtime_ns is not None
                and cached_mtime_ns is not None
                and cached_mtime_ns != mtime_ns
            ):
                self._store.pop(path, None)
                return None
            return content


    def put(self, path: str, content: str, mtime_ns: int | None = None) -> None:
        with self._lock:
            self._store[path] = (content, mtime_ns)


    def invalidate(self, path: str) -> None:
        with self._lock:
            self._store.pop(path, None)


    def clear(self) -> None:
        with self._lock:
            self._store.clear()


    def __len__(self) -> int:
        with self._lock:
            return len(self._store)
