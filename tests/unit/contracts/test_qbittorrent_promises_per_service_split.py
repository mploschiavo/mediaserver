"""Pin the per-service-registry shape of the qBittorrent family.

The split moved the single qBittorrent-family promise out of the
monolithic ``contracts/promises/promises.yaml`` into
``contracts/services/qbittorrent.yaml::plugin.promises:``. This
ratchet asserts the post-migration shape so a future contract
edit can't silently undo it.

Sections:

  * QbittorrentPromisesLoadFromServiceYaml — the loader's
    source-path map points the qBit promise at qbittorrent.yaml.
  * LegacyMonolithDoesNotContainQbittorrentPromises — the id is
    GONE from the cross-cutting / legacy promises.yaml.
  * BootstrapBlockingResolvesToLoaderDefault — the promise has
    no explicit ``bootstrap_blocking:`` line, so the loader
    default (True) applies. Pinning the resolved value catches
    a future loader-default flip.
  * RegistryCountUnchanged — the split was loss-free; the
    aggregate registry still has 52 promises.

Implementation note: every assertion runs against the loaded
registry (not raw YAML), so the ratchet verifies the loader
contract end-to-end. A revert that puts the promise back in
``promises.yaml`` would flip every section here.
"""

from __future__ import annotations

import unittest
from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parents[3]


# Promise-id -> trailing source-path suffix (services/<svc>.yaml).
# Mirrors the equivalent lidarr fixture shape for the single-promise
# qBittorrent family.
_PROMISE_SOURCE_MAP: dict[str, Path] = {
    "qbittorrent-categories": Path("services") / "qbittorrent.yaml",
}


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


class QbittorrentPromisesLoadFromServiceYaml(unittest.TestCase):
    """The qBit promise has its source path pointing at
    ``contracts/services/qbittorrent.yaml``, NOT at the
    cross-cutting registry."""

    def setUp(self) -> None:
        self.result = _LoadedRegistry.get()

    def assert_loaded_from_service_yaml(
        self, pid: str, expected_suffix: Path,
    ) -> None:
        source = self.result.source_paths.get(pid)
        self.assertIsNotNone(
            source,
            f"{pid!r} missing from the registry source-path map",
        )
        # Match the trailing two segments so the test is robust to
        # absolute-path differences across dev / CI / container.
        actual_suffix = Path(*source.parts[-2:])
        self.assertEqual(
            actual_suffix, expected_suffix,
            f"{pid} loaded from {source}, expected "
            f"…/{expected_suffix}. The split moved this into the "
            f"service contract; reverting means restoring the "
            f"promise in promises.yaml.",
        )

    def test_qbittorrent_categories_loads_from_qbittorrent_yaml(self) -> None:
        self.assert_loaded_from_service_yaml(
            "qbittorrent-categories",
            _PROMISE_SOURCE_MAP["qbittorrent-categories"],
        )


class LegacyMonolithDoesNotContainQbittorrentPromises(unittest.TestCase):
    """The move is loss-free, not duplicate-friendly. The qBit id
    must NOT appear in the monolithic ``promises.yaml`` (or the
    eventual ``cross_cutting.yaml``). The loader's id-uniqueness
    check would catch a duplicate fatally, but pinning the YAML
    shape here surfaces a botched revert as a clear file diff
    rather than a parse-time error."""

    _MIGRATED_IDS = tuple(_PROMISE_SOURCE_MAP.keys())

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

    def test_no_qbittorrent_id_in_legacy_yaml(self) -> None:
        for pid in self._MIGRATED_IDS:
            self.assertNotIn(
                f"id: {pid}", self.text,
                f"{pid!r} reappeared in promises.yaml — the split "
                f"moved it into services/qbittorrent.yaml. Either "
                f"complete the revert (restore in promises.yaml AND "
                f"remove from the service yaml) or remove this entry.",
            )


class BootstrapBlockingResolvesToLoaderDefault(unittest.TestCase):
    """The qBit promise carries no explicit ``bootstrap_blocking:``
    line in YAML, so the loader default (True) applies. The
    pre-split runtime value was True for the same reason; the move
    preserves that. A future change to the loader default would
    flip the qBit promise silently — pinning the resolved value
    here makes that surface as a test failure, not a runtime
    surprise."""

    _EXPECTED_FAMILY = tuple(_PROMISE_SOURCE_MAP.keys())

    def setUp(self) -> None:
        self.by_id = _LoadedRegistry.get().by_id()

    def test_each_qbittorrent_promise_resolves_as_blocking(self) -> None:
        for pid in self._EXPECTED_FAMILY:
            promise = self.by_id.get(pid)
            self.assertIsNotNone(
                promise, f"{pid!r} dropped out of the registry",
            )
            self.assertTrue(
                promise.bootstrap_blocking,
                f"{pid}: bootstrap_blocking resolved to False — "
                f"either the YAML grew an explicit "
                f"``bootstrap_blocking: false`` line or the loader "
                f"default flipped. Both are intentional changes "
                f"that need a ratchet update.",
            )

    def test_no_unexpected_cross_family_dependencies(self) -> None:
        # The migrated qBit promise had no ``depends_on`` in
        # promises.yaml. The split is a move-not-edit; the empty
        # depends_on must survive.
        for pid in self._EXPECTED_FAMILY:
            promise = self.by_id[pid]
            self.assertEqual(
                promise.depends_on, (),
                f"{pid}: depends_on grew during the migration "
                f"(now {promise.depends_on!r}) — the split is a "
                f"move-not-edit. Either revert or update this "
                f"ratchet with intent.",
            )


class RegistryCountUnchanged(unittest.TestCase):
    """The split was a move, not an edit. The aggregate registry size
    is unchanged at 52 promises. A drop = a botched move; an
    increase = an accidental duplicate the loader didn't catch
    (which would be a separate bug)."""

    _EXPECTED_TOTAL = 58  # +1 ADR-0013 Phase 2: qbittorrent-credentials-synced (closes the legacy runner.run qBit-login bespoke path)  # +5 ADR-0005 Phase 5c.1 wide ``*-api-key-discoverable``

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
