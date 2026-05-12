"""Pin the per-service-registry shape of the Bazarr family.

The split moved the five Bazarr promises out of the monolithic
``contracts/promises/promises.yaml`` into
``contracts/services/bazarr.yaml::plugin.promises:``. This
ratchet asserts the post-migration shape so a future contract
edit can't silently undo it.

Sections:

  * BazarrPromisesLoadFromServiceYaml — the loader's source-path
    map points each Bazarr promise at bazarr.yaml.
  * LegacyMonolithDoesNotContainBazarrPromises — the five ids
    are GONE from the cross-cutting / legacy promises.yaml.
  * BootstrapBlockingCarriesForward — the loader's default
    ``bootstrap_blocking: true`` survives the move (none of the
    Bazarr entries override it, so each must come back True).
  * RegistryCountUnchanged — the split was loss-free; the aggregate
    registry still has 52 promises.

Implementation note: every assertion runs against the loaded
registry (not raw YAML), so the ratchet verifies the loader
contract end-to-end. A revert that puts the promises
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


class BazarrPromisesLoadFromServiceYaml(unittest.TestCase):
    """Each of the five Bazarr promises has its source path
    pointing at ``contracts/services/bazarr.yaml``, NOT at the
    cross-cutting registry."""

    _EXPECTED_SOURCE_SUFFIX = Path("services") / "bazarr.yaml"

    def setUp(self) -> None:
        self.result = _LoadedRegistry.get()

    def assert_loaded_from_bazarr_yaml(self, pid: str) -> None:
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
            f"…/{self._EXPECTED_SOURCE_SUFFIX}. The split moved this "
            f"into the service contract; reverting means restoring "
            f"the promise in promises.yaml.",
        )

    def test_bazarr_language_profile_loads_from_service_yaml(self) -> None:
        self.assert_loaded_from_bazarr_yaml("bazarr-language-profile")

    def test_bazarr_default_profile_toggles_loads_from_service_yaml(
        self,
    ) -> None:
        self.assert_loaded_from_bazarr_yaml(
            "bazarr-default-profile-toggles",
        )

    def test_bazarr_providers_loads_from_service_yaml(self) -> None:
        self.assert_loaded_from_bazarr_yaml("bazarr-providers")

    def test_bazarr_arr_integration_loads_from_service_yaml(self) -> None:
        self.assert_loaded_from_bazarr_yaml("bazarr-arr-integration")

    def test_bazarr_jellyfin_plugin_config_loads_from_service_yaml(
        self,
    ) -> None:
        self.assert_loaded_from_bazarr_yaml(
            "bazarr-jellyfin-plugin-config",
        )


class LegacyMonolithDoesNotContainBazarrPromises(unittest.TestCase):
    """The move is loss-free, not duplicate-friendly. The
    five Bazarr ids must NOT appear in the monolithic
    ``promises.yaml`` (or the eventual ``cross_cutting.yaml``).
    The loader's id-uniqueness check would catch a duplicate
    fatally, but pinning the YAML shape here surfaces a botched
    revert as a clear file diff rather than a parse-time error."""

    _MIGRATED_IDS = (
        "bazarr-language-profile",
        "bazarr-default-profile-toggles",
        "bazarr-providers",
        "bazarr-arr-integration",
        "bazarr-jellyfin-plugin-config",
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

    def test_no_bazarr_id_in_legacy_yaml(self) -> None:
        for pid in self._MIGRATED_IDS:
            self.assertNotIn(
                f"id: {pid}", self.text,
                f"{pid!r} reappeared in promises.yaml — the split "
                f"moved it into services/bazarr.yaml. Either "
                f"complete the revert (restore in promises.yaml AND "
                f"remove from bazarr.yaml) or remove this entry.",
            )


class BootstrapBlockingCarriesForward(unittest.TestCase):
    """The loader defaults ``bootstrap_blocking`` to True when the
    field is omitted (see ``PromiseEntryParser`` in
    ``infrastructure/promises/registry.py``). None of the five
    Bazarr promises override it, so each must surface as
    ``bootstrap_blocking=True`` post-migration. the per-service split
    relocates the YAML; the loader's default-handling must keep
    the same observable shape.

    The five Bazarr promises also do NOT declare a ``depends_on``
    edge — neither cross-family (e.g. on ``sonarr-*``) nor
    intra-family — so the loader must surface an empty
    ``depends_on`` tuple for each."""

    _EXPECTED_FAMILY = (
        "bazarr-language-profile",
        "bazarr-default-profile-toggles",
        "bazarr-providers",
        "bazarr-arr-integration",
        "bazarr-jellyfin-plugin-config",
    )

    def setUp(self) -> None:
        self.by_id = _LoadedRegistry.get().by_id()

    def test_each_bazarr_promise_is_blocking(self) -> None:
        for pid in self._EXPECTED_FAMILY:
            promise = self.by_id.get(pid)
            self.assertIsNotNone(
                promise, f"{pid!r} dropped out of the registry",
            )
            self.assertTrue(
                promise.bootstrap_blocking,
                f"{pid}: bootstrap_blocking flipped to False — "
                f"the Bazarr promises rely on the loader default "
                f"(True) and none should declare otherwise.",
            )

    def test_no_cross_family_depends_on(self) -> None:
        # The Bazarr family is dependency-free in the YAML —
        # ``ensure-bazarr-language-profile`` runs the whole pass
        # in one job. Pin that absence so a future cross-family
        # edge (e.g. depends_on: [sonarr-has-indexers]) shows up
        # here rather than at runtime.
        for pid in self._EXPECTED_FAMILY:
            promise = self.by_id[pid]
            self.assertEqual(
                promise.depends_on, (),
                f"{pid}: depends_on is {promise.depends_on!r}, "
                f"expected (). The Bazarr family is dependency-"
                f"free at the registry level.",
            )


class RegistryCountUnchanged(unittest.TestCase):
    """The split was a move, not an edit. The aggregate registry
    size is 57 promises (52 from the original split + 5 added by
    ADR-0005 Phase 5c.1 wide: one ``*-api-key-discoverable`` per
    service for sonarr / radarr / lidarr / readarr / jellyseerr).
    A drop = a botched move; an increase = an accidental duplicate
    the loader didn't catch (which would be a separate bug)."""

    _EXPECTED_TOTAL = 59

    def test_total_count_unchanged_post_migration(self) -> None:
        result = _LoadedRegistry.get()
        self.assertEqual(
            len(result.promises), self._EXPECTED_TOTAL,
            f"registry total drifted to {len(result.promises)} "
            f"(expected {self._EXPECTED_TOTAL}). "
            f"the split is a move-not-edit — count must stay constant.",
        )


if __name__ == "__main__":
    unittest.main()
