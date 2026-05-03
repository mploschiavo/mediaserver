"""Pin the ADR-0006 Phase 2 Jellyfin family migration.

Phase 2 moved the three Jellyfin promises out of the monolithic
``contracts/promises/promises.yaml`` into
``contracts/services/jellyfin.yaml::plugin.promises:``. This
ratchet asserts the post-migration shape so a future contract
edit can't silently undo it.

Sections:

  * JellyfinPromisesLoadFromServiceYaml — the loader's source-path
    map points each Jellyfin promise at jellyfin.yaml.
  * LegacyMonolithDoesNotContainJellyfinPromises — the three ids
    are GONE from the cross-cutting / legacy promises.yaml.
  * BootstrapBlockingCarriesForward — ADR-0005 Phase 2's
    ``bootstrap_blocking: true`` annotation survived the move.
  * RegistryCountUnchanged — Phase 2 was loss-free; the aggregate
    registry still has 52 promises.

Implementation note: every assertion runs against the loaded
registry (not raw YAML), so the ratchet verifies the loader
contract end-to-end. A Phase-2 revert that puts the promises
back in ``promises.yaml`` would flip every section here.
"""

from __future__ import annotations

import unittest
from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parents[3]


class _LoadedRegistry:
    """One-shot fixture wrapper around the real
    ``PromiseRegistryLoader``. Caches the aggregate result so each
    test class doesn't reload the YAML tree from disk."""

    _cache = None

    @classmethod
    def get(cls):
        if cls._cache is None:
            from media_stack.infrastructure.promises.registry import (
                PromiseRegistryLoader,
            )
            cls._cache = PromiseRegistryLoader().aggregate()
        return cls._cache


class JellyfinPromisesLoadFromServiceYaml(unittest.TestCase):
    """Each of the three Jellyfin promises has its source path
    pointing at ``contracts/services/jellyfin.yaml``, NOT at the
    cross-cutting registry."""

    _EXPECTED_SOURCE_SUFFIX = Path("services") / "jellyfin.yaml"

    def setUp(self) -> None:
        self.result = _LoadedRegistry.get()

    def assert_loaded_from_jellyfin_yaml(self, pid: str) -> None:
        source = self.result.source_paths.get(pid)
        self.assertIsNotNone(
            source,
            f"{pid!r} missing from the registry source-path map",
        )
        # Match the trailing two segments so the test is robust to
        # absolute-path differences across dev / CI / container.
        actual_suffix = Path(*source.parts[-2:])
        self.assertEqual(
            actual_suffix, self._EXPECTED_SOURCE_SUFFIX,
            f"{pid} loaded from {source}, expected "
            f"…/{self._EXPECTED_SOURCE_SUFFIX}. Phase 2 moved this "
            f"into the service contract; reverting means restoring "
            f"the promise in promises.yaml.",
        )

    def test_jellyfin_running_loads_from_service_yaml(self) -> None:
        self.assert_loaded_from_jellyfin_yaml("jellyfin-running")

    def test_jellyfin_api_key_discoverable_loads_from_service_yaml(
        self,
    ) -> None:
        self.assert_loaded_from_jellyfin_yaml(
            "jellyfin-api-key-discoverable",
        )

    def test_jellyfin_libraries_loads_from_service_yaml(self) -> None:
        self.assert_loaded_from_jellyfin_yaml("jellyfin-libraries")


class LegacyMonolithDoesNotContainJellyfinPromises(unittest.TestCase):
    """The Phase-2 move is loss-free, not duplicate-friendly. The
    three Jellyfin ids must NOT appear in the monolithic
    ``promises.yaml`` (or the eventual ``cross_cutting.yaml``).
    The loader's id-uniqueness check would catch a duplicate
    fatally, but pinning the YAML shape here surfaces a botched
    revert as a clear file diff rather than a parse-time error."""

    _MIGRATED_IDS = (
        "jellyfin-running",
        "jellyfin-api-key-discoverable",
        "jellyfin-libraries",
    )

    def setUp(self) -> None:
        # Read the legacy file directly so the assertion is on
        # YAML shape, not on the loader's already-aggregated view.
        promises_yaml = (
            _REPO_ROOT / "contracts" / "promises" / "promises.yaml"
        )
        if promises_yaml.is_file():
            self.text = promises_yaml.read_text(encoding="utf-8")
        else:
            self.text = ""

    def test_no_jellyfin_id_in_legacy_yaml(self) -> None:
        for pid in self._MIGRATED_IDS:
            self.assertNotIn(
                f"id: {pid}", self.text,
                f"{pid!r} reappeared in promises.yaml — Phase 2 "
                f"moved it into services/jellyfin.yaml. Either "
                f"complete the revert (restore in promises.yaml AND "
                f"remove from jellyfin.yaml) or remove this entry.",
            )


class BootstrapBlockingCarriesForward(unittest.TestCase):
    """ADR-0005 Phase 2 set ``bootstrap_blocking: true`` on each
    Jellyfin promise. Phase 2 of ADR-0006 moves the YAML location;
    the annotation must survive."""

    _EXPECTED_FAMILY = (
        "jellyfin-running",
        "jellyfin-api-key-discoverable",
        "jellyfin-libraries",
    )

    def setUp(self) -> None:
        self.by_id = _LoadedRegistry.get().by_id()

    def test_each_jellyfin_promise_is_blocking(self) -> None:
        for pid in self._EXPECTED_FAMILY:
            promise = self.by_id.get(pid)
            self.assertIsNotNone(
                promise, f"{pid!r} dropped out of the registry",
            )
            self.assertTrue(
                promise.bootstrap_blocking,
                f"{pid}: bootstrap_blocking flipped to False — "
                f"ADR-0005 Phase 2 explicitly pins this True.",
            )

    def test_dependency_chain_preserved(self) -> None:
        # ``jellyfin-api-key-discoverable`` depends on
        # ``jellyfin-running``. Cross-file depends_on resolution is
        # the loader's job; this test pins it post-migration.
        api_key = self.by_id["jellyfin-api-key-discoverable"]
        self.assertEqual(api_key.depends_on, ("jellyfin-running",))


class RegistryCountUnchanged(unittest.TestCase):
    """Phase 2 was a move, not an edit. The aggregate registry size
    is unchanged at 52 promises. A drop = a botched move; an
    increase = an accidental duplicate the loader didn't catch
    (which would be a separate bug)."""

    _EXPECTED_TOTAL = 52

    def test_total_count_unchanged_post_migration(self) -> None:
        result = _LoadedRegistry.get()
        self.assertEqual(
            len(result.promises), self._EXPECTED_TOTAL,
            f"registry total drifted to {len(result.promises)} "
            f"(expected {self._EXPECTED_TOTAL}). "
            f"Phase 2 is a move-not-edit — count must stay constant.",
        )


if __name__ == "__main__":
    unittest.main()
