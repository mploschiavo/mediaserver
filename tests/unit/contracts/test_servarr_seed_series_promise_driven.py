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
  * LegacyJobRetired — ``ensure-sonarr-seed-series`` is GONE from
    core.yaml as of ADR-0005 Phase 5b.5; the orchestrator's
    lifecycle dispatch is the only path.
  * LegacyHandlerStillImportable — the underlying handler imports
    cleanly so the orchestrator's ``ensure_has_series`` wide-
    handler delegate keeps reaching the same heavyweight Sonarr-
    API + tvdbId-lookup code path.
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

    def test_promise_ensurer_is_job(self) -> None:
        """ADR-0010 Phase 7 — Sonarr seed-series promise routes via
        ``run_job(sonarr:ensure-has-series)``."""
        from media_stack.domain.services.promises import JobEnsurer
        promise = self.by_id[self._PROMISE_ID]
        self.assertIsInstance(
            promise.ensurer, JobEnsurer,
            f"{self._PROMISE_ID}: ensurer regressed from Job "
            f"dispatch (got {type(promise.ensurer).__name__})",
        )
        self.assertEqual(
            promise.ensurer.job_name,
            f"{self._EXPECTED_SERVICE}:ensure-has-series",
            f"{self._PROMISE_ID}: ensurer.job_name expected the "
            f"sonarr seed-series Job",
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


class LegacyJobRetired(unittest.TestCase):
    """``ensure-sonarr-seed-series`` is GONE from core.yaml as of
    ADR-0005 Phase 5b.5. The orchestrator's lifecycle dispatch via
    the ``sonarr-has-series`` promise is the only path; auto-heal
    and the operator dashboard route through the orchestrator
    too."""

    def setUp(self) -> None:
        self.contracts = _ContractFixture()

    def test_legacy_registration_is_gone(self) -> None:
        self.assertNotIn(
            "ensure-sonarr-seed-series", self.contracts.core_jobs(),
            "ensure-sonarr-seed-series reappeared in core.yaml — "
            "ADR-0005 Phase 5b.5 retired the registration shell. "
            "Reverting means restoring the entry (with phase: post "
            "+ priority: 87) AND flipping the sonarr-has-series "
            "promise back to http_json + string ensured_by.",
        )


class LegacyHandlerStillImportable(unittest.TestCase):
    """The underlying ``ensure_sonarr_seed_series`` handler stays
    importable because the orchestrator's
    ``ServarrLifecycle.ensure_has_series`` wirer wide-handler-
    delegates back to it via injected ``configure_handler``. The
    shim path through ``services.apps.core.job_adapters`` keeps
    the adapters → application hexagon ratchet clean (the
    lifecycle method's lazy import never reaches into
    ``application/`` directly)."""

    def test_handler_imports_via_services_shim(self) -> None:
        import importlib
        mod = importlib.import_module(
            "media_stack.services.apps.core.job_adapters",
        )
        self.assertTrue(
            hasattr(mod, "ensure_sonarr_seed_series"),
            "ensure_sonarr_seed_series dropped from "
            "services.apps.core.job_adapters — breaks the "
            "orchestrator's lifecycle method (which delegates to "
            "this handler via injected callables).",
        )


if __name__ == "__main__":
    unittest.main()
