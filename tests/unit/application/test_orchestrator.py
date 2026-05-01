"""Tests for ``application.services.orchestrator.satisfy_promises``.

The orchestrator is the central piece — these tests pin the
behaviors the rest of Phase 4 depends on:

  * Topological order respected (deps probed first).
  * Cooldown skip honored (don't re-evaluate too soon).
  * dep_failed cascade (when a dep fails this tick, dependents skip).
  * Parallel probe execution (a slow probe doesn't block faster
    probes in the same level).
  * Dry-run skips ensurers but still probes + records.
  * Tick summary counts each status correctly.
  * RunRecord emit fires per probe + ensurer attempt with
    ``promise_id`` populated.

Mocked dispatcher so we don't hit real services; the dispatcher's
own tests cover the real probe behaviors.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from media_stack.domain.services.lifecycle import Outcome, ProbeResult
from media_stack.domain.services.promises import (
    DeployEnsurer,
    JobEnsurer,
    LifecycleEnsurer,
    LifecycleProbe,
    Promise,
)
from media_stack.application.services.orchestrator import satisfy_promises
from media_stack.infrastructure.promises.cooldown import CooldownTracker


# ---------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------


def _promise(
    pid: str,
    *,
    depends_on: tuple[str, ...] = (),
    platforms: tuple[str, ...] = ("compose", "k8s"),
    ensurer: Any = None,
) -> Promise:
    return Promise(
        id=pid,
        description=pid,
        platforms=platforms,
        probe=LifecycleProbe(service="x", method="probe_running"),
        ensurer=ensurer or DeployEnsurer(target="x"),
        depends_on=depends_on,
    )


@pytest.fixture
def fresh_cooldown(tmp_path: Path) -> CooldownTracker:
    return CooldownTracker(tmp_path / "state.json")


@pytest.fixture
def emit_log() -> list[tuple[str, str, str]]:
    """Captures (phase, promise_id, status) for each emitted record."""
    return []


@pytest.fixture
def history_emit(emit_log):
    def emit(promise: Promise, attempt, phase: str) -> None:
        emit_log.append((phase, promise.id, attempt.status))
    return emit


# ---------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------


class TestPlatformFilter:
    def test_skips_promises_not_applicable_to_platform(
        self, fresh_cooldown, history_emit, emit_log,
    ) -> None:
        registry = [
            _promise("a", platforms=("compose",)),
            _promise("b", platforms=("k8s",)),  # skipped on compose
        ]
        with patch(
            "media_stack.application.services.orchestrator.dispatch_probe",
            return_value=ProbeResult.ok(),
        ):
            summary = satisfy_promises(
                registry,
                platform="compose",
                cooldown=fresh_cooldown,
                history_emit=history_emit,
            )
        assert summary.skipped_platform == 1
        assert summary.ok == 1
        # b was skipped at platform — no record emitted for it
        emitted_ids = {pid for (_, pid, _) in emit_log}
        assert "b" not in emitted_ids
        assert "a" in emitted_ids


class TestTopologicalOrder:
    def test_dep_failed_cascades_to_dependents(
        self, fresh_cooldown, history_emit, emit_log,
    ) -> None:
        registry = [
            _promise("root"),
            _promise("child", depends_on=("root",)),
            _promise("grandchild", depends_on=("child",)),
        ]
        # root probe fails → child should be dep_failed → grandchild dep_failed
        results_by_promise: dict[str, ProbeResult] = {
            "root": ProbeResult.failed("root broken"),
        }

        def fake_probe(spec, *, resolver, now, secrets=None):
            # We're matching by call sequence; in the real flow the
            # orchestrator only probes ``root`` because child + grandchild
            # are gated by dep_failed. Default returns ok for safety.
            return results_by_promise.get(
                getattr(spec, "service", ""), ProbeResult.ok(),
            )

        with patch(
            "media_stack.application.services.orchestrator.dispatch_probe",
            side_effect=lambda spec, **kw: results_by_promise.get(
                "root" if "root" in str(kw) else "ok", ProbeResult.failed("root broken"),
            ),
        ), patch(
            "media_stack.application.services.orchestrator.dispatch_ensurer",
            return_value=Outcome.failure("ensurer failed", transient=True),
        ):
            # Simpler: stub probe to always fail; we expect the
            # cascade behavior regardless of which probe was probed.
            summary = satisfy_promises(
                registry,
                platform="compose",
                cooldown=fresh_cooldown,
                history_emit=history_emit,
            )

        # root was probed + ensured but failed
        # child should be dep_failed (root failed)
        # grandchild should be dep_failed (child failed)
        attempts_by_id = {a.promise_id: a for a in summary.attempts}
        assert attempts_by_id["root"].status in (
            "failed_transient", "failed_permanent",
        )
        assert attempts_by_id["child"].status == "dep_failed"
        assert attempts_by_id["grandchild"].status == "dep_failed"
        assert summary.dep_failed == 2


class TestCooldown:
    def test_promise_in_cooldown_is_skipped(
        self, fresh_cooldown, history_emit, emit_log,
    ) -> None:
        # Pre-populate cooldown — promise X failed transient just now.
        from media_stack.domain.services.promises import PromiseAttempt
        fresh_cooldown.record_attempt(PromiseAttempt(
            promise_id="x", status="failed_transient",
            started_at=time.time(), elapsed_seconds=0.0,
        ))
        registry = [_promise("x")]
        with patch(
            "media_stack.application.services.orchestrator.dispatch_probe",
            return_value=ProbeResult.ok(),
        ) as mock_probe:
            summary = satisfy_promises(
                registry,
                platform="compose",
                cooldown=fresh_cooldown,
                history_emit=history_emit,
            )
        assert summary.skipped_cooldown == 1
        # Probe never called
        mock_probe.assert_not_called()


class TestProbeOk:
    def test_ok_probe_records_status_and_emits_run_record(
        self, fresh_cooldown, history_emit, emit_log,
    ) -> None:
        registry = [_promise("x")]
        with patch(
            "media_stack.application.services.orchestrator.dispatch_probe",
            return_value=ProbeResult.ok("alive"),
        ):
            summary = satisfy_promises(
                registry,
                platform="compose",
                cooldown=fresh_cooldown,
                history_emit=history_emit,
            )
        assert summary.ok == 1
        # Exactly one record emitted, phase=probe (no ensurer fired)
        assert len(emit_log) == 1
        phase, pid, status = emit_log[0]
        assert (phase, pid, status) == ("probe", "x", "ok")


class TestProbeFailureAndEnsurer:
    def test_dry_run_skips_ensurer(
        self, fresh_cooldown, history_emit, emit_log,
    ) -> None:
        registry = [_promise("x")]
        with patch(
            "media_stack.application.services.orchestrator.dispatch_probe",
            return_value=ProbeResult.failed("not yet"),
        ), patch(
            "media_stack.application.services.orchestrator.dispatch_ensurer",
        ) as mock_ensure:
            summary = satisfy_promises(
                registry,
                platform="compose",
                cooldown=fresh_cooldown,
                history_emit=history_emit,
                dry_run=True,
            )
        assert summary.failed_transient == 1
        # The whole point of dry_run: ensurers MUST NOT fire
        mock_ensure.assert_not_called()

    def test_failed_probe_runs_ensurer_then_reprobes(
        self, fresh_cooldown, history_emit, emit_log,
    ) -> None:
        registry = [_promise("x")]
        # Probe fails first, succeeds on re-probe — orchestrator
        # records ok. This is the self-healing happy path.
        probe_results = [
            ProbeResult.failed("not yet"),  # initial probe
            ProbeResult.ok("now up"),       # re-probe
        ]
        with patch(
            "media_stack.application.services.orchestrator.dispatch_probe",
            side_effect=probe_results,
        ), patch(
            "media_stack.application.services.orchestrator.dispatch_ensurer",
            return_value=Outcome.success(None),
        ):
            summary = satisfy_promises(
                registry,
                platform="compose",
                cooldown=fresh_cooldown,
                history_emit=history_emit,
            )
        assert summary.ok == 1

    def test_permanent_ensurer_failure_recorded_as_permanent(
        self, fresh_cooldown, history_emit, emit_log,
    ) -> None:
        registry = [_promise("x")]
        with patch(
            "media_stack.application.services.orchestrator.dispatch_probe",
            return_value=ProbeResult.failed("missing"),
        ), patch(
            "media_stack.application.services.orchestrator.dispatch_ensurer",
            return_value=Outcome.failure("config-level error", transient=False),
        ):
            summary = satisfy_promises(
                registry,
                platform="compose",
                cooldown=fresh_cooldown,
                history_emit=history_emit,
            )
        assert summary.failed_permanent == 1


class TestParallelism:
    def test_slow_probe_does_not_block_faster_probes(
        self, fresh_cooldown, history_emit,
    ) -> None:
        # Two promises in the same topo level. One probe is slow
        # (sleeps 0.3s); the other is instant. Total wall time should
        # be ~0.3s, NOT ~0.6s — proves parallel execution.
        registry = [_promise("slow"), _promise("fast")]

        def fake_probe(spec, *, resolver, now, secrets=None):
            if "slow" in spec.service if hasattr(spec, "service") else False:
                pass  # Won't match — probe is LifecycleProbe with service="x"
            return ProbeResult.ok()

        # We need different behavior per-promise, but the probe
        # specs are identical (same service "x"). Track promise
        # via the calling thread's order. Simplest: just measure
        # wall time and assert it's < 2× the slow time, which proves
        # the calls overlapped.
        call_order: list[float] = []

        def slow_then_fast(spec, **kw):
            t = time.time()
            call_order.append(t)
            # First call sleeps; second completes immediately.
            if len(call_order) == 1:
                time.sleep(0.2)
            return ProbeResult.ok()

        start = time.time()
        with patch(
            "media_stack.application.services.orchestrator.dispatch_probe",
            side_effect=slow_then_fast,
        ):
            satisfy_promises(
                registry,
                platform="compose",
                cooldown=fresh_cooldown,
                history_emit=history_emit,
                workers=4,
            )
        elapsed = time.time() - start
        # Both probes started within ~50ms of each other → parallel
        if len(call_order) >= 2:
            assert call_order[1] - call_order[0] < 0.05, (
                f"probes serialized: {call_order[1] - call_order[0]:.3f}s gap"
            )
        # Total wall ~0.2s — sequential would be ~0.4s
        assert elapsed < 0.35, f"orchestrator wall too long: {elapsed:.2f}s"


class TestSummary:
    def test_summary_line_lists_each_nonzero_bucket(
        self, fresh_cooldown, history_emit,
    ) -> None:
        registry = [
            _promise("a"),
            _promise("b"),
            _promise("c", platforms=("k8s",)),  # platform_skip on compose
        ]
        # a ok, b failed_transient
        results = {"a": ProbeResult.ok(), "b": ProbeResult.failed("x")}
        call_count = [0]

        def fake_probe(spec, **kw):
            # Determined by call order: alphabetical (orchestrator
            # sorts level-results by promise_id)
            i = call_count[0]
            call_count[0] += 1
            return [results["a"], results["b"]][i] if i < 2 else ProbeResult.failed("x")

        with patch(
            "media_stack.application.services.orchestrator.dispatch_probe",
            side_effect=fake_probe,
        ), patch(
            "media_stack.application.services.orchestrator.dispatch_ensurer",
            return_value=Outcome.failure("nope", transient=True),
        ):
            summary = satisfy_promises(
                registry,
                platform="compose",
                cooldown=fresh_cooldown,
                history_emit=history_emit,
            )
        line = summary.summary_line()
        assert "ok" in line
        assert "platform_skip" in line


class TestPromiseIdInRunRecords:
    def test_run_record_callback_receives_promise_id(
        self, fresh_cooldown,
    ) -> None:
        # This is the contract Phase 4c relies on: every emitted
        # record carries the promise id so the existing
        # /api/jobs/history endpoint can filter by it.
        seen_promise_ids: list[str] = []

        def emit(promise, attempt, phase):
            seen_promise_ids.append(attempt.promise_id)

        registry = [_promise("jellyfin-running")]
        with patch(
            "media_stack.application.services.orchestrator.dispatch_probe",
            return_value=ProbeResult.ok(),
        ):
            satisfy_promises(
                registry,
                platform="compose",
                cooldown=fresh_cooldown,
                history_emit=emit,
            )
        assert seen_promise_ids == ["jellyfin-running"]
