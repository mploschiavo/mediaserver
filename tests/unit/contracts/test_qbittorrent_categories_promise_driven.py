"""Pin the promise-driven wiring of the qBittorrent-categories
cutover (ADR-0005 Phase 3).

The ``qbittorrent-categories`` promise flipped from string-typed
``ensured_by: ensure-qbittorrent-categories`` + ``http_text`` probe
to ``{type: lifecycle, service: qbittorrent, method: …}`` for both
probe and ensurer. The legacy ``ensure-qbittorrent-categories`` job
in ``core.yaml`` lost its ``phase: post`` + ``priority: 84`` lines
so the bootstrap loader stops scheduling it directly — the
orchestrator dispatches via the promise registry instead.

This ratchet pins the contract-level shape so a future contract
edit can't silently undo it.

Sections:
  * PromiseUsesLifecycleDispatch — probe + ensurer are
    LifecycleProbe + LifecycleEnsurer with the correct service +
    method names.
  * PromiseIsBlocking — explicit ``bootstrap_blocking: true``
    survived the move (cutover-proof convention).
  * LegacyJobRetired — ``ensure-qbittorrent-categories`` is GONE
    from core.yaml as of ADR-0005 Phase 5b.5; the orchestrator's
    lifecycle dispatch is the only path.
  * WirerSurfacePinned — the ``CategoriesWirer`` class is the unit
    of cutover; its singleton attachment to ``QbittorrentLifecycle``
    is pinned so a refactor that "inlines the wirer back" or "swaps
    to a per-instance attribute" surfaces here.
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


_PROMISE_ID = "qbittorrent-categories"
_LEGACY_JOB_NAME = "ensure-qbittorrent-categories"


class PromiseUsesLifecycleDispatch(unittest.TestCase):
    """The qbittorrent-categories promise's probe + ensurer are
    LifecycleProbe + LifecycleEnsurer pointing at the
    ``QbittorrentLifecycle`` methods. Reverting to the legacy
    ``http_text`` probe + string ``ensured_by`` flips both
    assertions here."""

    def setUp(self) -> None:
        self.by_id = _LoadedRegistry.get().by_id()
        self.promise = self.by_id.get(_PROMISE_ID)
        self.assertIsNotNone(
            self.promise,
            f"{_PROMISE_ID!r} dropped out of registry — the cutover "
            "moved it to lifecycle dispatch but the promise itself "
            "must stay registered.",
        )

    def test_probe_is_lifecycle(self) -> None:
        from media_stack.domain.services.promises import LifecycleProbe
        self.assertIsInstance(
            self.promise.probe, LifecycleProbe,
            f"{_PROMISE_ID}: probe regressed from lifecycle dispatch "
            f"(got {type(self.promise.probe).__name__}). The cutover "
            "uses ``QbittorrentLifecycle.probe_categories``.",
        )
        self.assertEqual(self.promise.probe.service, "qbittorrent")
        self.assertEqual(self.promise.probe.method, "probe_categories")

    def test_ensurer_is_lifecycle(self) -> None:
        from media_stack.domain.services.promises import LifecycleEnsurer
        self.assertIsInstance(
            self.promise.ensurer, LifecycleEnsurer,
            f"{_PROMISE_ID}: ensurer regressed from lifecycle dispatch "
            f"(got {type(self.promise.ensurer).__name__}). The cutover "
            "uses ``QbittorrentLifecycle.ensure_categories``.",
        )
        self.assertEqual(self.promise.ensurer.service, "qbittorrent")
        self.assertEqual(self.promise.ensurer.method, "ensure_categories")


class PromiseIsBlocking(unittest.TestCase):
    """Explicit ``bootstrap_blocking: true`` annotation survives
    the cutover. Cutover-proof convention — explicit-on-cutover-proofs
    even though the loader default is True. A future loader-default
    flip would silently demote the promise without this pin."""

    def setUp(self) -> None:
        self.by_id = _LoadedRegistry.get().by_id()

    def test_promise_is_blocking(self) -> None:
        promise = self.by_id[_PROMISE_ID]
        self.assertTrue(
            promise.bootstrap_blocking,
            f"{_PROMISE_ID}: bootstrap_blocking flipped to False — "
            "the cutover proof requires explicit-True so "
            "orchestrator-driven bootstrap waits for it.",
        )


class LegacyJobRetired(unittest.TestCase):
    """``ensure-qbittorrent-categories`` is GONE from core.yaml as
    of ADR-0005 Phase 5b.5. The orchestrator's lifecycle dispatch
    via ``QbittorrentLifecycle.ensure_categories`` is the only
    path; auto-heal and the operator dashboard route through the
    orchestrator too."""

    def setUp(self) -> None:
        self.contracts = _ContractFixture()

    def test_legacy_registration_is_gone(self) -> None:
        self.assertNotIn(
            _LEGACY_JOB_NAME, self.contracts.core_jobs(),
            f"{_LEGACY_JOB_NAME} reappeared in core.yaml — "
            "ADR-0005 Phase 5b.5 retired the registration shell. "
            "Reverting means restoring the entry (with phase: post "
            "+ priority: 84) AND flipping the qbittorrent-categories "
            "promise back to http_text + string ensured_by.",
        )


class WirerSurfacePinned(unittest.TestCase):
    """The ``CategoriesWirer`` class is the unit of cutover. Pinning
    the singleton attachment to ``QbittorrentLifecycle`` (module-level
    ``_CATEGORIES_WIRER`` per the recipe) catches a refactor that
    "inlines the wirer back into lifecycle.py" or "swaps to a per-
    instance attribute" — both regress the OO discipline the recipe
    enforces."""

    def test_module_level_singleton_present(self) -> None:
        from media_stack.adapters.qbittorrent import lifecycle as lc_mod
        from media_stack.adapters.qbittorrent.categories_wiring import (
            CategoriesWirer,
        )
        self.assertTrue(
            hasattr(lc_mod, "_CATEGORIES_WIRER"),
            "lifecycle module lost its ``_CATEGORIES_WIRER`` singleton "
            "— the recipe attaches the wirer at module scope so the "
            "lifecycle methods are 2-line delegators, not 30-line "
            "reimplementations.",
        )
        self.assertIsInstance(
            getattr(lc_mod, "_CATEGORIES_WIRER"), CategoriesWirer,
        )

    def test_lifecycle_methods_delegate(self) -> None:
        from media_stack.adapters.qbittorrent.lifecycle import (
            QbittorrentLifecycle,
        )
        # Both methods exist on the public surface — pin against
        # accidental rename.
        self.assertTrue(callable(
            getattr(QbittorrentLifecycle, "probe_categories", None),
        ))
        self.assertTrue(callable(
            getattr(QbittorrentLifecycle, "ensure_categories", None),
        ))


if __name__ == "__main__":
    unittest.main()
