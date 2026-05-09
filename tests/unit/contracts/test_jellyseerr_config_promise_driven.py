"""Pin the promise-driven wiring of the Jellyseerr config-wiring
cutover (ADR-0005 Phase 3 — Jellyseerr family; Phase 5b.5 retired
the registration shells).

Three promises (jellyseerr-oidc, jellyseerr-application-url,
jellyseerr-arr-servers) all flipped from string-typed
``ensured_by: ensure-jellyseerr-oidc`` / ``configure-jellyseerr``
to ``{type: lifecycle, service: jellyseerr, method: ...}``. The
probes flipped from ``http_json`` / ``file_json`` to lifecycle-
typed too. The legacy ``ensure-jellyseerr-oidc`` job in
``core.yaml`` and ``configure-jellyseerr`` job in
``jellyseerr.yaml`` were deleted in Phase 5b.5; the orchestrator's
lifecycle dispatch is now the only path. The legacy
``configure_jellyseerr`` handler in
``application.jellyseerr.configure_jellyseerr_job`` stays
importable because ``JellyseerrConfigWirer.ensure_arr_servers``
wide-handler-delegates back to it; the legacy
``ensure_jellyseerr_oidc`` handler in
``services/apps/core/job_adapters.py`` is genuinely orphan now
(the wirer owns the OIDC settings.json mutation directly) and is
flagged for Phase 5c+ deletion.

This ratchet pins the contract-level shape so a future contract
edit can't silently undo it.

Sections:
  * EachPromiseUsesLifecycleDispatch — probe + ensurer are
    LifecycleProbe + LifecycleEnsurer with the correct service +
    method names.
  * EachPromiseIsBlocking — explicit ``bootstrap_blocking: true``
    survived the cutover (proof-of-pattern convention).
  * LegacyJobsRetired — both legacy job registrations are GONE.
  * LegacyConfigureHandlerStillImportable — the
    ``configure_jellyseerr`` handler stays so the wirer's
    wide-handler delegation keeps working.
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

    def test_each_promise_ensurer_is_job(self) -> None:
        """ADR-0010 Phase 7 — each Jellyseerr promise's ensurer is
        a JobEnsurer pointing at the contract Job whose handler
        binds to the corresponding ``JellyseerrLifecycle.<method>``."""
        from media_stack.domain.services.promises import JobEnsurer
        for pid, _probe_method, ensure_method in self._EXPECTED:
            promise = self.by_id[pid]
            self.assertIsInstance(
                promise.ensurer, JobEnsurer,
                f"{pid}: ensurer regressed from Job dispatch "
                f"(got {type(promise.ensurer).__name__})",
            )
            # ``ensure_oidc`` → ``jellyseerr:ensure-oidc`` etc.
            expected_job = (
                f"jellyseerr:{ensure_method.replace('_', '-')}"
                .replace("ensure-", "ensure-")
            )
            self.assertEqual(
                promise.ensurer.job_name, expected_job,
                f"{pid}: ensurer.job_name expected {expected_job!r}",
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


class LegacyJobsRetired(unittest.TestCase):
    """Both legacy job registrations are GONE as of ADR-0005
    Phase 5b.5. The orchestrator's lifecycle dispatch via the
    Jellyseerr config promises is the only path; auto-heal and the
    operator dashboard route through the orchestrator too."""

    def setUp(self) -> None:
        self.contracts = _ContractFixture()

    def test_oidc_registration_is_gone(self) -> None:
        self.assertNotIn(
            "ensure-jellyseerr-oidc", self.contracts.core_jobs(),
            "ensure-jellyseerr-oidc reappeared in core.yaml — "
            "ADR-0005 Phase 5b.5 retired the registration shell. "
            "Reverting means restoring the entry (with phase: post + "
            "priority: 88) AND flipping the jellyseerr-oidc + "
            "jellyseerr-application-url promises back to the legacy "
            "http_json/file_json probes + string ensured_by.",
        )

    def test_configure_registration_is_gone(self) -> None:
        self.assertNotIn(
            "configure-jellyseerr", self.contracts.jellyseerr_jobs(),
            "configure-jellyseerr reappeared in jellyseerr.yaml — "
            "ADR-0005 Phase 5b.5 retired the registration shell. "
            "Reverting means restoring the entry (with phase: post + "
            "priority: 5) AND flipping the jellyseerr-arr-servers "
            "promise back to ensured_by: configure-jellyseerr.",
        )


class LegacyConfigureHandlerStillImportable(unittest.TestCase):
    """``configure_jellyseerr`` stays importable because the
    orchestrator's ``JellyseerrLifecycle.ensure_arr_servers``
    wide-handler-delegates back to it via injected callables. The
    ``ensure_jellyseerr_oidc`` handler is genuinely orphan now (the
    wirer owns OIDC settings.json mutation directly) — flagged for
    Phase 5c+ deletion. We don't pin it here so the deletion can
    happen without churning this file."""

    def test_configure_handler_imports(self) -> None:
        import importlib
        mod = importlib.import_module(
            "media_stack.application.jellyseerr.configure_jellyseerr_job",
        )
        self.assertTrue(
            hasattr(mod, "configure_jellyseerr"),
            "configure_jellyseerr dropped from "
            "application.jellyseerr.configure_jellyseerr_job — "
            "breaks the orchestrator's ensure_arr_servers wide-"
            "handler delegation.",
        )


if __name__ == "__main__":
    unittest.main()
