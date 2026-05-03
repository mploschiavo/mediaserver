"""Tests for the ADR-0005 Phase 1 bootstrap handler.

Pin the contract that the (eventually-wired-in) ``bootstrap:satisfy-
promises`` job relies on:

  * ``OrchestratorBootstrapJobHandler`` returns the framework-
    expected result dict shape.
  * Status policy: ``ok`` on success, ``error`` on permanent failure
    (with ``permanent_failure_id`` lifted to ``error``), ``warn`` on
    timeout (with the orchestrator-keep-trying message).
  * Knobs come from env (``BOOTSTRAP_PROMISE_TIMEOUT``,
    ``BOOTSTRAP_PROMISE_TICK_INTERVAL``,
    ``ORCHESTRATOR_LIVE_SERVICES``) via the injected ``env_provider``.
  * Registered in ``contracts/services/guardrails.yaml`` so the
    dispatcher resolves it.
"""

from __future__ import annotations

from unittest.mock import patch
from typing import Any

import pytest

from media_stack.domain.services.promises import (
    BlockingSummary,
    PromiseAttempt,
    TickSummary,
)


class _StubCtx:
    pass


def _summary(
    *,
    blocking_promises_ok: bool = True,
    timed_out: bool = False,
    permanent_failure_id: str = "",
    ticks: int = 1,
    elapsed: float = 1.5,
    ok: int = 5,
    failed_transient: int = 0,
    failed_permanent: int = 0,
) -> BlockingSummary:
    final = TickSummary(
        started_at=0.0, elapsed_seconds=elapsed,
        total=ok + failed_transient + failed_permanent,
        ok=ok,
        failed_transient=failed_transient,
        failed_permanent=failed_permanent,
        dep_failed=0, skipped_cooldown=0, skipped_platform=0, unknown=0,
        attempts=(),
    )
    return BlockingSummary(
        ticks=ticks,
        elapsed_seconds=elapsed,
        final_summary=final,
        timed_out=timed_out,
        blocking_promises_ok=blocking_promises_ok,
        permanent_failure_id=permanent_failure_id,
    )


class TestBootstrapHandlerStatusPolicy:
    """``status`` field on the result dict is what JobRunner records
    as the terminal outcome — pin all three legs."""

    def test_status_ok_when_blocking_promises_satisfied(self) -> None:
        with patch(
            "media_stack.application.services.orchestrator."
            "satisfy_promises_blocking",
            return_value=_summary(blocking_promises_ok=True),
        ):
            from media_stack.application.jobs.orchestrator_satisfy import (
                OrchestratorBootstrapJobHandler,
            )
            handler = OrchestratorBootstrapJobHandler(env_provider={})
            result = handler.run(_StubCtx())
        assert result["status"] == "ok"
        assert result["blocking_promises_ok"] is True
        assert "error" not in result
        assert result["ticks"] == 1
        assert result["ok_count"] == 5

    def test_status_error_with_permanent_failure_id(self) -> None:
        with patch(
            "media_stack.application.services.orchestrator."
            "satisfy_promises_blocking",
            return_value=_summary(
                blocking_promises_ok=False,
                permanent_failure_id="jellyfin-running",
                failed_permanent=1, ok=4,
            ),
        ):
            from media_stack.application.jobs.orchestrator_satisfy import (
                OrchestratorBootstrapJobHandler,
            )
            handler = OrchestratorBootstrapJobHandler(env_provider={})
            result = handler.run(_StubCtx())
        assert result["status"] == "error"
        assert result["permanent_failure_id"] == "jellyfin-running"
        assert "jellyfin-running" in result["error"]
        assert "failed_permanent" in result["error"]

    def test_status_warn_with_timeout_message(self) -> None:
        # Timeout is non-fatal: bootstrap declares "completed with
        # warnings" so the dashboard onboarding banner unblocks; the
        # orchestrator's per-60s tick keeps trying.
        with patch(
            "media_stack.application.services.orchestrator."
            "satisfy_promises_blocking",
            return_value=_summary(
                blocking_promises_ok=False,
                timed_out=True,
                ticks=12,
                elapsed=240.0,
                ok=3, failed_transient=2,
            ),
        ):
            from media_stack.application.jobs.orchestrator_satisfy import (
                OrchestratorBootstrapJobHandler,
            )
            handler = OrchestratorBootstrapJobHandler(env_provider={})
            result = handler.run(_StubCtx())
        assert result["status"] == "warn"
        assert result["timed_out"] is True
        assert "240" in result["error"]
        assert "orchestrator continuous-mode" in result["error"]


class TestBootstrapHandlerEnvKnobs:
    """The env provider drives ``timeout_seconds`` /
    ``tick_interval_seconds`` / ``live_services``."""

    def test_passes_env_timeout_and_tick_interval(self) -> None:
        captured: dict[str, Any] = {}

        def _capture(**kwargs: Any) -> BlockingSummary:
            captured.update(kwargs)
            return _summary()

        env = {
            "BOOTSTRAP_PROMISE_TIMEOUT": "120.5",
            "BOOTSTRAP_PROMISE_TICK_INTERVAL": "2.5",
        }
        with patch(
            "media_stack.application.services.orchestrator."
            "satisfy_promises_blocking",
            side_effect=_capture,
        ):
            from media_stack.application.jobs.orchestrator_satisfy import (
                OrchestratorBootstrapJobHandler,
            )
            handler = OrchestratorBootstrapJobHandler(env_provider=env)
            handler.run(_StubCtx())
        assert captured["timeout_seconds"] == pytest.approx(120.5)
        assert captured["tick_interval_seconds"] == pytest.approx(2.5)

    def test_falls_back_to_defaults_when_env_unset(self) -> None:
        captured: dict[str, Any] = {}

        def _capture(**kwargs: Any) -> BlockingSummary:
            captured.update(kwargs)
            return _summary()

        with patch(
            "media_stack.application.services.orchestrator."
            "satisfy_promises_blocking",
            side_effect=_capture,
        ):
            from media_stack.application.jobs.orchestrator_satisfy import (
                OrchestratorBootstrapJobHandler,
            )
            handler = OrchestratorBootstrapJobHandler(env_provider={})
            handler.run(_StubCtx())
        # Defaults the ADR commits to: 240s timeout, 5s tick.
        assert captured["timeout_seconds"] == 240.0
        assert captured["tick_interval_seconds"] == 5.0

    def test_ignores_non_numeric_env_value(self) -> None:
        captured: dict[str, Any] = {}

        def _capture(**kwargs: Any) -> BlockingSummary:
            captured.update(kwargs)
            return _summary()

        env = {"BOOTSTRAP_PROMISE_TIMEOUT": "not-a-number"}
        with patch(
            "media_stack.application.services.orchestrator."
            "satisfy_promises_blocking",
            side_effect=_capture,
        ):
            from media_stack.application.jobs.orchestrator_satisfy import (
                OrchestratorBootstrapJobHandler,
            )
            handler = OrchestratorBootstrapJobHandler(env_provider=env)
            handler.run(_StubCtx())
        # Falls back to default; doesn't crash on parse failure.
        assert captured["timeout_seconds"] == 240.0

    def test_passes_live_services_allowlist(self) -> None:
        captured: dict[str, Any] = {}

        def _capture(**kwargs: Any) -> BlockingSummary:
            captured.update(kwargs)
            return _summary()

        env = {"ORCHESTRATOR_LIVE_SERVICES": " Jellyfin , sonarr "}
        with patch(
            "media_stack.application.services.orchestrator."
            "satisfy_promises_blocking",
            side_effect=_capture,
        ):
            from media_stack.application.jobs.orchestrator_satisfy import (
                OrchestratorBootstrapJobHandler,
            )
            handler = OrchestratorBootstrapJobHandler(env_provider=env)
            handler.run(_StubCtx())
        assert captured["live_services"] == frozenset({"jellyfin", "sonarr"})


class TestBootstrapHandlerRegistration:
    def test_registered_in_guardrails_yaml(self) -> None:
        # The dispatcher resolves the job through the contract
        # registry. If the YAML entry drifts or moves,
        # run_job("bootstrap:satisfy-promises") returns "unknown
        # job" and any future bootstrap-DAG cutover silently
        # skips the orchestrator path.
        from pathlib import Path
        import yaml

        contract = (
            Path(__file__).resolve().parents[3]
            / "contracts" / "services" / "guardrails.yaml"
        )
        data = yaml.safe_load(contract.read_text(encoding="utf-8"))
        jobs = (data.get("plugin") or {}).get("jobs") or {}
        assert "bootstrap:satisfy-promises" in jobs, (
            "bootstrap:satisfy-promises not registered in "
            "contracts/services/guardrails.yaml::plugin.jobs."
        )
        entry = jobs["bootstrap:satisfy-promises"]
        assert entry["handler"] == (
            "media_stack.application.jobs.orchestrator_satisfy:satisfy_blocking"
        )
        # Phase 2 (2026-05-03) graduated the synthetic job into
        # ``post`` priority 100 — it now runs as the FINAL step of
        # the post phase. The Phase-1 holding-area phase
        # ``orchestrator_satisfy`` was retired; pin the new shape
        # here so a Phase-2 revert is intentional. The richer
        # placement ratchet lives at
        # ``tests/unit/contracts/test_adr_0005_phase_2_cutover.py``.
        assert entry["phase"] == "post", (
            f"phase changed from 'post' to {entry['phase']!r} — "
            "Phase 2 graduated the synthetic job into the post "
            "phase. If you're moving it, update the placement "
            "ratchet too."
        )
        assert entry["priority"] >= 100, (
            "bootstrap:satisfy-promises must run AFTER all other "
            "post-phase ensurers (priority 100+); see "
            "test_adr_0005_phase_2_cutover.py for the full ratchet."
        )

    def test_discoverable_via_contract_loader(self) -> None:
        from media_stack.application.jobs.framework import (
            discover_jobs_from_contracts, get_job_registry,
        )
        jobs = discover_jobs_from_contracts()
        names = {j["name"] for j in jobs}
        assert "bootstrap:satisfy-promises" in names
        assert "bootstrap:satisfy-promises" in get_job_registry()

    def test_module_function_delegates_to_singleton(self) -> None:
        with patch(
            "media_stack.application.services.orchestrator."
            "satisfy_promises_blocking",
            return_value=_summary(blocking_promises_ok=True),
        ):
            from media_stack.application.jobs.orchestrator_satisfy import (
                satisfy_blocking,
            )
            result = satisfy_blocking(_StubCtx())
        assert result["status"] == "ok"
