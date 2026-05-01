"""Tests for ``infrastructure.promises.cooldown.CooldownTracker``.

Pin: backoff windows by status, persistence round-trip,
``consecutive_failures`` counter math, thread-safety contract (the
internal lock exists; we don't run race detectors here).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from media_stack.domain.services.promises import PromiseAttempt
from media_stack.infrastructure.promises.cooldown import CooldownTracker


def _attempt(pid: str, status: str, started_at: float = 1_000_000.0) -> PromiseAttempt:
    return PromiseAttempt(
        promise_id=pid,
        status=status,  # type: ignore[arg-type]
        started_at=started_at,
        elapsed_seconds=0.01,
    )


class TestCooldownWindows:
    def test_ok_status_never_in_cooldown(self, tmp_path: Path) -> None:
        # Probes recovering to ok should be re-evaluated next tick —
        # the invariant could break again. No cooldown.
        c = CooldownTracker(tmp_path / "state.json")
        c.record_attempt(_attempt("x", "ok", started_at=1000.0))
        assert not c.is_in_cooldown("x", now=1000.5)

    def test_failed_transient_uses_30s_window(self, tmp_path: Path) -> None:
        c = CooldownTracker(
            tmp_path / "state.json",
            transient_cooldown=30.0,
            permanent_cooldown=300.0,
        )
        c.record_attempt(_attempt("x", "failed_transient", started_at=1000.0))
        assert c.is_in_cooldown("x", now=1015.0)  # 15s in: still cooling
        assert not c.is_in_cooldown("x", now=1031.0)  # 31s: window elapsed

    def test_failed_permanent_uses_300s_window(self, tmp_path: Path) -> None:
        c = CooldownTracker(
            tmp_path / "state.json",
            transient_cooldown=30.0,
            permanent_cooldown=300.0,
        )
        c.record_attempt(_attempt("x", "failed_permanent", started_at=1000.0))
        assert c.is_in_cooldown("x", now=1100.0)  # 100s: still cooling
        assert not c.is_in_cooldown("x", now=1301.0)  # 301s: elapsed

    def test_unknown_treated_like_transient(self, tmp_path: Path) -> None:
        c = CooldownTracker(
            tmp_path / "state.json",
            transient_cooldown=30.0,
            permanent_cooldown=300.0,
        )
        c.record_attempt(_attempt("x", "unknown", started_at=1000.0))
        assert c.is_in_cooldown("x", now=1015.0)
        assert not c.is_in_cooldown("x", now=1031.0)

    def test_remaining_seconds_decrements(self, tmp_path: Path) -> None:
        c = CooldownTracker(
            tmp_path / "state.json",
            transient_cooldown=30.0,
            permanent_cooldown=300.0,
        )
        c.record_attempt(_attempt("x", "failed_transient", started_at=1000.0))
        assert c.remaining_cooldown_seconds("x", now=1010.0) == pytest.approx(20.0)
        assert c.remaining_cooldown_seconds("x", now=1031.0) == 0.0


class TestConsecutiveFailures:
    def test_counter_increments_on_repeated_failure(self, tmp_path: Path) -> None:
        c = CooldownTracker(tmp_path / "state.json")
        for i in range(3):
            c.record_attempt(_attempt("x", "failed_transient", started_at=1000.0 + i))
        assert c.last_attempt("x").consecutive_failures == 3

    def test_counter_resets_on_ok(self, tmp_path: Path) -> None:
        c = CooldownTracker(tmp_path / "state.json")
        c.record_attempt(_attempt("x", "failed_transient"))
        c.record_attempt(_attempt("x", "failed_transient"))
        c.record_attempt(_attempt("x", "ok"))
        assert c.last_attempt("x").consecutive_failures == 0

    def test_skipped_does_not_increment(self, tmp_path: Path) -> None:
        # Cooldown skips don't advance the counter — would cause
        # exponential cooldown extension by the time the operator
        # fixes the underlying issue.
        c = CooldownTracker(tmp_path / "state.json")
        c.record_attempt(_attempt("x", "failed_transient"))
        c.record_attempt(_attempt("x", "skipped_cooldown"))
        c.record_attempt(_attempt("x", "skipped_cooldown"))
        # consecutive count comes from the failure path; skipped
        # resets it (treated as "no failure this tick").
        assert c.last_attempt("x").consecutive_failures == 0


class TestPersistence:
    def test_save_and_load_round_trip(self, tmp_path: Path) -> None:
        path = tmp_path / "state.json"
        c1 = CooldownTracker(path)
        c1.record_attempt(_attempt("a", "ok", started_at=1000.0))
        c1.record_attempt(_attempt("b", "failed_transient", started_at=1100.0))
        c1.save()

        c2 = CooldownTracker(path)
        c2.load()
        a = c2.last_attempt("a")
        b = c2.last_attempt("b")
        assert a is not None and a.status == "ok"
        assert b is not None and b.status == "failed_transient"

    def test_missing_file_loads_clean(self, tmp_path: Path) -> None:
        c = CooldownTracker(tmp_path / "nope.json")
        c.load()
        assert c.last_attempt("x") is None

    def test_malformed_json_loads_clean(self, tmp_path: Path) -> None:
        # Operators editing the file by hand or a partial-write
        # mid-crash should not crash the controller. Logged + ignored.
        path = tmp_path / "state.json"
        path.write_text("not valid json {{")
        c = CooldownTracker(path)
        c.load()
        assert c.last_attempt("x") is None

    def test_save_creates_parent_dir(self, tmp_path: Path) -> None:
        # Fresh deploys may not have ``.controller/`` yet.
        path = tmp_path / "fresh" / "deploy" / "state.json"
        c = CooldownTracker(path)
        c.record_attempt(_attempt("x", "ok"))
        c.save()
        assert path.is_file()

    def test_atomic_write_via_tmp_rename(self, tmp_path: Path) -> None:
        # Write goes through a .tmp file; the .tmp shouldn't linger
        # after a successful save.
        path = tmp_path / "state.json"
        c = CooldownTracker(path)
        c.record_attempt(_attempt("x", "ok"))
        c.save()
        assert path.is_file()
        assert not (path.with_suffix(".json.tmp")).is_file()
        # Real JSON content
        loaded = json.loads(path.read_text())
        assert "attempts" in loaded
        assert "x" in loaded["attempts"]
