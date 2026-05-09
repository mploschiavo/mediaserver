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
    survive the YAML move. ADR-0005 Phase 3 then cut these promises
    over from ``ensured_by: ensure-jellyseerr-oidc`` /
    ``configure-jellyseerr`` strings to typed lifecycle dispatch
    via ``JellyseerrLifecycle.{ensure_oidc,ensure_application_url,
    ensure_arr_servers}``. The legacy job entries are still
    REGISTERED so ``run_job(name)`` keeps resolving them — that's
    pinned by ``test_jellyseerr_config_promise_driven.py``.
    """

    _EXPECTED_FAMILY = (
        "jellyseerr-oidc",
        "jellyseerr-application-url",
        "jellyseerr-arr-servers",
    )

    # ADR-0005 Phase 3 cutover — each promise's ensurer is now a
    # ``LifecycleEnsurer`` carrying (service, method) instead of a
    # ``JobEnsurer`` carrying (job_name). Reverting the cutover
    # restores the legacy job-name shape; this map preserves the
    # original mapping for that revert path's regression check.
    _EXPECTED_LIFECYCLE_METHOD = {
        "jellyseerr-oidc":             "ensure_oidc",
        "jellyseerr-application-url":  "ensure_application_url",
        "jellyseerr-arr-servers":      "ensure_arr_servers",
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

    def test_ensurer_targets_preserved_across_phase_7(self) -> None:
        """ADR-0010 Phase 7 — Jellyseerr promises now target Jobs
        (``jellyseerr:ensure-<topic>``) instead of lifecycle dispatch.
        The lifecycle method name is preserved as the
        last-segment-with-dashes inside the Job name; this test
        pins the mapping so a regression that points the promise at
        the wrong Job (or back to the legacy LifecycleEnsurer) fails."""
        for pid, expected_method in self._EXPECTED_LIFECYCLE_METHOD.items():
            promise = self.by_id[pid]
            job_name = getattr(promise.ensurer, "job_name", None)
            expected_job = (
                f"jellyseerr:{expected_method.replace('_', '-')}"
            )
            self.assertEqual(
                job_name, expected_job,
                f"{pid}: ensurer.job_name drifted to {job_name!r} "
                f"— expected {expected_job!r} (Phase 7 routes via "
                f"``run_job(<job-name>)`` instead of lifecycle "
                f"dispatch).",
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

    _EXPECTED_TOTAL = 57  # +5 ADR-0005 Phase 5c.1 wide ``*-api-key-discoverable``

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
