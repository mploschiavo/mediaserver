"""Tests for ``MediaIntegrityScheduler`` — the daemon-thread driver
that runs boot-time enforce + steady-state reconcile.

We don't actually start a thread in most tests; instead we exercise
``run_one_pass()`` and the internal loop body directly with a
controllable sleep + time function so the test deterministically
finishes."""

from __future__ import annotations

import threading
import time
from typing import Any

import pytest

from media_stack.services.media_integrity.policy import ServarrPolicy
from media_stack.services.media_integrity.scheduler_hook import (
    DEFAULT_BOOT_DELAY_SEC,
    DEFAULT_RECONCILE_INTERVAL_SEC,
    MediaIntegrityScheduler,
    SchedulerConfig,
)
from media_stack.services.media_integrity.service import MediaIntegrityService


class _SpyService:
    """A drop-in for MediaIntegrityService that records calls."""

    def __init__(
        self,
        *,
        enforce_raises: Exception | None = None,
        reconcile_raises: Exception | None = None,
    ) -> None:
        self.enforce_calls: list[str] = []
        self.reconcile_calls: list[str] = []
        self._enforce_raises = enforce_raises
        self._reconcile_raises = reconcile_raises

    def enforce_config(self, *, actor: str = "system") -> dict:
        self.enforce_calls.append(actor)
        if self._enforce_raises:
            raise self._enforce_raises
        return {"servarr": {"total_fields_changed": 0}, "bazarr": None}

    def reconcile(self, *, actor: str = "system") -> dict:
        self.reconcile_calls.append(actor)
        if self._reconcile_raises:
            raise self._reconcile_raises
        return {"servarr": {"total_resolved": 0}, "bazarr": None}

    def status(self) -> dict:
        return {}


# ---------------------------------------------------------------------------


def test_run_one_pass_invokes_enforce_then_reconcile() -> None:
    spy = _SpyService()
    sched = MediaIntegrityScheduler(service=spy)  # type: ignore[arg-type]
    results = sched.run_one_pass()
    assert spy.enforce_calls == ["scheduler"]
    assert spy.reconcile_calls == ["scheduler"]
    assert "enforce" in results and "reconcile" in results


def test_run_one_pass_continues_when_enforce_raises() -> None:
    spy = _SpyService(enforce_raises=RuntimeError("503"))
    sched = MediaIntegrityScheduler(service=spy)  # type: ignore[arg-type]
    results = sched.run_one_pass()
    assert "error" in results["enforce"]
    assert spy.reconcile_calls == ["scheduler"]


def test_run_one_pass_continues_when_reconcile_raises() -> None:
    spy = _SpyService(reconcile_raises=RuntimeError("timeout"))
    sched = MediaIntegrityScheduler(service=spy)  # type: ignore[arg-type]
    results = sched.run_one_pass()
    assert "error" in results["reconcile"]


def test_default_config_uses_15_min_interval() -> None:
    cfg = SchedulerConfig()
    assert cfg.reconcile_interval_sec == DEFAULT_RECONCILE_INTERVAL_SEC == 900
    assert cfg.boot_delay_sec == DEFAULT_BOOT_DELAY_SEC == 120
    assert cfg.enforce_at_boot is True
    assert cfg.enforce_each_tick is False


def test_start_is_idempotent() -> None:
    spy = _SpyService()
    sched = MediaIntegrityScheduler(
        service=spy,  # type: ignore[arg-type]
        config=SchedulerConfig(boot_delay_sec=10_000, reconcile_interval_sec=10_000),
    )
    sched.start()
    first = sched._thread
    sched.start()
    assert sched._thread is first
    sched.stop()


def test_thread_runs_boot_enforce_then_reconcile_then_exits() -> None:
    """End-to-end with a real thread + tiny intervals + controlled
    time. We use a fake clock so the test finishes in milliseconds."""
    spy = _SpyService()

    # Fake time that advances on each sleep so the loop progresses
    # deterministically.
    state = {"now": 0.0}

    def fake_sleep(secs: float) -> None:
        state["now"] += max(secs, 0.001)

    def fake_time() -> float:
        return state["now"]

    cfg = SchedulerConfig(
        boot_delay_sec=1, reconcile_interval_sec=1, enforce_at_boot=True,
    )
    sched = MediaIntegrityScheduler(
        service=spy,  # type: ignore[arg-type]
        config=cfg,
        sleep_fn=fake_sleep,
        time_fn=fake_time,
    )
    # Start the thread, let it run a couple of cycles, then stop.
    sched.start()
    # Give the thread real wall-clock time to advance through enough
    # iterations of its (fake-time) loop to reach reconcile.
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        if spy.reconcile_calls:
            break
        time.sleep(0.05)
    sched.stop()
    assert spy.enforce_calls != []  # boot enforce ran
    assert spy.reconcile_calls != []  # at least one reconcile fired


def test_thread_respects_enforce_each_tick() -> None:
    spy = _SpyService()
    state = {"now": 0.0}

    def fake_sleep(secs: float) -> None:
        state["now"] += max(secs, 0.001)

    def fake_time() -> float:
        return state["now"]

    cfg = SchedulerConfig(
        boot_delay_sec=1, reconcile_interval_sec=1,
        enforce_at_boot=False, enforce_each_tick=True,
    )
    sched = MediaIntegrityScheduler(
        service=spy,  # type: ignore[arg-type]
        config=cfg,
        sleep_fn=fake_sleep,
        time_fn=fake_time,
    )
    sched.start()
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        if len(spy.enforce_calls) >= 1 and len(spy.reconcile_calls) >= 1:
            break
        time.sleep(0.05)
    sched.stop()
    # enforce_at_boot=False so the boot enforce was skipped, but each
    # tick fires reconcile + enforce.
    assert len(spy.enforce_calls) >= 1
    assert len(spy.reconcile_calls) >= 1


def test_stop_returns_without_blocking_if_thread_never_started() -> None:
    sched = MediaIntegrityScheduler(service=_SpyService())  # type: ignore[arg-type]
    sched.stop()  # should not raise


def test_wait_returns_true_when_stop_signalled() -> None:
    spy = _SpyService()
    sched = MediaIntegrityScheduler(
        service=spy,  # type: ignore[arg-type]
        sleep_fn=lambda s: None,
    )
    sched._stop_event.set()
    assert sched._wait(60) is True
