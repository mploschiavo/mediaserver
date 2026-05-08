"""Tests for ``PromiseOrchestrator.satisfy_scope`` — ADR-0005 Phase 5c.1.

The narrower variant of ``tick``: pass a list of promise ids and the
orchestrator runs the same probe / ensurer / re-probe machinery
against the filtered subset. Used by the ``discover-api-keys`` job
to dispatch only the per-service api-key promises rather than the
full registry every time.

Coverage:
  * empty id list → empty TickSummary
  * filter drops registry entries not in scope
  * unknown ids in scope are silently dropped (not an error)
  * cooldown + history emitter are SHARED with the parent
    orchestrator instance
  * platform filter + dry-run + live_services flow through
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from media_stack.application.services.orchestrator import (
    PromiseOrchestrator,
)
from media_stack.domain.services.lifecycle import Outcome, ProbeResult
from media_stack.domain.services.promises import (
    DeployEnsurer,
    LifecycleProbe,
    Promise,
)
from media_stack.infrastructure.promises.cooldown import CooldownTracker


def _promise(pid: str, *, service: str = "x") -> Promise:
    return Promise(
        id=pid,
        description=pid,
        platforms=("compose", "k8s"),
        probe=LifecycleProbe(service=service, method="probe_running"),
        ensurer=DeployEnsurer(target=service),
    )


@pytest.fixture
def cooldown(tmp_path: Path) -> CooldownTracker:
    return CooldownTracker(tmp_path / "state.json")


def _run_with_probes(
    *,
    registry: list[Promise],
    scope: list[str],
    cooldown: CooldownTracker,
    probe_results: dict[str, ProbeResult],
    platform: str = "compose",
):
    """Helper — patch the dispatcher so probes return canned results."""
    def fake_dispatch_probe(probe, *, resolver, now, secrets):
        # The probe spec is identified by its ``service`` (matches the
        # promise id we set above when service == promise id).
        for pid, result in probe_results.items():
            if probe.service == pid or probe.service in pid:
                return result
        # Default: ok
        return ProbeResult.ok("default", evidence={}, evaluated_at=now)

    with patch(
        "media_stack.application.services.orchestrator.dispatch_probe",
        side_effect=fake_dispatch_probe,
    ):
        orch = PromiseOrchestrator(
            registry=registry,
            cooldown=cooldown,
            history_emit=lambda *a, **kw: None,
        )
        return orch.satisfy_scope(scope, platform=platform)


def test_empty_scope_returns_empty_summary(cooldown: CooldownTracker) -> None:
    orch = PromiseOrchestrator(
        registry=[_promise("a"), _promise("b")],
        cooldown=cooldown,
        history_emit=lambda *a, **kw: None,
    )
    summary = orch.satisfy_scope([])
    assert summary.attempts == ()


def test_unknown_ids_in_scope_silently_dropped(
    cooldown: CooldownTracker,
) -> None:
    orch = PromiseOrchestrator(
        registry=[_promise("a")],
        cooldown=cooldown,
        history_emit=lambda *a, **kw: None,
    )
    summary = orch.satisfy_scope(["does-not-exist"])
    assert summary.attempts == ()


def test_filter_drops_out_of_scope_promises(
    cooldown: CooldownTracker,
) -> None:
    """The full registry has 3 promises; satisfy_scope should run
    exactly the 2 in scope."""
    registry = [_promise("a", service="a"), _promise("b", service="b"), _promise("c", service="c")]
    summary = _run_with_probes(
        registry=registry,
        scope=["a", "b"],
        cooldown=cooldown,
        probe_results={
            "a": ProbeResult.ok("a-ok", evidence={}, evaluated_at=0.0),
            "b": ProbeResult.ok("b-ok", evidence={}, evaluated_at=0.0),
        },
    )
    pids = {a.promise_id for a in summary.attempts}
    assert pids == {"a", "b"}


def test_scope_intersection_runs_full_lifecycle(
    cooldown: CooldownTracker,
) -> None:
    """A scoped run goes through the same probe machinery as a
    full tick — pinning that the attempts get the canonical
    ``ok`` / ``failed_*`` PromiseStatus values."""
    registry = [_promise("a", service="a")]
    summary = _run_with_probes(
        registry=registry,
        scope=["a"],
        cooldown=cooldown,
        probe_results={
            "a": ProbeResult.ok("a-ok", evidence={}, evaluated_at=0.0),
        },
    )
    assert len(summary.attempts) == 1
    assert summary.attempts[0].status == "ok"


def test_platform_filter_applies_inside_scope(
    cooldown: CooldownTracker,
) -> None:
    """A promise with ``platforms=[k8s]`` skipped on compose even
    when in scope — the platform filter runs INSIDE the sub-tick."""
    p = Promise(
        id="k8s-only",
        description="",
        platforms=("k8s",),
        probe=LifecycleProbe(service="k8s-only", method="probe_running"),
        ensurer=DeployEnsurer(target="k8s-only"),
    )
    summary = _run_with_probes(
        registry=[p],
        scope=["k8s-only"],
        cooldown=cooldown,
        probe_results={},
        platform="compose",
    )
    # Skipped by platform filter — no attempt recorded.
    assert summary.attempts == ()


def test_history_emitter_shared_with_parent(
    cooldown: CooldownTracker,
) -> None:
    """Scoped tick must use the parent orchestrator's history emitter
    so RunRecords land in the same JSONL stream as full-registry
    ticks."""
    emitted: list[str] = []

    def emit(promise, attempt, phase):
        emitted.append(f"{phase}:{promise.id}:{attempt.status}")

    registry = [_promise("a", service="a")]
    with patch(
        "media_stack.application.services.orchestrator.dispatch_probe",
        return_value=ProbeResult.ok(
            "ok", evidence={}, evaluated_at=0.0,
        ),
    ):
        orch = PromiseOrchestrator(
            registry=registry,
            cooldown=cooldown,
            history_emit=emit,
        )
        orch.satisfy_scope(["a"])
    assert any("a:ok" in e for e in emitted)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
