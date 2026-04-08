"""Thread-safe TTL cache for expensive API responses."""

from __future__ import annotations

import threading
import time
from typing import Any


class TTLCache:
    """Simple thread-safe TTL cache for expensive API responses."""

    def __init__(self) -> None:
        self._store: dict[str, tuple[float, Any]] = {}
        self._lock = threading.Lock()

    def get(self, key: str, ttl: float) -> Any | None:
        with self._lock:
            entry = self._store.get(key)
            if entry and (time.time() - entry[0]) < ttl:
                return entry[1]
        return None

    def set(self, key: str, value: Any) -> None:
        with self._lock:
            self._store[key] = (time.time(), value)


# Singleton shared across request handlers
api_cache = TTLCache()
