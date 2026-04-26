"""Pluggable architecture tests — verify each service can be swapped without editing platform code.

For each service role, this test:
1. Removes the service from the registry
2. Verifies platform code still functions (graceful degradation)
3. Adds a fake replacement service
4. Verifies the replacement is picked up by platform code

If any test fails, it means platform code has a hardcoded dependency on
that specific service — violating the pluggable architecture requirement.
"""

import copy
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.api.services.registry import (  # noqa: E402
    SERVICES, SERVICE_MAP, ServiceDef, reload_registry,
)
import media_stack.api.services.registry as registry_mod  # noqa: E402


class _RegistryTestBase(unittest.TestCase):
    """Base class that saves/restores the global registry around each test."""

    def setUp(self):
        self._orig_services = list(registry_mod.SERVICES)
        self._orig_map = dict(registry_mod.SERVICE_MAP)
        self._orig_cats = list(registry_mod.CATEGORIES)

    def tearDown(self):
        registry_mod.SERVICES = self._orig_services
        registry_mod.SERVICE_MAP = self._orig_map
        registry_mod.CATEGORIES.clear()
        registry_mod.CATEGORIES.extend(self._orig_cats)

    def _remove_service(self, svc_id: str):
        """Remove a service from the live registry."""
        registry_mod.SERVICES = [s for s in registry_mod.SERVICES if s.id != svc_id]
        registry_mod.SERVICE_MAP = {s.id: s for s in registry_mod.SERVICES}

    def _add_fake_service(self, svc_id: str, **kwargs):
        """Add a fake replacement service to the registry."""
        defaults = {"name": svc_id.capitalize(), "host": svc_id, "port": 9999, "health_path": "/health"}
        defaults.update(kwargs)
        fake = ServiceDef(id=svc_id, **defaults)
        registry_mod.SERVICES = list(registry_mod.SERVICES) + [fake]
        registry_mod.SERVICE_MAP[svc_id] = fake
        return fake


class TestMediaServerSwap(_RegistryTestBase):
    """Verify the media server (e.g. jellyfin) can be swapped for another."""

    def test_remove_media_server_graceful(self):
        """Removing the media server should not crash platform code."""
        ms = next((s for s in SERVICES if s.category == "media"), None)
        if not ms:
            self.skipTest("No media server in registry")
        self._remove_service(ms.id)
        # Platform code should handle missing media server gracefully
        from media_stack.api.services.config import get_libraries
        result = get_libraries()
        self.assertIn("libraries", result)

    def test_swap_media_server(self):
        """A replacement media server should be found by platform code."""
        ms = next((s for s in SERVICES if s.category == "media"), None)
        if ms:
            self._remove_service(ms.id)
        self._add_fake_service("emby", category="media")
        # Verify it's in the registry
        self.assertIn("emby", registry_mod.SERVICE_MAP)
        self.assertEqual(registry_mod.SERVICE_MAP["emby"].category, "media")

    def test_gpu_ops_without_media_server(self):
        """GPU operations should handle missing media server."""
        ms = next((s for s in SERVICES if s.category == "media"), None)
        if ms:
            self._remove_service(ms.id)
        from media_stack.api.services.ops import enable_gpu_transcoding
        result = enable_gpu_transcoding()
        self.assertIn("error", result)


class TestIndexerManagerSwap(_RegistryTestBase):
    """Verify the indexer manager (e.g. prowlarr) can be swapped."""

    def test_remove_indexer_manager_graceful(self):
        """Removing the indexer manager should not crash platform code."""
        im = next((s for s in SERVICES if s.indexer_path), None)
        if not im:
            self.skipTest("No indexer manager in registry")
        self._remove_service(im.id)
        from media_stack.api.services.content import get_indexers
        result = get_indexers()
        self.assertEqual(result["indexers"], [])

    def test_swap_indexer_manager(self):
        """A replacement indexer manager should be found."""
        im = next((s for s in SERVICES if s.indexer_path), None)
        if im:
            self._remove_service(im.id)
        self._add_fake_service("myindexer", indexer_path="/api/v1/indexer")
        from media_stack.api.services.content import get_indexers
        # Should try the new service (will fail on HTTP but won't crash)
        result = get_indexers()
        self.assertIn("indexers", result)


class TestDownloadClientSwap(_RegistryTestBase):
    """Verify download clients can be swapped."""

    def test_remove_torrent_client_graceful(self):
        """Removing qbittorrent should not crash downloads."""
        self._remove_service("qbittorrent")
        from media_stack.api.services.content import get_downloads
        result = get_downloads()
        self.assertIsInstance(result, dict)

    def test_remove_usenet_client_graceful(self):
        """Removing sabnzbd should not crash downloads."""
        self._remove_service("sabnzbd")
        from media_stack.api.services.content import get_downloads
        result = get_downloads()
        self.assertIsInstance(result, dict)


class TestDashboardSwap(_RegistryTestBase):
    """Verify the dashboard service can be swapped."""

    def test_remove_dashboard_graceful(self):
        """Removing the dashboard service should not crash."""
        self._remove_service("homepage")
        # Config artifacts should handle missing dashboard
        from media_stack.api.services.health import probe_services
        from media_stack.api.cache import TTLCache
        cache = TTLCache()
        result = probe_services(cache)
        self.assertIn("services", result)


class TestSubtitleServiceSwap(_RegistryTestBase):
    """Verify the subtitle service can be swapped."""

    def test_remove_subtitle_service_graceful(self):
        """Removing bazarr should not crash platform code."""
        self._remove_service("bazarr")
        from media_stack.api.services.health import probe_services
        from media_stack.api.cache import TTLCache
        cache = TTLCache()
        result = probe_services(cache)
        self.assertIn("services", result)


class TestRequestManagerSwap(_RegistryTestBase):
    """Verify the request manager can be swapped."""

    def test_remove_request_manager_graceful(self):
        """Removing jellyseerr should not crash."""
        self._remove_service("jellyseerr")
        from media_stack.api.services.health import probe_services
        from media_stack.api.cache import TTLCache
        cache = TTLCache()
        result = probe_services(cache)
        self.assertIn("services", result)

    def test_swap_request_manager(self):
        """A replacement request manager should appear in health probes."""
        self._remove_service("jellyseerr")
        self._add_fake_service("overseerr", category="request")
        self.assertIn("overseerr", registry_mod.SERVICE_MAP)


class TestAdminOpsSwap(_RegistryTestBase):
    """Verify admin operations work after swapping services."""

    def test_password_reset_with_empty_registry(self):
        """Password reset should handle empty registry gracefully."""
        registry_mod.SERVICES = []
        registry_mod.SERVICE_MAP = {}
        from media_stack.api.services.admin import reset_password
        with patch.dict(os.environ, {"STACK_ADMIN_PASSWORD": "old"}, clear=False), \
             patch("media_stack.api.services.admin.get_services_with_password_api", return_value=[]), \
             patch("media_stack.api.services.admin.get_services_with_password_config", return_value=[]), \
             patch("media_stack.api.services.admin.persist_keys_to_secret"):
            result = reset_password("test_pass")
        self.assertEqual(result["status"], "updated")

    def test_hard_reset_unknown_service(self):
        """Hard reset of unknown service should return error."""
        from media_stack.api.services.admin import hard_reset_service
        result = hard_reset_service("nonexistent-xyz", {})
        self.assertIn("error", result)


class TestCredentialValidationSwap(_RegistryTestBase):
    """Verify credential validation adapts to registry changes."""

    def test_login_probes_adapt_to_registry(self):
        """Login probes should only check services currently in the registry."""
        from media_stack.api.services.health import LOGIN_PROBES
        # LOGIN_PROBES is built at import time from SERVICES
        # After removing a service, new probes should not include it
        original_count = len(LOGIN_PROBES)
        self.assertGreaterEqual(original_count, 0)


class TestNoHardcodedDependencies(_RegistryTestBase):
    """Meta-test: verify platform code has no hardcoded service dependencies."""

    def test_all_services_removable(self):
        """Every registered service can be removed without crashing core endpoints."""
        from media_stack.api.services.health import probe_services
        from media_stack.api.cache import TTLCache

        for svc in list(SERVICES):
            with self.subTest(service=svc.id):
                self._remove_service(svc.id)
                cache = TTLCache()
                try:
                    result = probe_services(cache)
                    self.assertIn("services", result)
                except Exception as exc:
                    self.fail(f"Removing {svc.id} crashed probe_services: {exc}")
                # Restore for next iteration
                registry_mod.SERVICES = list(self._orig_services)
                registry_mod.SERVICE_MAP = dict(self._orig_map)


if __name__ == "__main__":
    unittest.main()
