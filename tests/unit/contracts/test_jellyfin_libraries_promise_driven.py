"""Pin the promise-driven wiring of the jellyfin-libraries cutover
(ADR-0005 Phase 5b — the 10th and final wirer; Phase 5b.5 retired
the registration shell).

The ``jellyfin-libraries`` promise flipped from string-typed
``ensured_by: ensure-jellyfin-libraries`` to ``{type: lifecycle,
service: jellyfin, method: ensure_libraries}``. Its probe flipped
from ``http_json`` to lifecycle-typed too. The legacy
``ensure-jellyfin-libraries`` job in ``core.yaml`` was deleted in
Phase 5b.5 (the orchestrator's lifecycle dispatch is now the only
path; auto-heal + the operator dashboard route through the
orchestrator too). The legacy handler in
``services/apps/core/job_adapters.py`` is now genuinely orphan
(``JellyfinLibrariesWirer`` owns the GET/POST flow directly, no
wide-handler delegation) — flagged for Phase 5c+ deletion.

This ratchet pins the contract-level shape so a future contract
edit can't silently undo it.

Sections:
  * PromiseUsesLifecycleDispatch — probe + ensurer are
    LifecycleProbe + LifecycleEnsurer with the correct service +
    method names.
  * PromiseIsBlocking — explicit ``bootstrap_blocking: true``
    survived the move (matches the *-jellyfin-notifier convention).
  * LegacyJobRetired — ``ensure-jellyfin-libraries`` is GONE from
    core.yaml. Re-introducing the registration would re-create the
    legacy ``run_job(name)`` path the orchestrator now owns.
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

    def test_promise_ensurer_is_job(self) -> None:
        """ADR-0010 Phase 7 — promise→Job migration. Routes via
        ``run_job(jellyfin:ensure-libraries)`` instead of the
        legacy ``LifecycleEnsurer`` dispatch."""
        from media_stack.domain.services.promises import JobEnsurer
        self.assertIsInstance(
            self.promise.ensurer, JobEnsurer,
            f"{self._PROMISE_ID}: ensurer regressed from Job "
            f"dispatch (got {type(self.promise.ensurer).__name__})",
        )
        self.assertEqual(
            self.promise.ensurer.job_name,
            "jellyfin:ensure-libraries",
            f"{self._PROMISE_ID}: ensurer.job_name expected "
            "'jellyfin:ensure-libraries'",
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


class LegacyJobRetired(unittest.TestCase):
    """``ensure-jellyfin-libraries`` is GONE from core.yaml as of
    ADR-0005 Phase 5b.5. The orchestrator's lifecycle dispatch via
    the ``jellyfin-libraries`` promise is the only path; auto-heal
    and the operator dashboard route through the orchestrator too.
    Re-introducing the registration shell would re-create the
    legacy ``run_job(name)`` path."""

    def setUp(self) -> None:
        self.contracts = _ContractFixture()

    def test_legacy_registration_is_gone(self) -> None:
        self.assertNotIn(
            "ensure-jellyfin-libraries", self.contracts.core_jobs(),
            "ensure-jellyfin-libraries reappeared in core.yaml — "
            "ADR-0005 Phase 5b.5 retired the registration shell. "
            "Lifecycle dispatch via the jellyfin-libraries promise "
            "is the only path. Reverting means restoring the entry "
            "(with phase: post + priority: 86) AND flipping the "
            "promise back to http_json + string ensured_by.",
        )


if __name__ == "__main__":
    unittest.main()
