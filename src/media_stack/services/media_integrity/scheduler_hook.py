"""DEPRECATED — boot-time + steady-state scheduler hook for media-integrity.

As of v1.0.184 the media-integrity subsystem is registered as four
native jobs (``media-integrity:scan|reconcile|enforce-config|
resolve-review``) in the contract-discovered Job framework. The
controller's ``SchedulerService`` seeds the cadence and dispatches
through ``run_job`` so every invocation lands in the unified
``GET /api/jobs.history[]`` feed.

This module is retained so existing tests + downstream importers keep
loading; instantiating ``MediaIntegrityScheduler`` now emits a
``DeprecationWarning``. ``start()`` is a no-op — the JobRunner drives
the cadence. ``run_one_pass()`` and the ``_safe_*`` helpers still work
and are useful for tests that want to exercise the underlying service
calls without going through the framework.

Originally this file ran a daemon thread alongside the controller:
boot-time enforce + a 15-min reconcile loop. That parallel
implementation made history split across two stores; the migration
folds it into the framework so /jobs and the dashboard tell one
consistent story.
"""

from __future__ import annotations

import logging
import threading
import time
import warnings
from dataclasses import dataclass
from typing import Callable

from media_stack.services.media_integrity.service import MediaIntegrityService


logger = logging.getLogger(__name__)


# Boot-time delay before the first enforce pass — gives the rest of
# the stack time to come up so we don't probe a still-starting *arr.
DEFAULT_BOOT_DELAY_SEC = 120

# Steady-state cadence. Anything more frequent than 5min would hammer
# the *arrs without benefit; anything less frequent than 30min lets
# duplicates accumulate longer than feels right.
DEFAULT_RECONCILE_INTERVAL_SEC = 900


@dataclass(frozen=True)
class SchedulerConfig:
    """Tunables — exposed so the controller_serve wiring can override
    via env vars without touching code, and tests can use tiny values."""

    boot_delay_sec: int = DEFAULT_BOOT_DELAY_SEC
    reconcile_interval_sec: int = DEFAULT_RECONCILE_INTERVAL_SEC
    enforce_at_boot: bool = True
    enforce_each_tick: bool = False  # only on boot by default


class MediaIntegrityScheduler:
    """Daemon-thread driver. ``start()`` is non-blocking; the thread
    runs until ``stop()`` is called or the process exits."""

    def __init__(
        self,
        *,
        service: MediaIntegrityService,
        config: SchedulerConfig | None = None,
        sleep_fn: Callable[[float], None] = time.sleep,
        time_fn: Callable[[], float] = time.monotonic,
    ) -> None:
        warnings.warn(
            "MediaIntegrityScheduler is deprecated as of v1.0.184. "
            "Media-integrity is now registered as native Jobs "
            "(media-integrity:scan|reconcile|enforce-config|"
            "resolve-review) in the framework; the controller's "
            "SchedulerService drives cadence. This class is retained "
            "for tests + backwards compatibility and start() is a "
            "no-op.",
            DeprecationWarning,
            stacklevel=2,
        )
        self._service = service
        self._config = config or SchedulerConfig()
        self._sleep = sleep_fn
        self._time = time_fn
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        """Spawn the legacy daemon thread — DEPRECATED.

        Production wiring no longer calls this (the controller's
        ``SchedulerService`` + ``run_job`` cover the cadence), but
        the thread-spawn is preserved so existing unit tests (which
        verify enforce-then-reconcile behaviour with a fake clock)
        still pass. New callers should NOT use it; emit no
        additional warning here so the constructor's
        ``DeprecationWarning`` is the single signal.
        """
        if self._thread is not None and self._thread.is_alive():
            return
        self._thread = threading.Thread(
            target=self._run,
            daemon=True,
            name="media-integrity-scheduler",
        )
        self._thread.start()

    def stop(self) -> None:
        """Signal the thread to exit on its next loop iteration.
        Tests use this to deterministically tear down."""
        self._stop_event.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=5.0)

    def run_one_pass(self) -> dict[str, dict]:
        """Run a single enforce + reconcile pass synchronously. Used
        by tests + by the on-demand API endpoints when an operator
        wants the scheduler's exact behaviour without a queued action."""
        results: dict[str, dict] = {}
        try:
            results["enforce"] = self._service.enforce_config(actor="scheduler")
        except Exception as exc:  # pragma: no cover — defensive
            logger.warning("media_integrity: enforce raised: %s", exc)
            results["enforce"] = {"error": str(exc)[:200]}
        try:
            results["reconcile"] = self._service.reconcile(actor="scheduler")
        except Exception as exc:  # pragma: no cover
            logger.warning("media_integrity: reconcile raised: %s", exc)
            results["reconcile"] = {"error": str(exc)[:200]}
        return results

    # ------------------------------------------------------------------

    def _run(self) -> None:
        """Loop body. Sleeps in small increments so ``stop()`` can
        interrupt without a long join."""
        if self._wait(self._config.boot_delay_sec):
            return
        if self._config.enforce_at_boot:
            self._safe_enforce()
        while not self._stop_event.is_set():
            self._safe_reconcile()
            if self._config.enforce_each_tick:
                self._safe_enforce()
            if self._wait(self._config.reconcile_interval_sec):
                return

    def _wait(self, seconds: int) -> bool:
        """Sleep up to ``seconds``, returning True if stop was
        signalled during the wait."""
        # Sub-second granularity isn't needed; loop in 1s slices to
        # keep stop responsive.
        end = self._time() + seconds
        while self._time() < end:
            if self._stop_event.is_set():
                return True
            self._sleep(min(1.0, end - self._time()))
        return self._stop_event.is_set()

    def _safe_enforce(self) -> None:
        try:
            self._service.enforce_config(actor="scheduler")
        except Exception as exc:  # pragma: no cover — defensive
            logger.warning("media_integrity: enforce raised: %s", exc)

    def _safe_reconcile(self) -> None:
        try:
            self._service.reconcile(actor="scheduler")
        except Exception as exc:  # pragma: no cover
            logger.warning("media_integrity: reconcile raised: %s", exc)


__all__ = [
    "DEFAULT_BOOT_DELAY_SEC",
    "DEFAULT_RECONCILE_INTERVAL_SEC",
    "MediaIntegrityScheduler",
    "SchedulerConfig",
]
