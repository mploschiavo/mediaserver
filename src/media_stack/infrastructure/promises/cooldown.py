"""Cooldown state for the orchestrator — ADR-0003 Phase 4b.

Tracks per-promise backoff state across ticks. Persisted to JSON in
``.controller/promise_state.json`` (sibling to ``run-history.jsonl``)
so a controller restart doesn't immediately re-evaluate every
recently-failed promise.

Backoff schedule (deliberately conservative — promises probe cheap
HTTP/file/lifecycle calls; the cost of an extra evaluation is tiny
compared to the cost of NOT noticing a real regression):

  * After ``ok``: no cooldown — always re-probe next tick. The
    invariant might break.
  * After ``failed_transient`` or ``unknown``: 30s cooldown. Service
    warming up; quick retry catches transient warmup races.
  * After ``failed_permanent``: 300s cooldown. Operator action
    expected; pounding on the same broken thing every 60s wastes
    log volume.

The schedule is config-driven via constructor args so tests can
override without monkey-patching constants.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any, Mapping, Optional

from media_stack.domain.services.promises import PromiseAttempt, PromiseStatus


logger = logging.getLogger(__name__)


_DEFAULT_TRANSIENT_COOLDOWN_SECONDS = 30.0
_DEFAULT_PERMANENT_COOLDOWN_SECONDS = 300.0


def default_state_path() -> Path:
    """Sibling to ``run-history.jsonl`` so existing PVC/bind mounts
    cover both files automatically."""
    config_root = (os.environ.get("CONFIG_ROOT") or "").strip()
    base = Path(config_root) if config_root else Path("config")
    return base / ".controller" / "promise_state.json"


class CooldownTracker:
    """In-memory cooldown state with lazy JSON persistence.

    Thread-safe: probes run in parallel via ``ThreadPoolExecutor``
    and may all want to update the tracker. A single mutex protects
    the dict; the contention window is microseconds (one probe's
    update). Persistence happens explicitly via ``save()`` — the
    orchestrator calls it once at the end of each tick, not on every
    update, so JSON write is amortized across all promises evaluated
    that tick.
    """

    def __init__(
        self,
        path: Path | None = None,
        *,
        transient_cooldown: float = _DEFAULT_TRANSIENT_COOLDOWN_SECONDS,
        permanent_cooldown: float = _DEFAULT_PERMANENT_COOLDOWN_SECONDS,
    ) -> None:
        self._path = path or default_state_path()
        self._lock = threading.Lock()
        self._attempts: dict[str, PromiseAttempt] = {}
        self._transient_cooldown = float(transient_cooldown)
        self._permanent_cooldown = float(permanent_cooldown)
        self._loaded = False

    # --- persistence ----------------------------------------------------

    def load(self) -> None:
        """Read existing state from disk. Tolerates a missing file
        (fresh deploy) and a malformed file (logged + ignored — the
        cost of starting from zero is one extra evaluation cycle, much
        cheaper than crashing the controller)."""
        with self._lock:
            self._loaded = True
            if not self._path.is_file():
                return
            try:
                raw = json.loads(self._path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError) as exc:
                logger.warning(
                    "promise_state.json malformed; starting fresh: %s", exc,
                )
                return
            entries = raw.get("attempts") or {}
            if not isinstance(entries, dict):
                return
            for pid, data in entries.items():
                if not isinstance(data, dict):
                    continue
                try:
                    self._attempts[str(pid)] = PromiseAttempt.from_dict(data)
                except (TypeError, ValueError) as exc:
                    logger.debug(
                        "skipping malformed entry %s: %s", pid, exc,
                    )

    def save(self) -> None:
        """Atomic-ish write to disk. Best-effort — a failure here
        means we lose cooldown state on next restart, which is one
        extra evaluation cycle, not a correctness issue."""
        with self._lock:
            try:
                self._path.parent.mkdir(parents=True, exist_ok=True)
                payload = {
                    "version": 1,
                    "saved_at": time.time(),
                    "attempts": {
                        pid: attempt.to_dict()
                        for pid, attempt in self._attempts.items()
                    },
                }
                tmp = self._path.with_suffix(self._path.suffix + ".tmp")
                tmp.write_text(json.dumps(payload), encoding="utf-8")
                tmp.replace(self._path)
            except OSError as exc:
                logger.warning("promise_state.json save failed: %s", exc)

    # --- query ----------------------------------------------------------

    def last_attempt(self, promise_id: str) -> Optional[PromiseAttempt]:
        with self._lock:
            return self._attempts.get(promise_id)

    def is_in_cooldown(self, promise_id: str, now: float) -> bool:
        """Returns True when the most recent attempt's cooldown window
        has not yet elapsed. The orchestrator skips the promise this
        tick (status=skipped_cooldown) — but does NOT advance any
        counters, so the cooldown timer doesn't keep restarting."""
        with self._lock:
            attempt = self._attempts.get(promise_id)
        if attempt is None:
            return False
        delta = now - attempt.started_at
        if attempt.status == "failed_transient" or attempt.status == "unknown":
            return delta < self._transient_cooldown
        if attempt.status == "failed_permanent":
            return delta < self._permanent_cooldown
        # ok / skipped_* — no cooldown; always re-probe.
        return False

    def remaining_cooldown_seconds(self, promise_id: str, now: float) -> float:
        """For DEBUG logging — how much longer until the cooldown
        window elapses. Returns ``0.0`` if not in cooldown."""
        with self._lock:
            attempt = self._attempts.get(promise_id)
        if attempt is None:
            return 0.0
        if attempt.status == "failed_transient" or attempt.status == "unknown":
            window = self._transient_cooldown
        elif attempt.status == "failed_permanent":
            window = self._permanent_cooldown
        else:
            return 0.0
        elapsed = now - attempt.started_at
        return max(0.0, window - elapsed)

    # --- mutate ---------------------------------------------------------

    def record_attempt(self, attempt: PromiseAttempt) -> PromiseAttempt:
        """Record this evaluation. Computes ``consecutive_failures``
        from the previous attempt's count: a fresh ``ok`` resets to
        zero; a continued failure increments. The orchestrator passes
        in an attempt without ``consecutive_failures``; this method
        returns a corrected one (frozen dataclass — replace, don't
        mutate)."""
        with self._lock:
            prev = self._attempts.get(attempt.promise_id)
            failing = attempt.status not in ("ok", "skipped_cooldown",
                                             "skipped_platform")
            if failing:
                consecutive = (
                    (prev.consecutive_failures + 1) if prev is not None else 1
                )
            else:
                consecutive = 0
            updated = PromiseAttempt(
                promise_id=attempt.promise_id,
                status=attempt.status,
                started_at=attempt.started_at,
                elapsed_seconds=attempt.elapsed_seconds,
                detail=attempt.detail,
                probe_evidence=attempt.probe_evidence,
                ensurer_fired=attempt.ensurer_fired,
                ensurer_attempts=attempt.ensurer_attempts,
                consecutive_failures=consecutive,
            )
            self._attempts[attempt.promise_id] = updated
            return updated

    def all_attempts(self) -> Mapping[str, PromiseAttempt]:
        with self._lock:
            return dict(self._attempts)


__all__ = ["CooldownTracker", "default_state_path"]
