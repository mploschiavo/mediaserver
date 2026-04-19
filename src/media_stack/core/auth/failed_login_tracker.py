"""In-memory failed-login counter with cooldown.

Used to raise an alert (audit log entry + optional webhook) when a
single account sees N failures in a short window — a crude but
effective brute-force signal. Per-IP counters are kept separately by
the existing RateLimiter; this one is keyed by username so an attacker
who rotates IPs still trips the alarm.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field


@dataclass
class _Window:
    count: int = 0
    first_failure_at: float = 0.0
    alerted: bool = False


@dataclass
class FailedLoginTracker:
    threshold: int = 5
    window_seconds: int = 5 * 60
    cooldown_seconds: int = 60 * 60

    def __post_init__(self) -> None:
        self._lock = threading.Lock()
        self._windows: dict[str, _Window] = {}

    def register_failure(self, username: str, *, now: float | None = None
                         ) -> tuple[bool, int]:
        """Record a failure. Return (alert_fired, total_in_window).

        ``alert_fired`` is True the first time a window crosses the
        threshold; subsequent failures in the same window return False
        so callers don't re-alert until after ``cooldown_seconds``.
        """
        clock = now if now is not None else time.time()
        key = (username or "").strip().lower() or "-"
        with self._lock:
            w = self._windows.get(key)
            if w is None or clock - w.first_failure_at > self.window_seconds:
                w = _Window(count=1, first_failure_at=clock, alerted=False)
                self._windows[key] = w
                return False, 1
            w.count += 1
            if (w.count >= self.threshold and not w.alerted
                    and clock - w.first_failure_at <= self.window_seconds):
                w.alerted = True
                return True, w.count
            if w.alerted and clock - w.first_failure_at > self.cooldown_seconds:
                # Cooldown elapsed — start a new window
                w.count = 1
                w.first_failure_at = clock
                w.alerted = False
            return False, w.count

    def register_success(self, username: str) -> None:
        key = (username or "").strip().lower() or "-"
        with self._lock:
            self._windows.pop(key, None)

    def is_locked(self, key_raw: str, *, now: float | None = None) -> bool:
        """True when ``key_raw`` has tripped the threshold and is still
        inside the cooldown window. Used to reject auth attempts from
        an IP that has already burned through its failure budget,
        without adding a separate rate limiter.
        """
        clock = now if now is not None else time.time()
        key = (key_raw or "").strip().lower() or "-"
        with self._lock:
            w = self._windows.get(key)
            if w is None or not w.alerted:
                return False
            return (clock - w.first_failure_at) <= self.cooldown_seconds

    def snapshot(self) -> dict[str, dict]:
        with self._lock:
            return {
                k: {"count": w.count, "alerted": w.alerted,
                    "first_failure_at": w.first_failure_at}
                for k, w in self._windows.items()
            }
