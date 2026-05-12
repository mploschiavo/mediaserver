"""Pin the per-service-registry shape of the four gateway promises.

The gateway-level promises (HTTPS listener, Jellyfin subdomain
route, /app/<svc>/ path-prefix route, HTTP→HTTPS redirect) live
in ``contracts/services/envoy.yaml::plugin.promises:``, not in
the cross-cutting ``contracts/promises/promises.yaml``. The
gateway is implemented by Envoy, so co-locating the probes with
the service they prove keeps "what does the gateway promise?"
answerable by reading one file.

Sections:

  * GatewayPromisesLoadFromEnvoyYaml — the loader's source-path
    map points each gateway promise at envoy.yaml.
  * LegacyMonolithDoesNotContainGatewayPromises — the four ids
    are GONE from the cross-cutting / legacy promises.yaml.
  * BootstrapBlockingResolvesToLoaderDefault — none of the four
    promises carry an explicit ``bootstrap_blocking:`` line, so
    the loader default (True) applies. Pinning the resolved
    value catches a future loader-default flip.
  * RegistryCountUnchanged — the split was loss-free; the
    aggregate registry still has 52 promises.

Implementation note: every assertion runs against the loaded
registry (not raw YAML), so the ratchet verifies the loader
contract end-to-end. A revert that puts the promises back in
``promises.yaml`` would flip every section here.
"""

from __future__ import annotations

import unittest
from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parents[3]


_PROMISE_SOURCE_MAP: dict[str, Path] = {
    "gateway-https-listener-up":      Path("services") / "envoy.yaml",
    "gateway-jellyfin-route":         Path("services") / "envoy.yaml",
    "gateway-app-prefix-route":       Path("services") / "envoy.yaml",
    "gateway-http-redirects-to-https": Path("services") / "envoy.yaml",
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


class GatewayPromisesLoadFromEnvoyYaml(unittest.TestCase):
    """Each of the four gateway promises has its source path
    pointing at ``contracts/services/envoy.yaml``, NOT at the
    cross-cutting registry."""

    _EXPECTED_SOURCE_SUFFIX = Path("services") / "envoy.yaml"

    def setUp(self) -> None:
        self.result = _LoadedRegistry.get()

    def assert_loaded_from_envoy_yaml(self, pid: str) -> None:
        source = self.result.source_paths.get(pid)
        self.assertIsNotNone(
            source,
            f"{pid!r} missing from the registry source-path map",
        )
        actual_suffix = Path(*source.parts[-2:])
        self.assertEqual(
            actual_suffix, self._EXPECTED_SOURCE_SUFFIX,
            f"{pid} loaded from {source}, expected "
            f"…/{self._EXPECTED_SOURCE_SUFFIX}. The promise lives "
            f"in the service contract; reverting means restoring "
            f"it in promises.yaml.",
        )

    def test_gateway_https_listener_up_loads_from_envoy_yaml(self) -> None:
        self.assert_loaded_from_envoy_yaml("gateway-https-listener-up")

    def test_gateway_jellyfin_route_loads_from_envoy_yaml(self) -> None:
        self.assert_loaded_from_envoy_yaml("gateway-jellyfin-route")

    def test_gateway_app_prefix_route_loads_from_envoy_yaml(self) -> None:
        self.assert_loaded_from_envoy_yaml("gateway-app-prefix-route")

    def test_gateway_http_redirects_to_https_loads_from_envoy_yaml(
        self,
    ) -> None:
        self.assert_loaded_from_envoy_yaml(
            "gateway-http-redirects-to-https",
        )


class LegacyMonolithDoesNotContainGatewayPromises(unittest.TestCase):
    """The split is loss-free, not duplicate-friendly. The four
    gateway ids must NOT appear in the monolithic
    ``promises.yaml`` (or the eventual ``cross_cutting.yaml``).
    The loader's id-uniqueness check would catch a duplicate
    fatally, but pinning the YAML shape here surfaces a botched
    revert as a clear file diff rather than a parse-time error."""

    _MIGRATED_IDS = tuple(_PROMISE_SOURCE_MAP.keys())

    def setUp(self) -> None:
        promises_yaml = (
            _REPO_ROOT / "contracts" / "promises" / "promises.yaml"
        )
        if promises_yaml.is_file():
            self.text = promises_yaml.read_text(encoding="utf-8")
        else:
            self.text = ""

    def test_no_gateway_id_in_legacy_yaml(self) -> None:
        for pid in self._MIGRATED_IDS:
            self.assertNotIn(
                f"id: {pid}", self.text,
                f"{pid!r} reappeared in promises.yaml — its "
                f"canonical home is services/envoy.yaml. Either "
                f"complete the revert (restore in promises.yaml AND "
                f"remove from envoy.yaml) or remove this entry.",
            )


class BootstrapBlockingResolvesToLoaderDefault(unittest.TestCase):
    """None of the four gateway promises carry an explicit
    ``bootstrap_blocking:`` line in YAML, so the loader default
    (True) applies. The pre-split runtime value was True for the
    same reason; the move preserves that. A future change to the
    loader default would flip every gateway promise silently —
    pinning the resolved value here makes that surface as a test
    failure, not a runtime surprise."""

    _EXPECTED_FAMILY = tuple(_PROMISE_SOURCE_MAP.keys())

    def setUp(self) -> None:
        self.by_id = _LoadedRegistry.get().by_id()

    def test_each_gateway_promise_resolves_as_blocking(self) -> None:
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
    """The split was a move, not an edit. The aggregate registry
    size is unchanged at 52 promises. A drop = a botched move; an
    increase = an accidental duplicate the loader didn't catch
    (which would be a separate bug)."""

    _EXPECTED_TOTAL = 64  # +5 ADR-0005 Phase 5c.1 wide ``*-api-key-discoverable``

    def test_total_count_unchanged_post_migration(self) -> None:
        result = _LoadedRegistry.get()
        self.assertEqual(
            len(result.promises), self._EXPECTED_TOTAL,
            f"registry total drifted to {len(result.promises)} "
            f"(expected {self._EXPECTED_TOTAL}). The split is a "
            f"move-not-edit — count must stay constant.",
        )


if __name__ == "__main__":
    unittest.main()
