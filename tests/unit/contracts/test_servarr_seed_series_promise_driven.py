"""Pin the promise-driven wiring of the Sonarr seed-series cutover
(ADR-0005 Phase 3 — wide-handler delegation addendum).

The ``sonarr-has-series`` promise flipped from string-typed
``ensured_by: ensure-sonarr-seed-series`` (with an ``http_json``
``len(response) >= 5`` probe) to lifecycle-typed:
``{type: lifecycle, service: sonarr, method: ensure_has_series}``
+ ``{type: lifecycle, service: sonarr, method: probe_has_series}``.
The legacy ``ensure-sonarr-seed-series`` job in ``core.yaml`` lost
its ``phase: post`` + ``priority: 87`` so the bootstrap loader
stops scheduling it directly — the orchestrator dispatches via the
promise registry instead. The ``ensure_has_series`` ensurer's
wirer DELEGATES BACK to this same legacy handler via injected
callables (Jellyseerr wide-handler pattern) so the heavyweight
Sonarr-API + tvdbId-lookup implementation stays the single source
of truth.

This ratchet pins the contract-level shape so a future contract
edit can't silently undo it.

Sections:
  * PromiseUsesLifecycleDispatch — probe + ensurer are
    LifecycleProbe + LifecycleEnsurer with the correct service +
    method names.
  * PromiseIsBlocking — explicit ``bootstrap_blocking: true``
    survived the move (matches the *-jellyfin-notifier convention).
  * LegacyJobUnscheduled — ``ensure-sonarr-seed-series`` is still
    REGISTERED in core.yaml (handler + label) but has NO ``phase``
    or ``priority`` field, so ``discover_jobs_from_contracts``
    doesn't place it on the bootstrap-scheduled phase tree.
  * LegacyHandlerStillResolvable — the registered handler imports
    cleanly so ``run_job(name)``, the auto-heal cycle, AND the
    orchestrator's ``ensure_has_series`` lifecycle delegate all
    keep reaching the same code path.
"""

from __future__ import annotations

import unittest
from pathlib import Path

import yaml


_REPO_ROOT = Path(__file__).resolve().parents[3]


class _ContractFixture:
    """Loads the relevant contract YAMLs once per test class."""

    def __init__(self) -> None:
        self._core = yaml.safe_load(
            (_REPO_ROOT / "contracts" / "services" / "core.yaml")
            .read_text(encoding="utf-8")
        )

    def core_jobs(self) -> dict:
        return (self._core.get("plugin") or {}).get("jobs") or {}


class _LoadedRegistry:
    """One-shot loader fixture cache."""

    _cache = None

    @classmethod
    def get(cls):
        if cls._cache is None:
            from media_stack.infrastructure.promises.registry import (
                PromiseRegistryLoader,
            )
            cls._cache = PromiseRegistryLoader().aggregate()
        return cls._cache


class PromiseUsesLifecycleDispatch(unittest.TestCase):
    """The ``sonarr-has-series`` promise's probe + ensurer are
    LifecycleProbe + LifecycleEnsurer pointing at the
    ServarrLifecycle methods. Reverting to the legacy ``http_json``
    probe + string ``ensured_by: ensure-sonarr-seed-series`` flips
    every assertion here."""

    _PROMISE_ID = "sonarr-has-series"
    _EXPECTED_SERVICE = "sonarr"
    _EXPECTED_PROBE_METHOD = "probe_has_series"
    _EXPECTED_ENSURE_METHOD = "ensure_has_series"

    def setUp(self) -> None:
        self.by_id = _LoadedRegistry.get().by_id()

    def test_promise_probe_is_lifecycle(self) -> None:
        from media_stack.domain.services.promises import LifecycleProbe
        promise = self.by_id.get(self._PROMISE_ID)
        self.assertIsNotNone(
            promise, f"{self._PROMISE_ID!r} dropped out of registry",
        )
        self.assertIsInstance(
            promise.probe, LifecycleProbe,
            f"{self._PROMISE_ID}: probe regressed from lifecycle "
            f"dispatch (got {type(promise.probe).__name__})",
        )
        self.assertEqual(
            promise.probe.service, self._EXPECTED_SERVICE,
            f"{self._PROMISE_ID}: probe.service expected "
            f"{self._EXPECTED_SERVICE!r}",
        )
        self.assertEqual(
            promise.probe.method, self._EXPECTED_PROBE_METHOD,
            f"{self._PROMISE_ID}: probe.method expected "
            f"{self._EXPECTED_PROBE_METHOD!r}",
        )

    def test_promise_ensurer_is_lifecycle(self) -> None:
        from media_stack.domain.services.promises import LifecycleEnsurer
        promise = self.by_id[self._PROMISE_ID]
        self.assertIsInstance(
            promise.ensurer, LifecycleEnsurer,
            f"{self._PROMISE_ID}: ensurer regressed from lifecycle "
            f"dispatch (got {type(promise.ensurer).__name__})",
        )
        self.assertEqual(
            promise.ensurer.service, self._EXPECTED_SERVICE,
            f"{self._PROMISE_ID}: ensurer.service expected "
            f"{self._EXPECTED_SERVICE!r}",
        )
        self.assertEqual(
            promise.ensurer.method, self._EXPECTED_ENSURE_METHOD,
            f"{self._PROMISE_ID}: ensurer.method expected "
            f"{self._EXPECTED_ENSURE_METHOD!r}",
        )


class PromiseIsBlocking(unittest.TestCase):
    """Explicit ``bootstrap_blocking: true`` annotation survives
    the cutover. The Jellyfin Phase 2 family proof set the
    explicit-on-cutover-proofs convention; every Phase 3 cutover
    follows it."""

    def setUp(self) -> None:
        self.by_id = _LoadedRegistry.get().by_id()

    def test_promise_is_blocking(self) -> None:
        promise = self.by_id["sonarr-has-series"]
        self.assertTrue(
            promise.bootstrap_blocking,
            "sonarr-has-series: bootstrap_blocking flipped to False "
            "— the cutover requires explicit-True so the "
            "orchestrator-driven bootstrap waits for it.",
        )


class LegacyJobUnscheduled(unittest.TestCase):
    """``ensure-sonarr-seed-series`` no longer has ``phase: post`` /
    ``priority: 87`` in core.yaml. The job is still REGISTERED so
    ``run_job(name)`` (auto-heal + operator) keeps resolving it
    AND the orchestrator's ``ensure_has_series`` ensurer delegates
    back to it; the bootstrap loader skips it because ``phase`` is
    absent."""

    def setUp(self) -> None:
        self.contracts = _ContractFixture()
        self.entry = self.contracts.core_jobs().get(
            "ensure-sonarr-seed-series",
        )
        self.assertIsNotNone(
            self.entry,
            "ensure-sonarr-seed-series disappeared from core.yaml — "
            "the cutover keeps it registered, just unscheduled. "
            "Restore the entry (without phase/priority) so run_job "
            "+ auto-heal + the lifecycle-method delegation still "
            "resolve it.",
        )

    def test_no_phase_field(self) -> None:
        # Restoring ``phase: post`` would put the job back on the
        # bootstrap-scheduled phase tree and double up with the
        # orchestrator's lifecycle dispatch via the
        # ``sonarr-has-series`` promise.
        self.assertNotIn(
            "phase", self.entry,
            "ensure-sonarr-seed-series has phase= again — the cutover "
            "removed it. Reverting means restoring phase: post + "
            "priority: 87 in core.yaml AND flipping the "
            "sonarr-has-series promise back to http_json + string "
            "ensured_by.",
        )

    def test_no_priority_field(self) -> None:
        self.assertNotIn(
            "priority", self.entry,
            "ensure-sonarr-seed-series has priority= again — kept "
            "paired with phase removal so reverting is a single-step "
            "diff.",
        )

    def test_handler_path_unchanged(self) -> None:
        # The orchestrator's LifecycleEnsurer:sonarr:ensure_has_series
        # delegates BACK to this handler via the wirer's injected
        # ``configure_handler``. Renaming the handler path silently
        # breaks both legacy run_job AND the orchestrator's lifecycle
        # delegate.
        self.assertEqual(
            self.entry.get("handler"),
            "media_stack.services.apps.core.job_adapters:"
            "ensure_sonarr_seed_series",
        )


class LegacyHandlerStillResolvable(unittest.TestCase):
    """The job entry's handler imports cleanly. Auto-heal,
    operator-dashboard ``run_job``, AND the orchestrator's
    ``ensure_has_series`` lifecycle delegate all resolve through
    the same shim path
    (``services.apps.core.job_adapters``). The shim path keeps the
    adapters → application hexagon ratchet clean (the lifecycle
    method's lazy import never reaches into ``application/``
    directly)."""

    def test_handler_imports_via_services_shim(self) -> None:
        import importlib
        mod = importlib.import_module(
            "media_stack.services.apps.core.job_adapters",
        )
        self.assertTrue(
            hasattr(mod, "ensure_sonarr_seed_series"),
            "ensure_sonarr_seed_series dropped from "
            "services.apps.core.job_adapters — breaks both legacy "
            "run_job AND the orchestrator's lifecycle method (which "
            "delegates to this handler via injected callables).",
        )


if __name__ == "__main__":
    unittest.main()
