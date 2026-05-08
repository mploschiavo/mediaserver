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
  * LegacyJobRetired — ``ensure-arr-jellyfin-notifier`` is GONE
    from core.yaml as of ADR-0005 Phase 5b.5; the orchestrator's
    lifecycle dispatch is the only path.
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


class LegacyJobRetired(unittest.TestCase):
    """``ensure-arr-jellyfin-notifier`` is GONE from core.yaml as of
    ADR-0005 Phase 5b.5. The orchestrator's lifecycle dispatch via
    the three *-jellyfin-notifier promises is the only path; auto-
    heal and the operator dashboard route through the orchestrator
    too."""

    def setUp(self) -> None:
        self.contracts = _ContractFixture()

    def test_legacy_registration_is_gone(self) -> None:
        self.assertNotIn(
            "ensure-arr-jellyfin-notifier", self.contracts.core_jobs(),
            "ensure-arr-jellyfin-notifier reappeared in core.yaml — "
            "ADR-0005 Phase 5b.5 retired the registration shell. "
            "Reverting means restoring the entry (with phase: post + "
            "priority: 89) AND flipping every *-jellyfin-notifier "
            "promise back to http_json + string ensured_by.",
        )


if __name__ == "__main__":
    unittest.main()
