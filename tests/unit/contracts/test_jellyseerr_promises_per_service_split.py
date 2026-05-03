"""Pin the per-service-registry shape of the Jellyseerr family.

The split moved the three Jellyseerr promises out of the monolithic
``contracts/promises/promises.yaml`` into
``contracts/services/jellyseerr.yaml::plugin.promises:``. This
ratchet asserts the post-migration shape so a future contract
edit can't silently undo it.

Sections:

  * JellyseerrPromisesLoadFromServiceYaml — the loader's source-path
    map points each Jellyseerr promise at jellyseerr.yaml.
  * LegacyMonolithDoesNotContainJellyseerrPromises — the three ids
    are GONE from the cross-cutting / legacy promises.yaml.
  * EnsurerCarriesForward — each promise's ``ensured_by``
    reference and platform list survive the move.
  * RegistryCountUnchanged — aggregate registry total is unchanged
    at 52 promises (the move is loss-free).

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


class JellyseerrPromisesLoadFromServiceYaml(unittest.TestCase):
    """Each of the three Jellyseerr promises has its source path
    pointing at ``contracts/services/jellyseerr.yaml``, NOT at the
    cross-cutting registry."""

    _EXPECTED_SOURCE_SUFFIX = Path("services") / "jellyseerr.yaml"

    def setUp(self) -> None:
        self.result = _LoadedRegistry.get()

    def assert_loaded_from_jellyseerr_yaml(self, pid: str) -> None:
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

    def test_jellyseerr_oidc_loads_from_service_yaml(self) -> None:
        self.assert_loaded_from_jellyseerr_yaml("jellyseerr-oidc")

    def test_jellyseerr_application_url_loads_from_service_yaml(
        self,
    ) -> None:
        self.assert_loaded_from_jellyseerr_yaml(
            "jellyseerr-application-url",
        )

    def test_jellyseerr_arr_servers_loads_from_service_yaml(self) -> None:
        self.assert_loaded_from_jellyseerr_yaml("jellyseerr-arr-servers")


class LegacyMonolithDoesNotContainJellyseerrPromises(unittest.TestCase):
    """The move is loss-free, not duplicate-friendly. The
    three Jellyseerr ids must NOT appear in the monolithic
    ``promises.yaml`` (or the eventual ``cross_cutting.yaml``).
    The loader's id-uniqueness check would catch a duplicate
    fatally, but pinning the YAML shape here surfaces a botched
    revert as a clear file diff rather than a parse-time error."""

    _MIGRATED_IDS = (
        "jellyseerr-oidc",
        "jellyseerr-application-url",
        "jellyseerr-arr-servers",
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

    def test_no_jellyseerr_id_in_legacy_yaml(self) -> None:
        for pid in self._MIGRATED_IDS:
            self.assertNotIn(
                f"id: {pid}", self.text,
                f"{pid!r} reappeared in promises.yaml — the split "
                f"moved it into services/jellyseerr.yaml. Either "
                f"complete the revert (restore in promises.yaml AND "
                f"remove from jellyseerr.yaml) or remove this entry.",
            )


class EnsurerCarriesForward(unittest.TestCase):
    """Each Jellyseerr promise's ``ensured_by`` reference must
    survive the YAML move. ``jellyseerr-oidc`` and
    ``jellyseerr-application-url`` both resolve to
    ``ensure-jellyseerr-oidc`` (one job ensures both probes);
    ``jellyseerr-arr-servers`` resolves to ``configure-jellyseerr``
    (the in-file job declared on the same service plugin).
    Loader normalises bare-string ``ensured_by:`` into a
    ``JobEnsurer`` with ``job_name`` — both shapes hold here."""

    _EXPECTED_FAMILY = (
        "jellyseerr-oidc",
        "jellyseerr-application-url",
        "jellyseerr-arr-servers",
    )

    _EXPECTED_JOB_NAME = {
        "jellyseerr-oidc": "ensure-jellyseerr-oidc",
        "jellyseerr-application-url": "ensure-jellyseerr-oidc",
        "jellyseerr-arr-servers": "configure-jellyseerr",
    }

    def setUp(self) -> None:
        self.by_id = _LoadedRegistry.get().by_id()

    def test_each_jellyseerr_promise_in_registry(self) -> None:
        for pid in self._EXPECTED_FAMILY:
            self.assertIn(
                pid, self.by_id,
                f"{pid!r} dropped out of the registry — the split "
                f"move-not-edit must keep all three promises live.",
            )

    def test_ensurer_job_names_preserved(self) -> None:
        for pid, expected_job in self._EXPECTED_JOB_NAME.items():
            promise = self.by_id[pid]
            actual_job = getattr(promise.ensurer, "job_name", None)
            self.assertEqual(
                actual_job, expected_job,
                f"{pid}: ensurer job_name drifted to {actual_job!r} "
                f"— the split is a YAML move, not an edit. "
                f"Expected {expected_job!r}.",
            )

    def test_platforms_preserved(self) -> None:
        for pid in self._EXPECTED_FAMILY:
            promise = self.by_id[pid]
            self.assertEqual(
                tuple(promise.platforms), ("compose", "k8s"),
                f"{pid}: platforms tuple drifted to "
                f"{tuple(promise.platforms)!r}. Expected "
                f"('compose', 'k8s'); the move must preserve it.",
            )


class RegistryCountUnchanged(unittest.TestCase):
    """The split was a move, not an edit. The aggregate registry size
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
            f"the split is a move-not-edit — count must stay constant.",
        )


if __name__ == "__main__":
    unittest.main()
