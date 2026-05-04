"""Pin the promise-driven wiring of the Jellyseerr config-wiring
cutover (ADR-0005 Phase 3 — Jellyseerr family).

Three promises (jellyseerr-oidc, jellyseerr-application-url,
jellyseerr-arr-servers) all flipped from string-typed
``ensured_by: ensure-jellyseerr-oidc`` / ``configure-jellyseerr``
to ``{type: lifecycle, service: jellyseerr, method: ...}``. The
probes flipped from ``http_json`` / ``file_json`` to lifecycle-
typed too. The legacy ``ensure-jellyseerr-oidc`` job in
``core.yaml`` and ``configure-jellyseerr`` job in
``jellyseerr.yaml`` lost their ``phase: post`` so the bootstrap
loader stops scheduling them directly — the orchestrator
dispatches via the promise registry instead.

This ratchet pins the contract-level shape so a future contract
edit can't silently undo it.

Sections:
  * EachPromiseUsesLifecycleDispatch — probe + ensurer are
    LifecycleProbe + LifecycleEnsurer with the correct service +
    method names.
  * EachPromiseIsBlocking — explicit ``bootstrap_blocking: true``
    survived the cutover (proof-of-pattern convention).
  * LegacyJobsUnscheduled — both legacy jobs retained their handler
    + label entries but lost ``phase``, so
    ``discover_jobs_from_contracts`` skips them from the bootstrap
    DAG.
  * LegacyHandlersStillResolvable — the registered handlers import
    cleanly so ``run_job(name)`` and the auto-heal cycle keep
    working even though the bootstrap loader never picks them up.
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
        self._jellyseerr = yaml.safe_load(
            (_REPO_ROOT / "contracts" / "services" / "jellyseerr.yaml")
            .read_text(encoding="utf-8")
        )

    def core_jobs(self) -> dict:
        return (self._core.get("plugin") or {}).get("jobs") or {}

    def jellyseerr_jobs(self) -> dict:
        return (self._jellyseerr.get("plugin") or {}).get("jobs") or {}


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


class EachPromiseUsesLifecycleDispatch(unittest.TestCase):
    """Each Jellyseerr config promise's probe + ensurer are
    LifecycleProbe + LifecycleEnsurer pointing at the
    JellyseerrLifecycle methods. Reverting to the legacy
    ``http_json``/``file_json`` probe + string ``ensured_by`` flips
    every assertion here."""

    _EXPECTED = (
        ("jellyseerr-oidc",
         "probe_oidc", "ensure_oidc"),
        ("jellyseerr-application-url",
         "probe_application_url", "ensure_application_url"),
        ("jellyseerr-arr-servers",
         "probe_arr_servers", "ensure_arr_servers"),
    )

    def setUp(self) -> None:
        self.by_id = _LoadedRegistry.get().by_id()

    def test_each_promise_probe_is_lifecycle(self) -> None:
        from media_stack.domain.services.promises import LifecycleProbe
        for pid, probe_method, _ensure_method in self._EXPECTED:
            promise = self.by_id.get(pid)
            self.assertIsNotNone(
                promise, f"{pid!r} dropped out of registry",
            )
            self.assertIsInstance(
                promise.probe, LifecycleProbe,
                f"{pid}: probe regressed from lifecycle dispatch "
                f"(got {type(promise.probe).__name__})",
            )
            self.assertEqual(
                promise.probe.service, "jellyseerr",
                f"{pid}: probe.service expected 'jellyseerr'",
            )
            self.assertEqual(
                promise.probe.method, probe_method,
                f"{pid}: probe.method expected {probe_method!r}",
            )

    def test_each_promise_ensurer_is_lifecycle(self) -> None:
        from media_stack.domain.services.promises import LifecycleEnsurer
        for pid, _probe_method, ensure_method in self._EXPECTED:
            promise = self.by_id[pid]
            self.assertIsInstance(
                promise.ensurer, LifecycleEnsurer,
                f"{pid}: ensurer regressed from lifecycle dispatch "
                f"(got {type(promise.ensurer).__name__})",
            )
            self.assertEqual(
                promise.ensurer.service, "jellyseerr",
                f"{pid}: ensurer.service expected 'jellyseerr'",
            )
            self.assertEqual(
                promise.ensurer.method, ensure_method,
                f"{pid}: ensurer.method expected {ensure_method!r}",
            )


class EachPromiseIsBlocking(unittest.TestCase):
    """Explicit ``bootstrap_blocking: true`` annotation survives
    the cutover. The Jellyfin Phase 2 family proof set the
    explicit-on-cutover-proofs convention; this Jellyseerr family
    follows it."""

    _IDS = (
        "jellyseerr-oidc",
        "jellyseerr-application-url",
        "jellyseerr-arr-servers",
    )

    def setUp(self) -> None:
        self.by_id = _LoadedRegistry.get().by_id()

    def test_each_promise_is_blocking(self) -> None:
        for pid in self._IDS:
            promise = self.by_id[pid]
            self.assertTrue(
                promise.bootstrap_blocking,
                f"{pid}: bootstrap_blocking flipped to False — "
                f"the cutover proof requires explicit-True so "
                f"orchestrator-driven bootstrap waits for it.",
            )


class LegacyJobsUnscheduled(unittest.TestCase):
    """Both legacy jobs are still registered (handler + label +
    requires) but no longer have ``phase: post``. Without ``phase``
    the bootstrap loader skips them; ``run_job(name)`` (auto-heal +
    operator) keeps resolving them."""

    def setUp(self) -> None:
        self.contracts = _ContractFixture()
        self.oidc_entry = self.contracts.core_jobs().get(
            "ensure-jellyseerr-oidc",
        )
        self.configure_entry = self.contracts.jellyseerr_jobs().get(
            "configure-jellyseerr",
        )

    def test_oidc_job_still_registered(self) -> None:
        self.assertIsNotNone(
            self.oidc_entry,
            "ensure-jellyseerr-oidc disappeared from core.yaml — "
            "the cutover keeps it registered, just unscheduled. "
            "Restore the entry (without phase) so run_job + "
            "auto-heal still resolve it.",
        )

    def test_configure_job_still_registered(self) -> None:
        self.assertIsNotNone(
            self.configure_entry,
            "configure-jellyseerr disappeared from jellyseerr.yaml — "
            "the cutover keeps it registered, just unscheduled.",
        )

    def test_oidc_job_has_no_phase(self) -> None:
        self.assertNotIn(
            "phase", self.oidc_entry,
            "ensure-jellyseerr-oidc has phase= again — the cutover "
            "removed it. Reverting means restoring phase: post + "
            "priority: 88 in core.yaml AND flipping the jellyseerr-oidc "
            "+ jellyseerr-application-url promises back to the legacy "
            "http_json/file_json probes + string ensured_by.",
        )

    def test_configure_job_has_no_phase(self) -> None:
        self.assertNotIn(
            "phase", self.configure_entry,
            "configure-jellyseerr has phase= again — the cutover "
            "removed it. Reverting means restoring phase: post + "
            "priority: 5 in jellyseerr.yaml AND flipping the "
            "jellyseerr-arr-servers promise back to "
            "ensured_by: configure-jellyseerr.",
        )

    def test_oidc_handler_path_unchanged(self) -> None:
        # The orchestrator's LifecycleEnsurer path uses
        # JellyseerrLifecycle.ensure_oidc, which mutates settings.json
        # in the same shape as the legacy handler. The legacy job's
        # handler MUST stay so run_job + auto-heal keep working.
        self.assertEqual(
            self.oidc_entry.get("handler"),
            "media_stack.services.apps.core.job_adapters:"
            "ensure_jellyseerr_oidc",
        )

    def test_configure_handler_path_unchanged(self) -> None:
        self.assertEqual(
            self.configure_entry.get("handler"),
            "media_stack.services.apps.jellyseerr."
            "configure_jellyseerr_job:configure_jellyseerr",
        )


class LegacyHandlersStillResolvable(unittest.TestCase):
    """The job entries' handlers import cleanly. Auto-heal and
    operator-dashboard ``run_job`` still resolve through
    ``get_job_registry()`` even when the bootstrap loader skips
    the jobs."""

    def test_oidc_handler_imports(self) -> None:
        import importlib
        mod = importlib.import_module(
            "media_stack.services.apps.core.job_adapters",
        )
        self.assertTrue(
            hasattr(mod, "ensure_jellyseerr_oidc"),
            "ensure_jellyseerr_oidc dropped from job_adapters — "
            "breaks both legacy run_job AND the orchestrator's "
            "lifecycle method (which the legacy handler is the "
            "reference implementation for).",
        )

    def test_configure_handler_imports(self) -> None:
        import importlib
        mod = importlib.import_module(
            "media_stack.application.jellyseerr.configure_jellyseerr_job",
        )
        self.assertTrue(
            hasattr(mod, "configure_jellyseerr"),
            "configure_jellyseerr dropped from "
            "application.jellyseerr.configure_jellyseerr_job — "
            "breaks both legacy run_job AND the orchestrator's "
            "ensure_arr_servers (which delegates back to it).",
        )


if __name__ == "__main__":
    unittest.main()
