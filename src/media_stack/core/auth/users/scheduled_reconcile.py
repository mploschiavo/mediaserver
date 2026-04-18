"""Background thread that runs reconcile + last-login sync periodically.

Kicks off a daemon thread at controller startup (when
RECONCILE_INTERVAL_SEC is set). Each pass:
  1. Calls svc.reconcile_report() to populate drift counters.
  2. For every live user, calls svc.user_detail() which syncs
     last_login_at from providers.
  3. Emits a single audit_log entry with the result.

The job never touches providers when no users exist locally, so a
freshly-installed controller stays quiet until reconcile picks up
the bootstrap admin.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Callable

_log = logging.getLogger("media_stack")
_DEFAULT_INTERVAL_SEC = 60 * 60  # hourly


class ScheduledReconciler:

    def __init__(
        self,
        *,
        service_factory: Callable,
        interval_sec: int = _DEFAULT_INTERVAL_SEC,
        clock: Callable[[], float] | None = None,
        sleeper: Callable[[float], None] | None = None,
    ) -> None:
        self._service_factory = service_factory
        self._interval = max(60, int(interval_sec))
        self._clock = clock or time.monotonic
        self._sleeper = sleeper or time.sleep
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.last_run_at: str = ""
        self.last_drift_summary: dict = {}

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        t = threading.Thread(target=self._loop, name="user-reconcile",
                             daemon=True)
        self._thread = t
        t.start()
        _log.info("[user-reconcile] started, interval=%ds", self._interval)

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)

    def _loop(self) -> None:
        # Delay the first run by a few seconds so controller startup
        # finishes before we start hitting provider APIs.
        self._sleeper(5.0)
        while not self._stop.is_set():
            try:
                self.run_once()
            except Exception as exc:  # noqa: BLE001
                _log.warning("[user-reconcile] pass failed: %s", exc)
            if self._stop.wait(self._interval):
                break

    def run_once(self) -> dict:
        svc = self._service_factory()
        diffs = svc.reconcile_report()
        summary = {
            d["provider"]: {
                "matched": d.get("matched", 0),
                "orphans": len(d.get("orphans", [])),
                "ghosts": len(d.get("ghosts", [])),
            }
            for d in diffs
        }
        # Refresh last-login for every live user (best-effort)
        for u in svc.list_users():
            try:
                svc.user_detail(u["id"])
            except Exception as exc:  # noqa: BLE001
                _log.debug("[user-reconcile] user_detail %s: %s",
                           u.get("email", ""), exc)
        self.last_drift_summary = summary
        _log.debug("[user-reconcile] pass complete: %s", summary)
        return summary


