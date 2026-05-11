"""ControllerSchedulerSeedService — seed the controller's default schedules.

ADR-0015 Phase 7e. Pre-Phase-7e ~150 LoC of default-schedule
seeding lived inline inside the 683-LoC ``_run_serve`` god
method as the ``_scheduler_loop`` closure. The seeding is
load-bearing on first compose deploys: without it the scheduler
service stores nothing and the recurring cleanup jobs never fire.

This service encapsulates the seed declaration table so:

* Adding a new default schedule means appending one entry to
  :data:`_DEFAULT_SCHEDULE_SEEDS`; the seed loop is generic.
* Each seed entry documents why the schedule exists + what the
  effective cron-equivalence is.
* The action-dispatch loop in :class:`ControllerActionDispatcher`
  can compose this service without taking the kitchen-sink
  ``_run_serve`` closure capture chain along for the ride.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable


_HOUR_SECONDS = 3600
_QUARTER_HOUR_SECONDS = 15 * 60
_HALF_HOUR_SECONDS = 30 * 60
_SIX_HOUR_SECONDS = 6 * _HOUR_SECONDS
_DAY_SECONDS = 24 * _HOUR_SECONDS


@dataclass(frozen=True)
class _ScheduleSeed:
    """One default schedule entry — action + cadence + operator-readable label."""

    action: str
    interval_seconds: int
    label: str


_DEFAULT_SCHEDULE_SEEDS: tuple[_ScheduleSeed, ...] = (
    # Hourly stalled/orphan torrent cleanup. The aggressive defaults in
    # _guardrail_config.py only apply when a run actually fires; without
    # this seed, nothing fires on compose.
    _ScheduleSeed(
        action="run-media-hygiene",
        interval_seconds=_HOUR_SECONDS,
        label="Auto-cleanup stalled / orphaned downloads (hourly)",
    ),
    # Periodic scan of completed-downloads paths so files the user
    # dropped into qBit directly (or that the *arr missed via webhook)
    # get picked up. Each *arr has DownloadedMoviesScan/DownloadedEpisodesScan
    # that walks its configured download path and imports anything it
    # recognizes by metadata. (v1.0.144)
    _ScheduleSeed(
        action="scan-completed-downloads",
        interval_seconds=_QUARTER_HOUR_SECONDS,
        label="Scan completed downloads into *arr libraries (15m)",
    ),
    # Hourly heartbeat for mass-search-throttled. The adapter itself
    # is adaptive — empty installs run aggressively, healthy installs
    # short-circuit as a no-op. (v1.0.148)
    _ScheduleSeed(
        action="mass-search-throttled",
        interval_seconds=_HOUR_SECONDS,
        label="Adaptive search for missing content (hourly)",
    ),
    # Catches "qBit-completed but *arr never imported" failure mode
    # (Shelter + Strangers incidents, v1.0.150). Two paths: queue
    # entries stuck "downloading" forever, AND orphan files in
    # /data/torrents/completed/ that the *arr's queue doesn't know
    # about. Both force-import via /api/v3/manualimport.
    _ScheduleSeed(
        action="recover-stuck-imports",
        interval_seconds=_HALF_HOUR_SECONDS,
        label="Recover stuck/orphan downloads (every 30m)",
    ),
    # Media-integrity jobs (v1.0.184). Replaces the legacy in-process
    # daemon-thread scheduler. Each cadence maps to a contract-registered
    # job; manual SPA triggers reach the same job through handlers_post
    # so history is unified in /api/jobs.history[].
    _ScheduleSeed(
        action="media-integrity:scan",
        interval_seconds=_QUARTER_HOUR_SECONDS,
        label="Media-integrity status scan (every 15m)",
    ),
    _ScheduleSeed(
        action="media-integrity:reconcile",
        interval_seconds=_SIX_HOUR_SECONDS,
        label="Media-integrity duplicate reconcile (every 6h)",
    ),
    _ScheduleSeed(
        action="media-integrity:enforce-config",
        interval_seconds=_DAY_SECONDS,
        label="Media-integrity policy enforcement (daily)",
    ),
)


class ControllerSchedulerSeedService:
    """Seed the controller's default schedules + report what fired.

    Idempotent: each seed entry checks for an existing matching
    schedule before adding, so restarts don't duplicate.
    """

    def __init__(self, log: Callable[[str], None]) -> None:
        self._log = log

    def seed_defaults(self) -> None:
        """Walk :data:`_DEFAULT_SCHEDULE_SEEDS` and add any that aren't already registered."""
        try:
            from media_stack.api.services import scheduler as _sched
            existing = {
                s.get("action")
                for s in _sched.get_schedules().get("schedules") or []
            }
            for seed in _DEFAULT_SCHEDULE_SEEDS:
                if seed.action in existing:
                    continue
                _sched.add_schedule(
                    action=seed.action,
                    interval_seconds=seed.interval_seconds,
                    label=seed.label,
                )
                self._log(
                    f"[INFO] Scheduler: seeded default '{seed.action}' "
                    f"(every {seed.interval_seconds}s)"
                )
        except Exception as exc:  # noqa: BLE001 — boot-time, fail-open
            self._log(f"[WARN] Scheduler seed failed: {exc}")


__all__ = ["ControllerSchedulerSeedService"]
