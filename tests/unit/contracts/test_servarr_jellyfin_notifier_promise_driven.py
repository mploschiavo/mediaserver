"""Pin the promise-driven wiring of the *arr Jellyfin-notifier
cutover (ADR-0005 Phase 3 proof-of-pattern).

Three promises (sonarr-jellyfin-notifier, radarr-jellyfin-notifier,
lidarr-jellyfin-notifier) all flipped from string-typed
``ensured_by: ensure-arr-jellyfin-notifier`` to
``{type: lifecycle, service: <svc>, method: ensure_jellyfin_notifier}``.
Their probes flipped from ``http_json`` to lifecycle-typed too. The
legacy ``ensure-arr-jellyfin-notifier`` job in ``core.yaml`` lost
its ``phase: post`` line so the bootstrap loader stops scheduling
it directly — the orchestrator dispatches via the promise registry
instead.

This ratchet pins the contract-level shape so a future contract
edit can't silently undo it.

Sections:
  * EachPromiseUsesLifecycleDispatch — probe + ensurer are
    LifecycleProbe + LifecycleEnsurer with the correct service +
    method names.
  * EachPromiseIsBlocking — explicit ``bootstrap_blocking: true``
    survived the move (proof-of-pattern convention).
  * LegacyJobUnscheduled — ``ensure-arr-jellyfin-notifier`` is
    still REGISTERED in core.yaml (handler + label + requires)
    but has NO ``phase`` field, so ``discover_jobs_from_contracts``
    skips it from the bootstrap DAG.
  * LegacyJobStillResolvable — the registered handler resolves
    cleanly so ``run_job(name)`` and the auto-heal cycle keep
    working even though the bootstrap loader never picks it up.
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
    """Each *-jellyfin-notifier promise's probe + ensurer are
    LifecycleProbe + LifecycleEnsurer pointing at the per-service
    ServarrLifecycle methods. Reverting to the legacy ``http_json``
    probe + string ``ensured_by: ensure-arr-jellyfin-notifier`` flips
    every assertion here."""

    _EXPECTED = (
        ("sonarr-jellyfin-notifier",  "sonarr"),
        ("radarr-jellyfin-notifier",  "radarr"),
        ("lidarr-jellyfin-notifier",  "lidarr"),
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
                promise.probe.method, "probe_jellyfin_notifier",
                f"{pid}: probe.method expected probe_jellyfin_notifier",
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
                promise.ensurer.method, "ensure_jellyfin_notifier",
                f"{pid}: ensurer.method expected ensure_jellyfin_notifier",
            )


class EachPromiseIsBlocking(unittest.TestCase):
    """Explicit ``bootstrap_blocking: true`` annotation survives
    the cutover. The Jellyfin Phase 2 family proof set the
    explicit-on-cutover-proofs convention; this Phase 3 family
    follows it."""

    _IDS = (
        "sonarr-jellyfin-notifier",
        "radarr-jellyfin-notifier",
        "lidarr-jellyfin-notifier",
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


class LegacyJobUnscheduled(unittest.TestCase):
    """``ensure-arr-jellyfin-notifier`` no longer has ``phase: post``
    in core.yaml. The job is still REGISTERED so ``run_job(name)``
    (auto-heal + operator) keeps resolving it; the bootstrap loader
    skips it because ``phase`` is absent."""

    def setUp(self) -> None:
        self.contracts = _ContractFixture()
        self.entry = self.contracts.core_jobs().get(
            "ensure-arr-jellyfin-notifier",
        )
        self.assertIsNotNone(
            self.entry,
            "ensure-arr-jellyfin-notifier disappeared from core.yaml — "
            "the cutover keeps it registered, just unscheduled. "
            "Restore the entry (without phase) so run_job + auto-heal "
            "still resolve it.",
        )

    def test_no_phase_field(self) -> None:
        # ``phase: post`` would put the job back in the bootstrap
        # DAG and double up with the orchestrator's lifecycle dispatch
        # via the *-jellyfin-notifier promises.
        self.assertNotIn(
            "phase", self.entry,
            "ensure-arr-jellyfin-notifier has phase= again — the "
            "cutover removed it. Reverting means restoring "
            "phase: post + priority: 89 in core.yaml AND flipping "
            "every *-jellyfin-notifier promise back to http_json + "
            "string ensured_by.",
        )

    def test_handler_path_unchanged(self) -> None:
        # The orchestrator's LifecycleEnsurer:<svc>:ensure_jellyfin_notifier
        # is implemented in ServarrLifecycle, but the legacy job's
        # handler MUST stay so run_job + auto-heal keep working.
        self.assertEqual(
            self.entry.get("handler"),
            "media_stack.services.apps.core.job_adapters:"
            "ensure_arr_jellyfin_notifier",
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
            hasattr(mod, "ensure_arr_jellyfin_notifier"),
            "ensure_arr_jellyfin_notifier dropped from job_adapters "
            "— breaks both legacy run_job AND the orchestrator's "
            "lifecycle method (which the legacy handler is the "
            "reference implementation for).",
        )


if __name__ == "__main__":
    unittest.main()
