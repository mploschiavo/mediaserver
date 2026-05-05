"""Tests for the guardrails job handler that unifies guardrail
evaluation onto the JobRunner path (v1.0.284+).

Covers:

  * The contract YAML registers ``guardrails:evaluate``.
  * The handler delegates to ``tick(record_history=False)`` so
    JobRunner is the only history writer.
  * Throttled cycles surface as ``{"skipped": "throttled", ...}``
    so JobRunner records terminal status ``skipped`` rather
    than ``ok``.
  * Triggers/actions surface unchanged for non-throttled cycles.
"""

from __future__ import annotations

from typing import Any

import pytest

from media_stack.application.guardrails import job_handlers
from media_stack.application.jobs.framework import (
    JobContext,
    discover_jobs_from_contracts,
)


def test_contract_registers_guardrails_evaluate_job() -> None:
    """The new contracts/services/guardrails.yaml must surface the
    job through the framework's discovery so JobRunner can route to
    it. Regression guard for "did someone delete the contract?"."""
    jobs = discover_jobs_from_contracts()
    names = [j["name"] for j in jobs]
    assert "guardrails:evaluate" in names
    target = next(j for j in jobs if j["name"] == "guardrails:evaluate")
    # Handler points at our application/guardrails module — if a
    # refactor moves the file, the contract must follow.
    assert target["handler"].startswith(
        "media_stack.application.guardrails.job_handlers:",
    )


def test_handler_returns_skipped_marker_when_tick_throttles(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the inner ``tick`` returns a throttled result (the
    ``MEDIA_STACK_GUARDRAIL_INTERVAL_SECONDS`` floor hasn't elapsed),
    the handler must propagate ``skipped`` so JobRunner records the
    run with terminal status ``skipped`` instead of ``ok``."""
    fake_tick_calls: list[dict[str, Any]] = []

    def fake_tick(**kwargs: Any) -> dict[str, Any]:
        fake_tick_calls.append(kwargs)
        return {
            "ran_at": 1_700_000_000,
            "elapsed": 0.0,
            "triggers": [],
            "actions": [],
            "skipped": "throttled",
            "next_eligible_at": 1_700_000_300,
        }

    monkeypatch.setattr(
        "media_stack.application.guardrails.evaluation_loop.tick",
        fake_tick,
    )
    out = job_handlers.guardrails_evaluate(JobContext())
    assert out.get("skipped") == "throttled"
    # JobRunner records history; the handler MUST suppress the
    # legacy in-tick history write so we don't double-write.
    # ADR-0008 Phase 2 also threads ``lockdown_service`` into the
    # tick call so the disk-pressure rule's Action(action="lockdown_*")
    # results dispatch to the engage/release implementer. The kwargs
    # always include ``record_history=False``; the lockdown_service
    # is whatever LockdownFactory.singleton() returned (may be None
    # in environments where no clients are configured).
    assert len(fake_tick_calls) == 1
    call = fake_tick_calls[0]
    assert call.get("record_history") is False


def test_handler_passes_through_triggers_when_tick_runs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A non-throttled tick returns ``triggers`` / ``actions``;
    the handler returns the payload unchanged so the RunDrawer's
    Output tab can surface what fired."""
    payload = {
        "ran_at": 1_700_000_000,
        "elapsed": 0.42,
        "triggers": [{"rule_id": "storage_low", "severity": "critical"}],
        "actions": [{"rule_id": "storage_low", "ok": True}],
    }
    monkeypatch.setattr(
        "media_stack.application.guardrails.evaluation_loop.tick",
        lambda **_: payload,
    )
    out = job_handlers.guardrails_evaluate(JobContext())
    assert "skipped" not in out
    assert out["triggers"] == payload["triggers"]
    assert out["actions"] == payload["actions"]
    assert out["elapsed"] == 0.42


def test_handler_does_not_double_record_history(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The handler must invoke tick with ``record_history=False``
    so the legacy ``_record_history()`` path stays dormant. After
    v1.0.284 JobRunner is the *only* history writer for guardrails
    cycles — preventing double-writes is the whole point of the
    unification."""
    seen_kwargs: dict[str, Any] = {}

    def fake_tick(**kwargs: Any) -> dict[str, Any]:
        seen_kwargs.update(kwargs)
        return {"ran_at": 0, "elapsed": 0.0, "triggers": [], "actions": []}

    monkeypatch.setattr(
        "media_stack.application.guardrails.evaluation_loop.tick",
        fake_tick,
    )
    job_handlers.guardrails_evaluate(JobContext())
    assert seen_kwargs.get("record_history") is False
