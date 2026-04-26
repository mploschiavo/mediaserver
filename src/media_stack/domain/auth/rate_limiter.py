"""Token-bucket rate limiter keyed by (client_id, bucket).

Used to throttle mutating user-management endpoints so a guessed admin
password can't brute-force reset/create/delete in a tight loop.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass


@dataclass
class _Bucket:
    tokens: float
    last_refill: float


class RateLimiter:
    """Simple in-memory token bucket.

    Each (client_id, bucket_name) pair has a capacity and a refill rate.
    ``allow`` returns True and deducts a token, or False if the bucket is
    empty. Thread-safe.
    """

    def __init__(
        self,
        *,
        capacity: float,
        refill_per_second: float,
        clock=None,
    ) -> None:
        self._capacity = float(capacity)
        self._refill = float(refill_per_second)
        self._clock = clock or time.monotonic
        self._buckets: dict[tuple[str, str], _Bucket] = {}
        self._lock = threading.Lock()

    def allow(self, *, client_id: str, bucket: str = "default") -> bool:
        key = (client_id or "-", bucket)
        now = self._clock()
        with self._lock:
            entry = self._buckets.get(key)
            if entry is None:
                entry = _Bucket(tokens=self._capacity, last_refill=now)
                self._buckets[key] = entry
            else:
                elapsed = max(0.0, now - entry.last_refill)
                entry.tokens = min(self._capacity,
                                   entry.tokens + elapsed * self._refill)
                entry.last_refill = now
            if entry.tokens >= 1.0:
                entry.tokens -= 1.0
                return True
            return False

    def reset(self) -> None:
        with self._lock:
            self._buckets.clear()
