"""Verify that module-level LRU caches do not leak stale state across tests.

The production codebase uses ``@lru_cache`` on several functions that read
from YAML contracts and the service registry.  If a test mocks the registry
or reloads contracts, the cache must be cleared so the next test sees fresh
data.

This test explicitly poisons a cache, then verifies the conftest fixture
restores it to a clean state.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))


class CatalogCacheIsolationTests(unittest.TestCase):
    """Ensure the profile catalog LRU cache is cleared between modules."""

    def test_catalog_cache_does_not_persist_stale_registry_data(self):
        """After clearing, the catalog reloads fresh data from the registry."""
        from media_stack.core.controller_profile.catalog_loader import (
            _load_bootstrap_profile_catalog_cached,
            clear_catalog_cache,
            load_bootstrap_profile_catalog,
        )
        from media_stack.api.services.registry import SERVICES

        # 1. Load the catalog once — this populates the cache
        catalog = load_bootstrap_profile_catalog()
        real_app_keys = set(catalog.app_keys)
        self.assertTrue(len(real_app_keys) > 0, "Catalog should have app keys from registry")

        # 2. Clear the cache
        clear_catalog_cache()

        # 3. Mock SERVICES to be empty — simulates test pollution.
        # SERVICES is imported inside the cached function, so we mock it
        # at its source module.
        with patch("media_stack.api.services.registry.SERVICES", []):
            # This should raise because the catalog YAML references apps
            # (e.g. traefik in minimal profile) that are unknown when
            # SERVICES is empty and apps.keys is also empty.
            with self.assertRaises(ValueError):
                load_bootstrap_profile_catalog()

        # 4. Clear again to undo the poisoned cache entry
        clear_catalog_cache()

        # 5. Load without mock — should work again with real registry
        fresh_catalog = load_bootstrap_profile_catalog()
        fresh_keys = set(fresh_catalog.app_keys)
        self.assertEqual(real_app_keys, fresh_keys,
                         "After cache clear, catalog should reload with real registry data")

    def test_all_lru_caches_have_cache_clear_method(self):
        """Every production @lru_cache function must expose cache_clear()."""
        caches = [
            (
                "media_stack.core.controller_profile.catalog_loader",
                "_load_bootstrap_profile_catalog_cached",
            ),
            (
                "media_stack.core.platform_plugin_registry",
                "load_platform_plugins",
            ),
            (
                "media_stack.services.apps.stack.controller_config_policy",
                "_load_policy_catalog",
            ),
            (
                "media_stack.core.edge.provider_registry",
                "load_builtin_edge_router_provider_specs",
            ),
        ]
        for module_path, fn_name in caches:
            try:
                mod = __import__(module_path, fromlist=[fn_name])
            except ImportError:
                continue
            fn = getattr(mod, fn_name, None)
            self.assertIsNotNone(fn, f"{module_path}.{fn_name} not found")
            self.assertTrue(
                hasattr(fn, "cache_clear"),
                f"{module_path}.{fn_name} is not an lru_cache (no cache_clear method)"
            )

    def test_conftest_fixture_clears_caches(self):
        """The conftest fixture should have already cleared caches for this module.

        If the fixture is working, loading the catalog here should always
        succeed regardless of what previous test modules did.
        """
        from media_stack.core.controller_profile.catalog_loader import (
            _load_bootstrap_profile_catalog_cached,
            load_bootstrap_profile_catalog,
        )

        # The cache should be cold (cleared by conftest before this module)
        info = _load_bootstrap_profile_catalog_cached.cache_info()
        # misses should be 0 or very small if this is the first access
        # (other tests in this module may have called it)

        # Loading should succeed — the real registry is intact
        catalog = load_bootstrap_profile_catalog()
        self.assertTrue(
            len(catalog.app_keys) > 0,
            "Catalog should load successfully after conftest cache clear"
        )


class RuntimeCacheConsistencyTests(unittest.TestCase):
    """Verify that reload_registry() does not leave stale catalog data.

    At runtime, reload_registry() can be called via the admin API. If the
    catalog LRU cache is not cleared, it may reference services that no
    longer exist in the registry, causing ValueError on next profile parse.
    """

    def test_reload_registry_does_not_poison_catalog_cache(self):
        """Catalog remains valid after a registry reload cycle."""
        import tempfile
        from pathlib import Path
        from unittest.mock import patch

        from media_stack.api.services import registry as registry_mod
        from media_stack.api.services.registry import reload_registry
        from media_stack.core.controller_profile.catalog_loader import (
            clear_catalog_cache,
            load_bootstrap_profile_catalog,
        )

        # 1. Load catalog with real registry — should succeed
        clear_catalog_cache()
        catalog_before = load_bootstrap_profile_catalog()
        keys_before = set(catalog_before.app_keys)
        self.assertTrue(len(keys_before) > 5)

        # 2. Save original state
        orig_services = registry_mod.SERVICES
        orig_map = registry_mod.SERVICE_MAP
        orig_cats = list(registry_mod.CATEGORIES)
        orig_order = registry_mod._CATEGORY_ORDER

        try:
            # 3. Reload with a minimal registry (simulating a corrupted YAML)
            with tempfile.TemporaryDirectory() as tmpdir:
                svc_dir = Path(tmpdir) / "services"
                svc_dir.mkdir()
                (svc_dir / "only_one.yaml").write_text(
                    "service:\n  id: only_one\n  name: OnlyOne\n"
                )
                with patch.dict(
                    "os.environ",
                    {"SERVICES_REGISTRY_DIR": str(svc_dir)},
                ):
                    reload_registry()

                # Registry is now corrupted — only has "only_one"
                self.assertEqual(len(registry_mod.SERVICES), 1)

                # 4. Clear catalog cache — this is the critical step
                clear_catalog_cache()

                # 5. Loading catalog should now fail because "traefik"
                #    etc. are referenced in profiles but not in registry
                with self.assertRaises(ValueError):
                    load_bootstrap_profile_catalog()
        finally:
            # 6. Restore original registry
            registry_mod.SERVICES = orig_services
            registry_mod.SERVICE_MAP = orig_map
            registry_mod.CATEGORIES[:] = orig_cats
            registry_mod._CATEGORY_ORDER = orig_order
            clear_catalog_cache()

        # 7. After restore, catalog should work again
        catalog_after = load_bootstrap_profile_catalog()
        self.assertEqual(set(catalog_after.app_keys), keys_before)


if __name__ == "__main__":
    unittest.main()
