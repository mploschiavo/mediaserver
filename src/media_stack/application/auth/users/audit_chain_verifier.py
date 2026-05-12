"""Background verifier for audit-log hash-chain integrity.

The audit log is hash-chained: every entry's ``hash`` is
``sha256(prev_hash || canonical_body)``. If an attacker (or disk
corruption) mutates an entry in place, the chain breaks at that
point and every subsequent entry's hash fails to verify.

This module runs ``AuditLog.verify_chain()`` on a timer and records
the latest result + any tamper detection time. It's exposed via
``GET /api/audit-log/verify`` (synchronous, authoritative) and via
the background thread that logs a loud warning on failure.

We DO NOT try to repair a broken chain — the whole point of the
chain is to detect tampering. Repair is an operator decision.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Callable

_log = logging.getLogger("media_stack")
_DEFAULT_INTERVAL_SEC = 10 * 60  # 10 min
_FIRST_RUN_DELAY_SEC = 30


class AuditChainVerifier:
    """Daemon thread that periodically runs ``verify_chain()``."""

    def __init__(
        self,
        *,
        audit_factory: Callable,
        interval_sec: int = _DEFAULT_INTERVAL_SEC,
        clock: Callable[[], float] | None = None,
        sleeper: Callable[[float], None] | None = None,
        alert_fn: Callable[[str], None] | None = None,
    ) -> None:
        self._factory = audit_factory
        self._interval = max(60, int(interval_sec))
        self._clock = clock or time.monotonic
        self._sleeper = sleeper or time.sleep
        self._alert_fn = alert_fn
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.last_checked_at: float = 0.0
        self.last_ok: bool = True
        self.last_detail: str = ""
        self.first_tamper_at: float = 0.0

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        t = threading.Thread(
            target=self._loop, name="audit-chain-verify", daemon=True,
        )
        self._thread = t
        t.start()
        _log.info("[audit-verify] started, interval=%ds", self._interval)

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)

    def _loop(self) -> None:
        self._sleeper(_FIRST_RUN_DELAY_SEC)
        while not self._stop.is_set():
            try:
                self.verify_once()
            except Exception as exc:  # noqa: BLE001
                _log.warning("[audit-verify] pass failed: %s", exc)
            if self._stop.wait(self._interval):
                break

    def verify_once(self) -> tuple[bool, str]:
        audit = self._factory()
        ok, detail = audit.verify_chain()
        prev_ok = self.last_ok
        prev_detail = self.last_detail
        self.last_checked_at = self._clock()
        self.last_ok = bool(ok)
        self.last_detail = str(detail or "")
        if not ok:
            self._handle_failure(detail, prev_ok, prev_detail)
        elif not prev_ok:
            # Recovery (operator archived the corrupted log).
            # Emit a single line so the dashboard can show the
            # state transition; the historical ``first_tamper_at``
            # timestamp is preserved for forensics.
            _log.info(
                "[audit-verify] chain intact (recovered after "
                "previous tamper at %.0f)", self.first_tamper_at,
            )
        return self.last_ok, self.last_detail

    def _handle_failure(
        self, detail: str, prev_ok: bool, prev_detail: str,
    ) -> None:
        """Record + alert on chain corruption, but suppress
        duplicate-error spam.

        Only log + alert when the state transitions (clean → broken)
        or when the corruption signature itself changes (a NEW break,
        e.g. the operator archived the previous corruption and a
        fresh race introduced another). Re-logging the same
        ``entry N: prev_hash mismatch`` every 10 minutes for weeks
        just fills the dashboard.
        """
        first_detection = self.first_tamper_at == 0.0
        if first_detection:
            self.first_tamper_at = self.last_checked_at
        detail_changed = self.last_detail != prev_detail
        if not (first_detection or prev_ok or detail_changed):
            return
        _log.error("[audit-verify] CHAIN CORRUPTION DETECTED: %s", detail)
        self._dispatch_alert(detail)

    def _dispatch_alert(self, detail: str) -> None:
        if self._alert_fn is None:
            return
        try:
            self._alert_fn(detail)
        except Exception as exc:  # noqa: BLE001
            _log.debug(
                "[DEBUG] audit-verify alert_fn raised: %s", exc,
            )

    def snapshot(self) -> dict:
        return {
            "last_checked_at": self.last_checked_at,
            "last_ok": self.last_ok,
            "last_detail": self.last_detail,
            "first_tamper_at": self.first_tamper_at,
            "interval_seconds": self._interval,
        }
