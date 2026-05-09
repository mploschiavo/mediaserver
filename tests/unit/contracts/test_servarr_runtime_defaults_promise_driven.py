"""Pin the promise-driven wiring of the runtime-defaults cutover
(ADR-0005 Phase 3 — three Servarr promises share one ensurer per
the Bazarr monolithic-handler lesson).

Three promises (sonarr-quality-profiles, radarr-quality-profiles,
radarr-import-lists-auto) all flipped from string-typed
``ensured_by: apply-arr-runtime-defaults`` to ``{type: lifecycle,
service: <svc>, method: ensure_runtime_defaults}``. Their probes
flipped from ``http_json`` to lifecycle-typed too — each pointing at
its own per-promise probe method on ``ServarrLifecycle``. The legacy
``apply-arr-runtime-defaults`` job in ``core.yaml`` lost its ``phase:
post`` + ``priority: 55`` lines so the bootstrap loader stops
scheduling it directly — the orchestrator dispatches via the promise
registry instead.

This ratchet pins the contract-level shape so a future contract edit
can't silently undo it.

Sections:
  * EachPromiseUsesLifecycleDispatch — probe + ensurer are
    LifecycleProbe + LifecycleEnsurer with the correct service +
    method names. Per-promise probe methods differ; all three share
    ``ensure_runtime_defaults`` (rationale: the legacy handler is
    monolithic — one call patches every *arr's quality / import-list
    / SAB / delay-profile state in one pass; per-promise ensurers
    would mean three runs that each clobber the shared *arr settings
    document).
  * EachPromiseEnsurerIsLifecycleAndShared — explicit ratchet for
    the shared-ensurer invariant (Bazarr ``test_each_promise_ensurer
    _is_lifecycle_and_shared`` pattern). A future split would clobber
    state and silently break.
  * EachPromiseIsBlocking — explicit ``bootstrap_blocking: true``
    survived the move (proof-of-pattern convention).
  * LegacyJobRetired — ``apply-arr-runtime-defaults`` is GONE from
    core.yaml as of ADR-0005 Phase 5b.5; the orchestrator's
    lifecycle dispatch is the only path.
  * LegacyHandlerStillImportable — the underlying handler imports
    cleanly so the orchestrator's ``ensure_runtime_defaults``
    wide-handler delegate keeps reaching the same heavyweight
    whole-family code path.
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


# Per-promise (probe-method, expected-service) map. All three
# share ``ensure_runtime_defaults``.
_EXPECTED_PROMISES = (
    ("sonarr-quality-profiles",   "sonarr", "probe_quality_profiles"),
    ("radarr-quality-profiles",   "radarr", "probe_quality_profiles"),
    ("radarr-import-lists-auto",  "radarr", "probe_import_lists_auto"),
)
_SHARED_ENSURER_METHOD = "ensure_runtime_defaults"


class EachPromiseUsesLifecycleDispatch(unittest.TestCase):
    """Each runtime-defaults promise's probe + ensurer are
    LifecycleProbe + LifecycleEnsurer pointing at the right
    ServarrLifecycle methods. Reverting to the legacy ``http_json``
    probe + string ``ensured_by: apply-arr-runtime-defaults`` flips
    every assertion here."""

    def setUp(self) -> None:
        self.by_id = _LoadedRegistry.get().by_id()

    def test_each_promise_probe_is_lifecycle(self) -> None:
        from media_stack.domain.services.promises import LifecycleProbe
        for pid, expected_service, expected_method in _EXPECTED_PROMISES:
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
                promise.probe.service, expected_service,
                f"{pid}: probe.service expected {expected_service!r}",
            )
            self.assertEqual(
                promise.probe.method, expected_method,
                f"{pid}: probe.method expected {expected_method!r}",
            )


class EachPromiseEnsurerIsLifecycleAndShared(unittest.TestCase):
    """All three runtime-defaults promises point at the same ensurer
    method (``ensure_runtime_defaults``). Splitting into per-promise
    ensurers would mean three POSTs that each clobber the shared
    *arr settings document — the legacy handler is monolithic
    (Bazarr lesson)."""

    def setUp(self) -> None:
        self.by_id = _LoadedRegistry.get().by_id()

    def test_each_promise_ensurer_is_shared_per_service_job(self) -> None:
        """ADR-0010 Phase 7 — multiple per-service runtime-defaults
        promises share a single Job per *arr
        (``<service>:ensure-runtime-defaults``). The legacy ensurer
        is monolithic; per-promise Jobs would clobber the shared
        *arr settings document."""
        from media_stack.domain.services.promises import JobEnsurer
        for pid, expected_service, _expected_probe in _EXPECTED_PROMISES:
            promise = self.by_id[pid]
            self.assertIsInstance(
                promise.ensurer, JobEnsurer,
                f"{pid}: ensurer regressed from Job dispatch "
                f"(got {type(promise.ensurer).__name__})",
            )
            self.assertEqual(
                promise.ensurer.job_name,
                f"{expected_service}:ensure-runtime-defaults",
                f"{pid}: ensurer.job_name expected the per-service "
                f"shared runtime-defaults Job",
            )


class EachPromiseIsBlocking(unittest.TestCase):
    """Explicit ``bootstrap_blocking: true`` annotation survives the
    cutover. The Jellyfin Phase 2 family proof set the
    explicit-on-cutover-proofs convention; this Phase 3 family
    follows it."""

    def setUp(self) -> None:
        self.by_id = _LoadedRegistry.get().by_id()

    def test_each_promise_is_blocking(self) -> None:
        for pid, _service, _method in _EXPECTED_PROMISES:
            promise = self.by_id[pid]
            self.assertTrue(
                promise.bootstrap_blocking,
                f"{pid}: bootstrap_blocking flipped to False — "
                f"the cutover proof requires explicit-True so "
                f"orchestrator-driven bootstrap waits for it.",
            )


class LegacyJobRetired(unittest.TestCase):
    """``apply-arr-runtime-defaults`` is GONE from core.yaml as of
    ADR-0005 Phase 5b.5. The orchestrator's lifecycle dispatch via
    the three runtime-defaults promises is the only path; auto-heal
    and the operator dashboard route through the orchestrator
    too."""

    def setUp(self) -> None:
        self.contracts = _ContractFixture()

    def test_legacy_registration_is_gone(self) -> None:
        self.assertNotIn(
            "apply-arr-runtime-defaults", self.contracts.core_jobs(),
            "apply-arr-runtime-defaults reappeared in core.yaml — "
            "ADR-0005 Phase 5b.5 retired the registration shell. "
            "Reverting means restoring the entry (with phase: post + "
            "priority: 55 + requires: [arr_apps_reachable]) AND "
            "flipping every *-quality-profiles + *-import-lists-auto "
            "promise back to http_json + string ensured_by.",
        )


class LegacyHandlerStillImportable(unittest.TestCase):
    """The underlying ``apply_arr_runtime_defaults`` handler stays
    importable because the orchestrator's
    ``ServarrLifecycle.ensure_runtime_defaults`` wide-handler-
    delegates back to it via injected ``configure_handler`` +
    ``job_context_factory`` callables."""

    def test_handler_imports(self) -> None:
        import importlib
        mod = importlib.import_module(
            "media_stack.services.apps.core.job_adapters",
        )
        self.assertTrue(
            hasattr(mod, "apply_arr_runtime_defaults"),
            "apply_arr_runtime_defaults dropped from job_adapters — "
            "breaks the orchestrator's lifecycle method (which "
            "wide-handler-delegates back to the legacy handler via "
            "injected callables).",
        )


if __name__ == "__main__":
    unittest.main()
