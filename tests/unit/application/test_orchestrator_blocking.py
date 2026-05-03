"""Tests for ADR-0005 Phase 1 — bootstrap_blocking + tick_until_done.

Covers:

  * ``Promise.bootstrap_blocking`` defaults to ``True`` and is
    honoured by the YAML loader.
  * ``BlockingLoopGuard`` predicates on a synthetic attempts map.
  * ``PromiseOrchestrator.tick_until_done`` exits via each of the
    three documented termination paths (all-ok, permanent-fail,
    timeout) and never busy-spins.
  * The module-level ``satisfy_promises_blocking`` shim delegates
    cleanly so contract YAMLs that name a function path resolve.
"""

from __future__ import annotations

from typing import Any

import pytest

from media_stack.application.services.orchestrator import (
    BlockingLoopGuard,
    PromiseOrchestrator,
    satisfy_promises_blocking,
)
from media_stack.domain.services.promises import (
    BlockingSummary,
    HttpJsonProbe,
    LifecycleEnsurer,
    Promise,
    PromiseAttempt,
    TickSummary,
)


# ----------------------------------------------------------------------
# Test doubles — synthetic Promise + TickSummary so the loop runs
# without touching CooldownTracker / dispatcher / network.
# ----------------------------------------------------------------------


def _promise(
    pid: str,
    *,
    bootstrap_blocking: bool = True,
    platforms: tuple[str, ...] = ("compose", "k8s"),
) -> Promise:
    return Promise(
        id=pid,
        description=f"synthetic {pid}",
        platforms=platforms,
        probe=HttpJsonProbe(service=pid, path="/", auth="none", assert_expr=""),
        ensurer=LifecycleEnsurer(service=pid, method="ensure"),
        bootstrap_blocking=bootstrap_blocking,
    )


def _attempt(pid: str, status: str) -> PromiseAttempt:
    return PromiseAttempt(
        promise_id=pid,
        status=status,  # type: ignore[arg-type]
        started_at=0.0,
        elapsed_seconds=0.0,
        detail="",
    )


def _summary_from(attempts: list[PromiseAttempt]) -> TickSummary:
    """Build a synthetic TickSummary the loop can read attempts from."""
    return TickSummary.from_attempts(
        started_at=0.0,
        skipped_platform=0,
        attempts={a.promise_id: a for a in attempts},
        elapsed_seconds=0.0,
    )


class _FakeClock:
    """Deterministic monotonic + sleep stand-ins for the blocking
    loop. ``advance_per_sleep`` sets how much wall-clock to charge
    on every ``sleep(...)`` call, so tests can drive the deadline
    forward without real waits."""

    def __init__(self, *, advance_per_sleep: float = 1.0) -> None:
        self._t = 0.0
        self._advance_per_sleep = advance_per_sleep
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self._t

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        # Charge the requested duration AND the advance, so the loop
        # makes deterministic forward progress per iteration.
        self._t += max(seconds, self._advance_per_sleep)


class _ScriptedOrchestrator(PromiseOrchestrator):
    """PromiseOrchestrator subclass whose ``tick`` returns
    pre-scripted summaries. Lets the loop test exercise termination
    paths without driving real probes."""

    def __init__(self, scripts: list[TickSummary]) -> None:
        # Avoid the parent's CooldownTracker.load() path — tests
        # don't need persistence, and we don't want a /srv-config
        # write attempt during test runs.
        self._registry = []
        self._resolver = object()  # never used by the override
        self._cooldown = object()
        self._history_emit = lambda *a, **k: None
        self._status = object()
        self._workers = 1
        self._slow_probe_warn_seconds = 1.0
        self._probe_batch_timeout_seconds = 30.0
        self._repeated_transient_warn_threshold = 3
        self._scripts = list(scripts)
        self.tick_calls: int = 0

    def tick(self, **_kwargs: Any) -> TickSummary:  # type: ignore[override]
        self.tick_calls += 1
        if not self._scripts:
            # Loop the last summary forever — let timeout handle it.
            raise AssertionError(
                "scripted tick exhausted; test should have terminated already",
            )
        return self._scripts.pop(0)


# ======================================================================
# Promise.bootstrap_blocking field
# ======================================================================


class TestPromiseBootstrapBlockingField:
    def test_defaults_to_true(self) -> None:
        # Authors writing a new promise YAML without the field
        # opt INTO bootstrap-blocking by default; the migration
        # plan flips long-running operational ones to False
        # explicitly.
        p = _promise("jellyfin-running")
        assert p.bootstrap_blocking is True

    def test_explicit_false_round_trips(self) -> None:
        p = _promise("mass-search", bootstrap_blocking=False)
        assert p.bootstrap_blocking is False

    def test_loader_rejects_non_bool(self) -> None:
        # The YAML loader must reject ``bootstrap_blocking: "true"``
        # (string) — operators frequently quote bools in YAML and
        # we don't want a silent always-True coercion.
        from media_stack.domain.services.promises import (
            PromiseRegistryError,
        )
        from media_stack.infrastructure.promises.registry import (
            _parse_promise,
        )

        bad_entry = {
            "id": "foo",
            "description": "",
            "platforms": ["compose"],
            "probe": {"kind": "http_status", "service": "foo",
                      "path": "/", "assert_expr": "ok"},
            "ensured_by": {"type": "lifecycle", "service": "foo",
                           "method": "ensure"},
            "bootstrap_blocking": "true",
        }
        with pytest.raises(PromiseRegistryError) as excinfo:
            _parse_promise(bad_entry)
        assert "bootstrap_blocking" in str(excinfo.value)


# ======================================================================
# BlockingLoopGuard predicates
# ======================================================================


class TestBlockingLoopGuard:
    def test_for_platform_filters_by_blocking_and_platform(self) -> None:
        registry = [
            _promise("blocking-compose", bootstrap_blocking=True,
                     platforms=("compose",)),
            _promise("blocking-k8s-only", bootstrap_blocking=True,
                     platforms=("k8s",)),
            _promise("non-blocking", bootstrap_blocking=False,
                     platforms=("compose", "k8s")),
        ]
        guard = BlockingLoopGuard.for_platform(registry, platform="compose")
        assert guard.blocking_ids == frozenset({"blocking-compose"})

    def test_first_permanent_failure_returns_first_match(self) -> None:
        guard = BlockingLoopGuard(frozenset({"a", "b"}))
        attempts = {
            "a": _attempt("a", "ok"),
            "b": _attempt("b", "failed_permanent"),
        }
        assert guard.first_permanent_failure(attempts) == "b"

    def test_first_permanent_failure_returns_empty_when_none(self) -> None:
        guard = BlockingLoopGuard(frozenset({"a", "b"}))
        attempts = {
            "a": _attempt("a", "ok"),
            "b": _attempt("b", "failed_transient"),
        }
        assert guard.first_permanent_failure(attempts) == ""

    def test_is_satisfied_only_when_every_blocking_is_ok(self) -> None:
        guard = BlockingLoopGuard(frozenset({"a", "b"}))
        # Missing entry — not satisfied.
        assert not guard.is_satisfied({"a": _attempt("a", "ok")})
        # All ok — satisfied.
        assert guard.is_satisfied({
            "a": _attempt("a", "ok"),
            "b": _attempt("b", "ok"),
        })
        # One transient — not satisfied.
        assert not guard.is_satisfied({
            "a": _attempt("a", "ok"),
            "b": _attempt("b", "failed_transient"),
        })

    def test_empty_blocking_set_is_vacuously_satisfied(self) -> None:
        # Registries with zero blocking promises (Phase 1 lands the
        # field but no contracts have been annotated yet) should
        # exit immediately rather than loop.
        guard = BlockingLoopGuard(frozenset())
        assert guard.is_satisfied({})

    def test_ok_count_matches_satisfied_blocking_promises(self) -> None:
        guard = BlockingLoopGuard(frozenset({"a", "b", "c"}))
        attempts = {
            "a": _attempt("a", "ok"),
            "b": _attempt("b", "failed_transient"),
            "c": _attempt("c", "ok"),
        }
        assert guard.ok_count(attempts) == 2


# ======================================================================
# PromiseOrchestrator.tick_until_done — termination paths
# ======================================================================


class TestTickUntilDoneTermination:
    def test_returns_immediately_when_blocking_set_satisfied_first_tick(
        self,
    ) -> None:
        registry = [_promise("a"), _promise("b")]
        scripted = [_summary_from([
            _attempt("a", "ok"),
            _attempt("b", "ok"),
        ])]
        orch = _ScriptedOrchestrator(scripted)
        orch._registry = registry
        clock = _FakeClock()

        result = orch.tick_until_done(
            timeout_seconds=240.0,
            tick_interval_seconds=5.0,
            sleep=clock.sleep,
            monotonic=clock.monotonic,
        )

        assert isinstance(result, BlockingSummary)
        assert result.blocking_promises_ok is True
        assert result.timed_out is False
        assert result.permanent_failure_id == ""
        assert result.ticks == 1
        assert clock.sleeps == [], (
            "no sleep should fire when the first tick is already satisfied"
        )

    def test_aborts_on_permanent_failure_of_blocking_promise(self) -> None:
        registry = [_promise("a"), _promise("b")]
        scripted = [_summary_from([
            _attempt("a", "ok"),
            _attempt("b", "failed_permanent"),
        ])]
        orch = _ScriptedOrchestrator(scripted)
        orch._registry = registry
        clock = _FakeClock()

        result = orch.tick_until_done(
            timeout_seconds=240.0,
            tick_interval_seconds=5.0,
            sleep=clock.sleep,
            monotonic=clock.monotonic,
        )

        assert result.blocking_promises_ok is False
        assert result.timed_out is False
        assert result.permanent_failure_id == "b"
        assert result.ticks == 1

    def test_loops_until_satisfied_then_returns_ok(self) -> None:
        registry = [_promise("a")]
        scripted = [
            _summary_from([_attempt("a", "failed_transient")]),
            _summary_from([_attempt("a", "failed_transient")]),
            _summary_from([_attempt("a", "ok")]),
        ]
        orch = _ScriptedOrchestrator(scripted)
        orch._registry = registry
        clock = _FakeClock()

        result = orch.tick_until_done(
            timeout_seconds=240.0,
            tick_interval_seconds=5.0,
            sleep=clock.sleep,
            monotonic=clock.monotonic,
        )

        assert result.blocking_promises_ok is True
        assert result.ticks == 3
        # 2 sleeps between 3 ticks.
        assert len(clock.sleeps) == 2
        assert all(s == 5.0 for s in clock.sleeps)

    def test_returns_timed_out_when_deadline_elapses(self) -> None:
        registry = [_promise("a")]
        # Always-failing scripted summaries; clock advances 100s per
        # sleep so the 240s budget runs out after 2-3 ticks.
        scripted = [
            _summary_from([_attempt("a", "failed_transient")])
            for _ in range(10)
        ]
        orch = _ScriptedOrchestrator(scripted)
        orch._registry = registry
        clock = _FakeClock(advance_per_sleep=100.0)

        result = orch.tick_until_done(
            timeout_seconds=240.0,
            tick_interval_seconds=5.0,
            sleep=clock.sleep,
            monotonic=clock.monotonic,
        )

        assert result.timed_out is True
        assert result.blocking_promises_ok is False
        assert result.permanent_failure_id == ""
        assert result.ticks >= 2

    def test_zero_blocking_promises_returns_immediately_ok(self) -> None:
        # Edge case: Phase 1 lands the field with default True, so
        # registries with NO blocking promises should still exit
        # cleanly (the satisfied predicate is vacuously true).
        registry = [_promise("only", bootstrap_blocking=False)]
        scripted = [_summary_from([_attempt("only", "failed_transient")])]
        orch = _ScriptedOrchestrator(scripted)
        orch._registry = registry
        clock = _FakeClock()

        result = orch.tick_until_done(
            timeout_seconds=240.0,
            tick_interval_seconds=5.0,
            sleep=clock.sleep,
            monotonic=clock.monotonic,
        )

        assert result.blocking_promises_ok is True
        assert result.ticks == 1


# ======================================================================
# Module-level shim
# ======================================================================


class TestSatisfyPromisesBlockingShim:
    def test_delegates_to_orchestrator(self, monkeypatch) -> None:
        # The shim exists so contract YAMLs can name a function
        # path. Pin that it constructs a PromiseOrchestrator and
        # calls tick_until_done with the same kwargs.
        captured: dict[str, Any] = {}

        class _StubOrch:
            def __init__(self, **kwargs: Any) -> None:
                captured["init_kwargs"] = kwargs

            def tick_until_done(self, **kwargs: Any) -> BlockingSummary:
                captured["tick_kwargs"] = kwargs
                return BlockingSummary(
                    ticks=1,
                    elapsed_seconds=0.0,
                    final_summary=_summary_from([]),
                    timed_out=False,
                    blocking_promises_ok=True,
                )

        monkeypatch.setattr(
            "media_stack.application.services.orchestrator."
            "PromiseOrchestrator", _StubOrch,
        )

        clock = _FakeClock()
        result = satisfy_promises_blocking(
            timeout_seconds=10.0,
            tick_interval_seconds=2.0,
            registry=[],
            platform="compose",
            sleep=clock.sleep,
            monotonic=clock.monotonic,
        )

        assert result.blocking_promises_ok is True
        assert captured["init_kwargs"]["registry"] == []
        assert captured["tick_kwargs"]["timeout_seconds"] == 10.0
        assert captured["tick_kwargs"]["tick_interval_seconds"] == 2.0
        assert captured["tick_kwargs"]["platform"] == "compose"
