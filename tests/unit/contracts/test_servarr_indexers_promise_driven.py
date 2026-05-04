"""Pin the promise-driven wiring of the *arr indexer-pipeline
cutover (ADR-0005 Phase 3 follow-on to the *-jellyfin-notifier
proof-of-pattern).

Two promises (sonarr-has-indexers, radarr-has-indexers) flipped
from string-typed ``ensured_by: push-indexers`` to
``{type: lifecycle, service: <svc>, method: ensure_indexers}``.
Their probes flipped from ``http_json`` to lifecycle-typed too.
The legacy ``push-indexers`` job in ``core.yaml`` lost its
``phase: download_clients`` + ``priority: 40`` lines so the
bootstrap loader stops scheduling it on the per-phase tree —
the orchestrator dispatches via the promise registry instead.
The legacy handler stays REGISTERED so ``run_job(name)`` (auto-
heal + operator dashboard) keeps reaching the heavyweight
whole-pipeline path.

This ratchet pins the contract-level shape so a future contract
edit can't silently undo it.

Sections:
  * EachPromiseUsesLifecycleDispatch — probe + ensurer are
    LifecycleProbe + LifecycleEnsurer with the correct service +
    method names.
  * EachPromiseIsBlocking — explicit ``bootstrap_blocking: true``
    survived the move (matches the *-jellyfin-notifier convention).
  * LegacyJobUnscheduled — ``push-indexers`` is still REGISTERED
    in core.yaml (handler + label + after-chain) but has NO
    ``phase`` field, so ``discover_jobs_from_contracts`` doesn't
    place it on the bootstrap-scheduled phase tree.
  * LegacyJobStillResolvable — the registered handler resolves
    cleanly so ``run_job(name)`` and the auto-heal cycle keep
    working even though the bootstrap loader skips it.
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


class EachPromiseUsesLifecycleDispatch(unittest.TestCase):
    """Each *-has-indexers promise's probe + ensurer are
    LifecycleProbe + LifecycleEnsurer pointing at the per-service
    ServarrLifecycle methods. Reverting to the legacy ``http_json``
    probe + string ``ensured_by: push-indexers`` flips every
    assertion here."""

    _EXPECTED = (
        ("sonarr-has-indexers", "sonarr"),
        ("radarr-has-indexers", "radarr"),
    )

    def setUp(self) -> None:
        self.by_id = _LoadedRegistry.get().by_id()

    def test_each_promise_probe_is_lifecycle(self) -> None:
        from media_stack.domain.services.promises import LifecycleProbe
        for pid, expected_service in self._EXPECTED:
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
                promise.probe.method, "probe_has_indexers",
                f"{pid}: probe.method expected probe_has_indexers",
            )

    def test_each_promise_ensurer_is_lifecycle(self) -> None:
        from media_stack.domain.services.promises import LifecycleEnsurer
        for pid, expected_service in self._EXPECTED:
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
                promise.ensurer.method, "ensure_indexers",
                f"{pid}: ensurer.method expected ensure_indexers",
            )


class EachPromiseIsBlocking(unittest.TestCase):
    """Explicit ``bootstrap_blocking: true`` annotation survives
    the cutover. The Jellyfin Phase 2 family proof set the
    explicit-on-cutover-proofs convention; the *-jellyfin-notifier
    Phase 3 family followed it; this Phase 3 follow-on does too."""

    _IDS = (
        "sonarr-has-indexers",
        "radarr-has-indexers",
    )

    def setUp(self) -> None:
        self.by_id = _LoadedRegistry.get().by_id()

    def test_each_promise_is_blocking(self) -> None:
        for pid in self._IDS:
            promise = self.by_id[pid]
            self.assertTrue(
                promise.bootstrap_blocking,
                f"{pid}: bootstrap_blocking flipped to False — "
                f"the cutover requires explicit-True so "
                f"orchestrator-driven bootstrap waits for it.",
            )


class LegacyJobUnscheduled(unittest.TestCase):
    """``push-indexers`` no longer has ``phase: download_clients`` /
    ``priority: 40`` in core.yaml. The job is still REGISTERED so
    ``run_job(name)`` (auto-heal + operator) keeps resolving it
    for full-pipeline reconcile; the bootstrap loader skips it
    because ``phase`` is absent."""

    def setUp(self) -> None:
        self.contracts = _ContractFixture()
        self.entry = self.contracts.core_jobs().get("push-indexers")
        self.assertIsNotNone(
            self.entry,
            "push-indexers disappeared from core.yaml — the cutover "
            "keeps it registered, just unscheduled. Restore the "
            "entry (without phase) so run_job + auto-heal still "
            "resolve it.",
        )

    def test_no_phase_field(self) -> None:
        # ``phase: download_clients`` would put the job back on the
        # bootstrap-scheduled phase tree and double up with the
        # orchestrator's lifecycle dispatch via the *-has-indexers
        # promises.
        self.assertNotIn(
            "phase", self.entry,
            "push-indexers has phase= again — the cutover removed "
            "it. Reverting means restoring phase: download_clients "
            "+ priority: 40 in core.yaml AND flipping every "
            "*-has-indexers promise back to http_json + string "
            "ensured_by.",
        )

    def test_no_priority_field(self) -> None:
        self.assertNotIn(
            "priority", self.entry,
            "push-indexers has priority= again — kept paired with "
            "phase removal so reverting is a single-step diff.",
        )

    def test_handler_path_unchanged(self) -> None:
        # The orchestrator's LifecycleEnsurer:<svc>:ensure_indexers
        # is implemented in ServarrLifecycle, but the legacy job's
        # handler MUST stay so run_job + auto-heal keep reaching
        # the heavyweight whole-pipeline path.
        self.assertEqual(
            self.entry.get("handler"),
            "media_stack.services.apps.core.job_adapters:push_indexers",
        )

    def test_after_chain_preserved(self) -> None:
        # ``after: [reset-prowlarr-app-mappings]`` keeps the relative
        # ordering for the cases ``run_job`` is invoked manually.
        # Drop it and any operator-driven ``run_job push-indexers``
        # would race the mapping-reset job.
        self.assertEqual(
            list(self.entry.get("after") or []),
            ["reset-prowlarr-app-mappings"],
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
            hasattr(mod, "push_indexers"),
            "push_indexers dropped from job_adapters — breaks "
            "both legacy run_job AND the orchestrator's lifecycle "
            "method (which the legacy handler is the reference "
            "implementation for).",
        )


if __name__ == "__main__":
    unittest.main()
