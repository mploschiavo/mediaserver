"""LRU idempotency cache for mutating security endpoints.

Any POST that applies a ban, kills a session, or consumes a ticket
needs to be safe to retry: UIs double-click, networks replay, proxies
retry. The :class:`IdempotencyCache` stores the successful response
payload keyed by ``(actor.audit_label, idempotency_key)`` with a
short TTL. A repeat with the same key inside TTL returns the cached
payload and the handler skips the side effects.

Design notes
------------
* Keyed on ``(actor_label, key)`` so two different admins pasting the
  same key (e.g. "banjoe-2026-04-24") do not collide. One user's
  retry never shadows another user's intent.
* In-memory + LRU; no durability across controller restarts. That is
  acceptable: the TTL is a few minutes and a crash loop is already a
  large enough event that re-running a ban/revoke is fine.
* An RLock (not Lock) guards state because ``get_or_store`` calls may
  execute the body inline while still under a need to re-enter the
  cache for accounting. Keeping it re-entrant matches the bus pattern.
* Values are ``dict`` payloads — the JSON response body the handler
  returned. We deep-copy on put + get so the caller cannot mutate the
  cached entry by poking the response object post-emit.
"""

from __future__ import annotations

import copy
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any

DEFAULT_MAX_ENTRIES = 1024
DEFAULT_TTL_SECONDS = 5 * 60  # 5 minutes


@dataclass
class _Entry:
    payload: dict[str, Any]
    expires_at: float


class IdempotencyCache:
    """In-memory LRU cache for idempotent POST retries.

    Parameters
    ----------
    max_entries:
        Hard cap on the number of cached responses. When exceeded the
        least-recently-used entry is evicted. Default 1024 — enough
        for several minutes of admin activity without leaking memory
        under a flood.
    ttl_seconds:
        How long a cached response remains valid. Reads after TTL
        return ``None`` (the value is also dropped opportunistically).
        Default 300s.
    clock:
        Optional callable returning monotonic seconds — injected so
        tests can drive the clock deterministically.
    """

    def __init__(
        self,
        *,
        max_entries: int = DEFAULT_MAX_ENTRIES,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
        clock: Any = None,
    ) -> None:
        if max_entries <= 0:
            raise ValueError("max_entries must be positive")
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        self._max = int(max_entries)
        self._ttl = float(ttl_seconds)
        self._clock = clock if clock is not None else time.monotonic
        self._lock = threading.RLock()
        self._entries: OrderedDict[tuple[str, str], _Entry] = OrderedDict()

    # ---- public API ----------------------------------------------------

    def get(self, actor_label: str, key: str) -> dict[str, Any] | None:
        """Return a cached payload for ``(actor_label, key)`` if still
        valid, else ``None``.

        Side effect: LRU bookkeeping — a successful hit moves the
        entry to the most-recently-used end.
        """
        if not key:
            return None
        ckey = self._cache_key(actor_label, key)
        now = self._clock()
        with self._lock:
            entry = self._entries.get(ckey)
            if entry is None:
                return None
            if entry.expires_at <= now:
                # Expired — drop opportunistically.
                self._entries.pop(ckey, None)
                return None
            # LRU touch.
            self._entries.move_to_end(ckey)
            # Deep-copy on read so the caller can't mutate the cached
            # payload by editing the returned dict.
            return copy.deepcopy(entry.payload)

    def put(self, actor_label: str, key: str,
            payload: dict[str, Any]) -> None:
        """Store ``payload`` under ``(actor_label, key)``.

        Silently no-ops when ``key`` is empty — callers thread the
        header value directly and omitting the header must not raise.
        """
        if not key:
            return
        ckey = self._cache_key(actor_label, key)
        now = self._clock()
        stored = _Entry(
            payload=copy.deepcopy(payload or {}),
            expires_at=now + self._ttl,
        )
        with self._lock:
            self._entries[ckey] = stored
            self._entries.move_to_end(ckey)
            self._evict_if_needed()

    def clear(self) -> None:
        """Drop every cached entry. Used by tests between cases."""
        with self._lock:
            self._entries.clear()

    def size(self) -> int:
        """Current number of cached entries."""
        with self._lock:
            return len(self._entries)

    # ---- internals -----------------------------------------------------

    def _cache_key(self, actor_label: str,
                   key: str) -> tuple[str, str]:
        # Normalise to str so callers can pass actor objects or keys
        # that come straight off an HTTP header without worrying about
        # None vs "". Both halves stripped to stop trailing whitespace
        # from creating split cache buckets.
        return (str(actor_label or "anonymous").strip(),
                str(key or "").strip())

    def _evict_if_needed(self) -> None:
        # Called under lock. Pop from the front (least recently used)
        # until we fit.
        while len(self._entries) > self._max:
            self._entries.popitem(last=False)


class IdempotencyCacheRegistry:
    """Process-wide accessor for the default cache.

    Class-level state + class methods so callers can do
    ``IdempotencyCacheRegistry.get_default()`` without plumbing an
    instance around. Tests inject a fresh cache per case via
    ``set_default`` and reset it in ``tearDown``.
    """

    _cache: IdempotencyCache | None = None
    _lock: threading.Lock = threading.Lock()

    @classmethod
    def get_default(cls) -> IdempotencyCache:
        with cls._lock:
            if cls._cache is None:
                cls._cache = IdempotencyCache()
            return cls._cache

    @classmethod
    def set_default(cls, cache: IdempotencyCache | None) -> None:
        with cls._lock:
            cls._cache = cache


__all__ = [
    "DEFAULT_MAX_ENTRIES",
    "DEFAULT_TTL_SECONDS",
    "IdempotencyCache",
    "IdempotencyCacheRegistry",
]
