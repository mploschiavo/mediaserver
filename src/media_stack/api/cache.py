"""Thread-safe TTL cache for expensive API responses."""

from __future__ import annotations

import threading
import time
from typing import Any


class TTLCache:
    """Simple thread-safe TTL cache for expensive API responses."""

    def __init__(self, max_size: int = 128) -> None:
        self._store: dict[str, tuple[float, Any]] = {}
        self._lock = threading.Lock()
        self._max_size = max_size

    def get(self, key: str, ttl: float) -> Any | None:
        with self._lock:
            entry = self._store.get(key)
            if entry and (time.time() - entry[0]) < ttl:
                return entry[1]
            # Evict stale entry on access
            if entry:
                del self._store[key]
        return None

    def set(self, key: str, value: Any) -> None:
        with self._lock:
            self._store[key] = (time.time(), value)
            # Evict expired entries when nearing capacity
            if len(self._store) > self._max_size:
                self._evict_expired()

    def invalidate(self, key: str) -> bool:
        """Remove a specific key. Returns True if it existed."""
        with self._lock:
            return self._store.pop(key, None) is not None

    def clear(self) -> int:
        """Remove all entries. Returns count removed."""
        with self._lock:
            count = len(self._store)
            self._store.clear()
            return count

    def _evict_expired(self, max_age: float = 300.0) -> int:
        """Remove entries older than max_age seconds. Called under lock."""
        now = time.time()
        expired = [k for k, (ts, _) in self._store.items() if (now - ts) > max_age]
        for k in expired:
            del self._store[k]
        return len(expired)

    def get_or_compute(self, key: str, compute_fn: Any, ttl: float = 60.0) -> Any:
        """Return cached value or compute and cache it."""
        cached = self.get(key, ttl)
        if cached is not None:
            return cached
        value = compute_fn()
        self.set(key, value)
        return value

    @property
    def size(self) -> int:
        with self._lock:
            return len(self._store)


# Singleton shared across request handlers
api_cache = TTLCache()
