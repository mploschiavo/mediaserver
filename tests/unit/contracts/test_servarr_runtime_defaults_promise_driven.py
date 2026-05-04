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
  * LegacyJobUnscheduled — ``apply-arr-runtime-defaults`` is still
    REGISTERED in core.yaml (handler + label + requires) but has NO
    ``phase`` field, so ``discover_jobs_from_contracts`` skips it
    from the bootstrap DAG.
  * LegacyJobStillResolvable — the registered handler imports
    cleanly so ``run_job(name)`` and the auto-heal cycle keep working
    even though the bootstrap loader skips it.
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

    def test_each_promise_ensurer_is_lifecycle_and_shared(self) -> None:
        from media_stack.domain.services.promises import LifecycleEnsurer
        for pid, expected_service, _expected_probe in _EXPECTED_PROMISES:
            promise = self.by_id[pid]
            self.assertIsInstance(
                promise.ensurer, LifecycleEnsurer,
                f"{pid}: ensurer regressed from lifecycle dispatch "
                f"(got {type(promise.ensurer).__name__})",
            )
            self.assertEqual(
                promise.ensurer.service, expected_service,
                f"{pid}: ensurer.service expected {expected_service!r}",
            )
            self.assertEqual(
                promise.ensurer.method, _SHARED_ENSURER_METHOD,
                f"{pid}: ensurer.method expected "
                f"{_SHARED_ENSURER_METHOD!r}. All three runtime-"
                "defaults promises intentionally share one ensurer "
                "— the legacy ``apply_arr_runtime_defaults`` is "
                "monolithic and per-promise ensurers would clobber "
                "the shared *arr settings.",
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


class LegacyJobUnscheduled(unittest.TestCase):
    """``apply-arr-runtime-defaults`` no longer has ``phase: post``
    or ``priority: 55`` in core.yaml. The job is still REGISTERED so
    ``run_job(name)`` (auto-heal + operator) keeps resolving it; the
    bootstrap loader skips it because ``phase`` is absent."""

    def setUp(self) -> None:
        self.contracts = _ContractFixture()
        self.entry = self.contracts.core_jobs().get(
            "apply-arr-runtime-defaults",
        )
        self.assertIsNotNone(
            self.entry,
            "apply-arr-runtime-defaults disappeared from core.yaml — "
            "the cutover keeps it registered, just unscheduled. "
            "Restore the entry (without phase/priority) so run_job + "
            "auto-heal still resolve it.",
        )

    def test_no_phase_field(self) -> None:
        self.assertNotIn(
            "phase", self.entry,
            "apply-arr-runtime-defaults has phase= again — the "
            "cutover removed it. Reverting means restoring "
            "phase: post + priority: 55 in core.yaml AND flipping "
            "every *-quality-profiles + *-import-lists-auto promise "
            "back to http_json + string ensured_by.",
        )

    def test_no_priority_field(self) -> None:
        self.assertNotIn(
            "priority", self.entry,
            "apply-arr-runtime-defaults has priority= again — kept "
            "paired with phase removal so reverting is a single-step "
            "diff.",
        )

    def test_handler_path_unchanged(self) -> None:
        # The orchestrator's
        # LifecycleEnsurer:<svc>:ensure_runtime_defaults wide-handler-
        # delegates back to this job's handler, so the path MUST
        # stay intact.
        self.assertEqual(
            self.entry.get("handler"),
            "media_stack.services.apps.core.job_adapters:"
            "apply_arr_runtime_defaults",
        )

    def test_requires_chain_preserved(self) -> None:
        # ``requires: [arr_apps_reachable]`` keeps the relative
        # ordering for cases where ``run_job`` is invoked manually.
        # Drop it and any operator-driven ``run_job
        # apply-arr-runtime-defaults`` would race the *arr reach
        # probe.
        self.assertEqual(
            list(self.entry.get("requires") or []),
            ["arr_apps_reachable"],
        )


class LegacyJobStillResolvable(unittest.TestCase):
    """The job entry's handler imports cleanly. Auto-heal and
    operator-dashboard ``run_job`` still resolve through
    ``get_job_registry()`` even when the bootstrap loader skips
    the job."""

    def test_handler_imports(self) -> None:
        import importlib
        mod = importlib.import_module(
            "media_stack.services.apps.core.job_adapters",
        )
        self.assertTrue(
            hasattr(mod, "apply_arr_runtime_defaults"),
            "apply_arr_runtime_defaults dropped from job_adapters — "
            "breaks both legacy run_job AND the orchestrator's "
            "lifecycle method (which wide-handler-delegates back to "
            "the legacy handler via injected callables).",
        )


if __name__ == "__main__":
    unittest.main()
