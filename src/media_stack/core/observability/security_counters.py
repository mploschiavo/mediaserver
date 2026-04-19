"""In-process counters for controller security events.

Tracks rejections that matter operationally:
  - sudo_fail               X-Sudo-Password mismatched
  - hmac_fail               webhook signature rejection
  - ip_lockout_trip         per-IP failed-login lockout kicked in
  - csrf_fail               CSRF token missing / mismatched
  - origin_reject           cookie-bearing POST with cross-origin Origin
  - auth_fail               any 401 (bearer / basic / cookie)
  - trusted_proxy_spoof     Remote-User header from outside trusted CIDR

Counters are plain ints guarded by a lock so every handler can
increment from request threads. Exposed via the existing /metrics
endpoint (Prometheus text) as counters named
``media_stack_<event>_total``.

Why not use prometheus_client? We don't ship it as a dep, and the
existing /metrics endpoint already generates its own text format
from internal state. Staying consistent with that avoids adding a
new dependency just for three extra counters.
"""

from __future__ import annotations

import threading
from typing import Iterable


_EVENTS: tuple[str, ...] = (
    "sudo_fail",
    "hmac_fail",
    "ip_lockout_trip",
    "csrf_fail",
    "origin_reject",
    "auth_fail",
    "trusted_proxy_spoof",
)


class SecurityCounters:
    """Thread-safe counter bag for security events.

    Module-level singleton ``security_counters`` is the canonical
    instance. Tests construct fresh instances to avoid leaking state.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counts: dict[str, int] = {k: 0 for k in _EVENTS}

    def incr(self, event: str, n: int = 1) -> None:
        """Increment an event. Unknown event names are accepted (and
        surfaced on /metrics) — the whitelist is just the default set
        so callers can extend without needing a code change here."""
        if n <= 0:
            return
        with self._lock:
            self._counts[event] = self._counts.get(event, 0) + int(n)

    def snapshot(self) -> dict[str, int]:
        with self._lock:
            return dict(self._counts)

    def event_names(self) -> Iterable[str]:
        with self._lock:
            return list(self._counts.keys())

    def reset(self) -> None:
        """Test helper: wipe all counters back to zero."""
        with self._lock:
            self._counts = {k: 0 for k in _EVENTS}


security_counters = SecurityCounters()
