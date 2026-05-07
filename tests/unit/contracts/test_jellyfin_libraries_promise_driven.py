"""Pin the promise-driven wiring of the jellyfin-libraries cutover
(ADR-0005 Phase 5b — the 10th and final wirer).

The ``jellyfin-libraries`` promise flipped from string-typed
``ensured_by: ensure-jellyfin-libraries`` to ``{type: lifecycle,
service: jellyfin, method: ensure_libraries}``. Its probe flipped
from ``http_json`` to lifecycle-typed too. The legacy
``ensure-jellyfin-libraries`` job in ``core.yaml`` lost its
``phase: post`` + ``priority: 86`` lines so the bootstrap loader
stops scheduling it — the orchestrator dispatches via the promise
registry instead. The legacy handler stays REGISTERED so
``run_job(name)`` (auto-heal + operator dashboard) keeps reaching
the heavyweight handler.

This ratchet pins the contract-level shape so a future contract
edit can't silently undo it.

Sections:
  * PromiseUsesLifecycleDispatch — probe + ensurer are
    LifecycleProbe + LifecycleEnsurer with the correct service +
    method names.
  * PromiseIsBlocking — explicit ``bootstrap_blocking: true``
    survived the move (matches the *-jellyfin-notifier convention).
  * LegacyJobUnscheduled — ``ensure-jellyfin-libraries`` is still
    REGISTERED in core.yaml (handler + label) but has NO ``phase``
    / ``priority`` field, so ``discover_jobs_from_contracts``
    doesn't place it on the bootstrap-scheduled phase tree.
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


class PromiseUsesLifecycleDispatch(unittest.TestCase):
    """The ``jellyfin-libraries`` promise's probe + ensurer are
    LifecycleProbe + LifecycleEnsurer pointing at the
    ``JellyfinLifecycle.probe_libraries`` / ``ensure_libraries``
    methods."""

    _PROMISE_ID = "jellyfin-libraries"

    def setUp(self) -> None:
        self.by_id = _LoadedRegistry.get().by_id()
        self.promise = self.by_id.get(self._PROMISE_ID)
        self.assertIsNotNone(
            self.promise,
            f"{self._PROMISE_ID!r} dropped out of registry",
        )

    def test_promise_probe_is_lifecycle(self) -> None:
        from media_stack.domain.services.promises import LifecycleProbe
        self.assertIsInstance(
            self.promise.probe, LifecycleProbe,
            f"{self._PROMISE_ID}: probe regressed from lifecycle "
            f"dispatch (got {type(self.promise.probe).__name__})",
        )
        self.assertEqual(
            self.promise.probe.service, "jellyfin",
            f"{self._PROMISE_ID}: probe.service expected 'jellyfin'",
        )
        self.assertEqual(
            self.promise.probe.method, "probe_libraries",
            f"{self._PROMISE_ID}: probe.method expected "
            "'probe_libraries'",
        )

    def test_promise_ensurer_is_lifecycle(self) -> None:
        from media_stack.domain.services.promises import LifecycleEnsurer
        self.assertIsInstance(
            self.promise.ensurer, LifecycleEnsurer,
            f"{self._PROMISE_ID}: ensurer regressed from lifecycle "
            f"dispatch (got {type(self.promise.ensurer).__name__})",
        )
        self.assertEqual(
            self.promise.ensurer.service, "jellyfin",
            f"{self._PROMISE_ID}: ensurer.service expected 'jellyfin'",
        )
        self.assertEqual(
            self.promise.ensurer.method, "ensure_libraries",
            f"{self._PROMISE_ID}: ensurer.method expected "
            "'ensure_libraries'",
        )


class PromiseIsBlocking(unittest.TestCase):
    """Explicit ``bootstrap_blocking: true`` annotation survives the
    cutover. Matches the *-jellyfin-notifier / *-has-indexers /
    *-download-client convention from earlier Phase 3 + 5b
    cutovers."""

    def setUp(self) -> None:
        self.promise = _LoadedRegistry.get().by_id()["jellyfin-libraries"]

    def test_promise_is_blocking(self) -> None:
        self.assertTrue(
            self.promise.bootstrap_blocking,
            "jellyfin-libraries: bootstrap_blocking flipped to "
            "False — the cutover requires explicit-True so "
            "orchestrator-driven bootstrap waits for it.",
        )


class LegacyJobUnscheduled(unittest.TestCase):
    """``ensure-jellyfin-libraries`` no longer has ``phase: post`` /
    ``priority: 86`` in core.yaml. The job is still REGISTERED so
    ``run_job(name)`` (auto-heal + operator) keeps resolving it for
    full-pipeline reconcile; the bootstrap loader skips it because
    ``phase`` is absent."""

    def setUp(self) -> None:
        self.contracts = _ContractFixture()
        self.entry = self.contracts.core_jobs().get(
            "ensure-jellyfin-libraries",
        )
        self.assertIsNotNone(
            self.entry,
            "ensure-jellyfin-libraries disappeared from core.yaml — "
            "the cutover keeps it registered, just unscheduled. "
            "Restore the entry (without phase) so run_job + auto-heal "
            "still resolve it.",
        )

    def test_no_phase_field(self) -> None:
        # ``phase: post`` would put the job back on the bootstrap-
        # scheduled phase tree and double up with the orchestrator's
        # lifecycle dispatch via the jellyfin-libraries promise.
        self.assertNotIn(
            "phase", self.entry,
            "ensure-jellyfin-libraries has phase= again — the "
            "cutover removed it. Reverting means restoring "
            "phase: post + priority: 86 in core.yaml AND flipping "
            "the jellyfin-libraries promise back to http_json + "
            "string ensured_by.",
        )

    def test_no_priority_field(self) -> None:
        self.assertNotIn(
            "priority", self.entry,
            "ensure-jellyfin-libraries has priority= again — kept "
            "paired with phase removal so reverting is a single-step "
            "diff.",
        )

    def test_handler_path_unchanged(self) -> None:
        # The orchestrator's
        # LifecycleEnsurer:jellyfin:ensure_libraries is implemented
        # in JellyfinLifecycle, but the legacy job's handler MUST
        # stay so run_job + auto-heal keep reaching the heavyweight
        # ensure_jellyfin_libraries path.
        self.assertEqual(
            self.entry.get("handler"),
            "media_stack.services.apps.core.job_adapters:ensure_jellyfin_libraries",
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
            hasattr(mod, "ensure_jellyfin_libraries"),
            "ensure_jellyfin_libraries dropped from job_adapters — "
            "breaks legacy run_job AND removes the reference "
            "implementation the orchestrator's lifecycle method "
            "mirrors.",
        )


if __name__ == "__main__":
    unittest.main()
