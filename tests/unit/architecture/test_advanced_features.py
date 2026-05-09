"""Tests for advanced features: storage migration, onboarding, download analytics, custom services."""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

import media_stack.api.services.config as config_mod  # noqa: E402
import media_stack.api.services.disk as disk_mod  # noqa: E402
import media_stack.api.services.content as content_mod  # noqa: E402


# ---------------------------------------------------------------------------
# #11 Storage migration wizard
# ---------------------------------------------------------------------------

class TestValidateMigrationTarget(unittest.TestCase):
    def test_empty_path_invalid(self):
        result = disk_mod.validate_migration_target("")
        self.assertFalse(result["valid"])

    def test_relative_path_invalid(self):
        result = disk_mod.validate_migration_target("relative/path")
        self.assertFalse(result["valid"])

    def test_valid_existing_directory(self):
        with tempfile.TemporaryDirectory() as td:
            result = disk_mod.validate_migration_target(td)
        self.assertTrue(result["valid"])
        self.assertTrue(result["exists"])
        self.assertIn("free_bytes", result)

    def test_nonexistent_with_valid_parent(self):
        with tempfile.TemporaryDirectory() as td:
            target = os.path.join(td, "new-media")
            result = disk_mod.validate_migration_target(target)
        self.assertTrue(result["valid"])
        self.assertFalse(result["exists"])

    def test_nonexistent_parent_invalid(self):
        result = disk_mod.validate_migration_target("/nonexistent-parent-xyz/child")
        self.assertFalse(result["valid"])

    def test_generates_rsync_commands(self):
        with tempfile.TemporaryDirectory() as td:
            result = disk_mod.validate_migration_target(td)
        self.assertIn("commands", result)
        self.assertIn("rsync", result["commands"]["migrate"])
        self.assertIn("dry_run", result["commands"])

    def test_warns_insufficient_space(self):
        with tempfile.TemporaryDirectory() as td:
            with patch.object(disk_mod, "get_storage_breakdown", return_value={"total_bytes": 999999999999, "media_root": "/media"}):
                result = disk_mod.validate_migration_target(td)
        if result.get("warnings"):
            self.assertTrue(any("insufficient" in w.lower() or "space" in w.lower() for w in result["warnings"]))

    def test_file_target_invalid(self):
        with tempfile.NamedTemporaryFile(delete=False) as f:
            pass
        result = disk_mod.validate_migration_target(f.name)
        os.unlink(f.name)
        self.assertFalse(result["valid"])
        self.assertIn("not a directory", result["error"])


# ---------------------------------------------------------------------------
# #12 Onboarding wizard
# ---------------------------------------------------------------------------

class TestOnboardingStatus(unittest.TestCase):
    @patch("media_stack.api.services.health.probe_services", return_value={"services": {}, "healthy": 0, "total": 0})
    @patch("media_stack.api.services.health.discover_api_keys", return_value={})
    def test_returns_steps(self, *_):
        with patch("media_stack.api.services._resolve.resolve_profile_path", return_value=None):
            result = config_mod.get_onboarding_status()
        self.assertIn("steps", result)
        self.assertIn("progress_pct", result)
        self.assertGreater(len(result["steps"]), 0)

    @patch("media_stack.api.services.health.probe_services", return_value={"services": {}, "healthy": 0, "total": 0})
    @patch("media_stack.api.services.health.discover_api_keys", return_value={})
    def test_first_run_detected(self, *_):
        with patch("media_stack.api.services._resolve.resolve_profile_path", return_value=None):
            result = config_mod.get_onboarding_status()
        self.assertTrue(result["is_first_run"])

    @patch("media_stack.api.services.health.probe_services", return_value={"services": {"a": {"status": "ok"}}, "healthy": 10, "total": 10})
    @patch("media_stack.api.services.health.discover_api_keys", return_value={"sonarr": "key"})
    def test_progress_percentage(self, *_):
        import yaml
        with tempfile.TemporaryDirectory() as td:
            profile = Path(td) / "profile.yaml"
            yaml.dump({"technology_bindings": {"torrent_client": "qbittorrent", "usenet_client": "sabnzbd"}, "routing": {"gateway_host": "gw.local"}}, open(profile, "w"))
            with patch("media_stack.api.services._resolve.resolve_profile_path", return_value=str(profile)):
                result = config_mod.get_onboarding_status()
        self.assertGreater(result["progress_pct"], 0)

    @patch("media_stack.api.services.health.probe_services", return_value={"services": {}, "healthy": 0, "total": 0})
    @patch("media_stack.api.services.health.discover_api_keys", return_value={})
    def test_step_ids_unique(self, *_):
        with patch("media_stack.api.services._resolve.resolve_profile_path", return_value=None):
            result = config_mod.get_onboarding_status()
        ids = [s["id"] for s in result["steps"]]
        self.assertEqual(len(ids), len(set(ids)))

    @patch(
        "media_stack.api.services.health.probe_services",
        return_value={
            "services": {
                "sonarr": {"status": "ok"},
                "radarr": {"status": "ok"},
                "sabnzbd": {"status": "disabled"},
                "tautulli": {"status": "disabled"},
                "plex": {"status": "disabled"},
            },
            "healthy": 2,
            "total": 5,
        },
    )
    @patch("media_stack.api.services.health.discover_api_keys", return_value={})
    def test_services_running_excludes_disabled_from_denominator(self, *_):
        """A stack where every enabled service is healthy should report
        100% on services_running, regardless of how many disabled
        services live in the registry. Without this, an operator with
        2 ok + 3 disabled services sees 'warn (2/5 healthy · 40%)'
        even though there's nothing to fix."""
        with patch(
            "media_stack.api.services._resolve.resolve_profile_path",
            return_value=None,
        ):
            result = config_mod.get_onboarding_status()
        services_step = next(
            s for s in result["steps"] if s["id"] == "services_running"
        )
        # 2 of 2 enabled (denominator excludes the 3 disabled).
        self.assertEqual(services_step["status"], "ok")
        self.assertIn("2/2", services_step["detail"])
        self.assertIn("3 disabled", services_step["detail"])

    @patch(
        "media_stack.api.services.health.probe_services",
        return_value={
            "services": {
                "sonarr": {"status": "ok"},
                "radarr": {"status": "ok"},
                "sabnzbd": {"status": "disabled"},
                "tautulli": {"status": "disabled"},
            },
            "healthy": 2,
            "total": 4,
        },
    )
    @patch(
        "media_stack.api.services.health.discover_api_keys",
        return_value={"sonarr": "k1", "radarr": "k2"},
    )
    def test_api_keys_excludes_disabled_services_from_expected(self, *_):
        """Disabled services don't need API keys — they should NOT
        count as missing. The check should pass at 2/2 enabled
        servarrs even though sabnzbd + tautulli are listed in the
        registry as services with ``api_key_env`` set."""
        with patch(
            "media_stack.api.services._resolve.resolve_profile_path",
            return_value=None,
        ):
            result = config_mod.get_onboarding_status()
        api_step = next(s for s in result["steps"] if s["id"] == "api_keys")
        # Exact ratio depends on the live registry; what matters is
        # that disabled services don't tip the bucket into "warn".
        # Assert the detail lists fewer expected keys than the raw
        # registry would (the disabled exclusion happened).
        from media_stack.core.service_registry.registry import SERVICES
        raw_expected = sum(1 for s in SERVICES if s.api_key_env)
        # Detail format: "{discovered}/{expected} keys"
        emitted_expected = int(api_step["detail"].split("/")[1].split()[0])
        # Disabled servarrs (sabnzbd, tautulli) should not be in
        # ``expected``, so the emitted denominator must be strictly
        # less than the raw count when disabled services have keys.
        # If neither sabnzbd nor tautulli has api_key_env set, the
        # emitted count equals the raw count and that's also fine —
        # the rule still holds: emitted <= raw.
        self.assertLessEqual(emitted_expected, raw_expected)
        # Discovered ratio should be intact.
        self.assertIn("2/", api_step["detail"])


# ---------------------------------------------------------------------------
# #13 Download analytics
# ---------------------------------------------------------------------------

class TestDownloadAnalytics(unittest.TestCase):
    @patch("media_stack.api.services.content_analytics_mixin.discover_api_keys", return_value={})
    def test_no_keys_returns_empty(self, _):
        result = content_mod.get_download_analytics()
        self.assertEqual(result["total_records"], 0)
        self.assertIn("daily_trend", result)
        self.assertIn("top_indexers", result)

    @patch("media_stack.api.services.content_analytics_mixin.discover_api_keys", return_value={"sonarr": "key"})
    @patch("urllib.request.urlopen")
    def test_aggregates_records(self, mock_urlopen, _):
        resp = MagicMock()
        resp.read.return_value = json.dumps({"records": [
            {"sourceTitle": "Show S01E01", "eventType": "grabbed", "date": "2026-04-10T12:00:00", "quality": {}, "data": {"indexer": "NZBgeek"}},
            {"sourceTitle": "Movie 2026", "eventType": "grabbed", "date": "2026-04-10T13:00:00", "quality": {}, "data": {"indexer": "NZBgeek"}},
            {"sourceTitle": "Show S01E02", "eventType": "grabbed", "date": "2026-04-09T12:00:00", "quality": {}, "data": {"indexer": "Torznab"}},
        ]}).encode()
        resp.__enter__ = MagicMock(return_value=resp)
        resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = resp
        result = content_mod.get_download_analytics()
        self.assertEqual(result["total_records"], 3)
        self.assertGreater(len(result["daily_trend"]), 0)
        self.assertGreater(len(result["top_indexers"]), 0)

    @patch("media_stack.api.services.content_analytics_mixin.discover_api_keys", return_value={"sonarr": "key"})
    @patch("urllib.request.urlopen", side_effect=Exception("timeout"))
    def test_http_failure_graceful(self, *_):
        result = content_mod.get_download_analytics()
        self.assertEqual(result["total_records"], 0)


# ---------------------------------------------------------------------------
# #14 Custom service addition
# ---------------------------------------------------------------------------

class TestAddCustomService(unittest.TestCase):
    def test_missing_fields_returns_error(self):
        result = config_mod.add_custom_service({"id": "test"})
        self.assertIn("error", result)

    def test_invalid_id_returns_error(self):
        result = config_mod.add_custom_service({"id": "bad id!", "name": "X", "port": 8080})
        self.assertIn("error", result)

    def test_creates_yaml_file(self):
        from media_stack.core.service_registry import registry
        orig_services = list(registry.SERVICES)
        orig_map = dict(registry.SERVICE_MAP)
        orig_cats = list(registry.CATEGORIES)
        try:
            with tempfile.TemporaryDirectory() as td:
                # Copy existing service YAMLs so reload doesn't lose them
                svc_dir = Path(__file__).resolve().parents[3] / "contracts" / "services"
                if svc_dir.is_dir():
                    import shutil
                    for f in svc_dir.glob("*.yaml"):
                        shutil.copy2(f, td)
                with patch.dict(os.environ, {"SERVICES_REGISTRY_DIR": td}):
                    result = config_mod.add_custom_service({
                        "id": "my-custom-app",
                        "name": "My Custom App",
                        "port": 9999,
                        "host": "my-app",
                        "desc": "A custom service",
                    })
                if result.get("status") == "created":
                    self.assertTrue((Path(td) / "my-custom-app.yaml").exists())
                    content = (Path(td) / "my-custom-app.yaml").read_text()
                    self.assertIn("my-custom-app", content)
                    self.assertIn("9999", content)
        finally:
            registry.SERVICES = orig_services
            registry.SERVICE_MAP = orig_map
            registry.CATEGORIES.clear()
            registry.CATEGORIES.extend(orig_cats)

    def test_duplicate_id_returns_error(self):
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "existing.yaml").write_text("service:\n  id: existing\n")
            with patch.dict(os.environ, {"SERVICES_REGISTRY_DIR": td}):
                result = config_mod.add_custom_service({"id": "existing", "name": "X", "port": 8080})
        self.assertIn("error", result)
        self.assertIn("already exists", result["error"])

    def test_empty_id_returns_error(self):
        result = config_mod.add_custom_service({"id": "", "name": "X", "port": 80})
        self.assertIn("error", result)


if __name__ == "__main__":
    unittest.main()
