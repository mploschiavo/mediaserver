"""Tests for admin.py — API key rotation, service restart, hard reset."""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

import media_stack.api.services.admin as admin_mod  # noqa: E402
import media_stack.services.apps.jellyfin.admin_ops as jellyfin_admin_ops  # noqa: E402
from media_stack.api.services.registry import ServiceDef  # noqa: E402


def _svc(id, **kw):
    return ServiceDef(id=id, name=id.capitalize(), **kw)


class TestRotateKeys(unittest.TestCase):
    """API key rotation across services."""

    @patch.dict(os.environ, {"CONFIG_ROOT": "/nonexistent"})
    @patch.object(admin_mod, "get_services_with_api_keys", return_value=[])
    def test_no_services_returns_empty(self, _):
        result = admin_mod.rotate_keys()
        self.assertIn("keys", result)
        self.assertEqual(len(result["keys"]), 0)

    @patch.dict(os.environ, {"CONFIG_ROOT": "/tmp", "SONARR_API_KEY": "oldkey"})
    @patch.object(admin_mod, "get_services_with_api_keys", return_value=[
        _svc("sonarr", api_key_env="SONARR_API_KEY", api_key_config="sonarr/config.xml", api_key_format="xml")
    ])
    @patch.object(admin_mod, "persist_keys_to_secret")
    def test_xml_key_rotated(self, mock_persist, _):
        with tempfile.TemporaryDirectory() as td:
            cfg_dir = Path(td) / "sonarr"
            cfg_dir.mkdir()
            cfg_path = cfg_dir / "config.xml"
            cfg_path.write_text("<Config><ApiKey>oldkey</ApiKey></Config>")
            with patch.dict(os.environ, {"CONFIG_ROOT": td}):
                result = admin_mod.rotate_keys(["sonarr"])
        self.assertIn("SONARR_API_KEY", result.get("keys", []))

    @patch.object(admin_mod, "get_services_with_api_keys", return_value=[
        _svc("sonarr", api_key_env="SONARR_API_KEY", api_key_config="sonarr/config.xml", api_key_format="xml")
    ])
    @patch.dict(os.environ, {"CONFIG_ROOT": "/nonexistent"})
    def test_missing_config_file_no_rotation(self, _):
        result = admin_mod.rotate_keys(["sonarr"])
        # File doesn't exist so no key rotated
        self.assertEqual(len(result.get("keys", [])), 0)


class TestRestartService(unittest.TestCase):
    @patch.dict(os.environ, {"K8S_NAMESPACE": ""})
    @patch("docker.from_env")
    def test_compose_restart(self, mock_docker):
        container = MagicMock()
        mock_docker.return_value.containers.get.return_value = container
        result = admin_mod.restart_service("sonarr")
        container.restart.assert_called_once()
        self.assertEqual(result["status"], "restarted")

    @patch.dict(os.environ, {"K8S_NAMESPACE": ""})
    @patch("docker.from_env", side_effect=Exception("not found"))
    def test_restart_failure(self, _):
        result = admin_mod.restart_service("nonexistent")
        self.assertIn("error", result)


class TestBatchRestart(unittest.TestCase):
    @patch.object(admin_mod._instance, "restart_service")
    def test_restarts_multiple(self, mock_restart):
        mock_restart.return_value = {"status": "restarted"}
        result = admin_mod.batch_restart(["sonarr", "radarr"])
        self.assertEqual(mock_restart.call_count, 2)
        self.assertIn("results", result)


class TestHardResetService(unittest.TestCase):
    @patch.object(admin_mod, "restart_service", return_value={"status": "restarted"})
    @patch("media_stack.api.services.registry.read_api_key_from_file", return_value="newkey")
    @patch("media_stack.api.services.registry.read_api_key_via_http", return_value="")
    @patch.object(admin_mod, "persist_keys_to_secret")
    def test_hard_reset_discovers_key(self, mock_persist, _, mock_read, mock_restart):
        from media_stack.api.services.registry import SERVICES
        svc = next((s for s in SERVICES if s.api_key_env), None)
        if not svc:
            self.skipTest("No service with api_key_env")
        result = admin_mod.hard_reset_service(svc.id, {})
        self.assertIn("status", result)

    def test_unknown_service_returns_error(self):
        result = admin_mod.hard_reset_service("nonexistent-xyz", {})
        self.assertIn("error", result)


class TestPersistKeysToSecret(unittest.TestCase):
    @patch.dict(os.environ, {"K8S_NAMESPACE": ""})
    def test_compose_mode_noop(self):
        # In compose mode, persist_keys_to_secret is a no-op
        admin_mod.persist_keys_to_secret({"KEY": "val"})  # Should not raise

    @patch.dict(os.environ, {"K8S_NAMESPACE": "media-stack"})
    @patch("kubernetes.client.CoreV1Api")
    @patch("kubernetes.config.load_incluster_config")
    def test_k8s_mode_creates_secret(self, mock_config, mock_api_cls):
        mock_api = MagicMock()
        mock_api_cls.return_value = mock_api
        mock_api.read_namespaced_secret.side_effect = Exception("not found")
        admin_mod.persist_keys_to_secret({"KEY": "val"})


class TestDiscoverJellyfinApiKey(unittest.TestCase):
    def test_missing_db_returns_empty(self):
        result = jellyfin_admin_ops.discover_api_key("/nonexistent")
        self.assertEqual(result, "")

    def test_valid_db(self):
        import sqlite3
        with tempfile.TemporaryDirectory() as td:
            db_dir = Path(td) / "jellyfin" / "data"
            db_dir.mkdir(parents=True)
            db_path = db_dir / "jellyfin.db"
            conn = sqlite3.connect(str(db_path))
            conn.execute("CREATE TABLE ApiKeys (Id INTEGER PRIMARY KEY, AccessToken TEXT)")
            conn.execute("INSERT INTO ApiKeys (AccessToken) VALUES ('jfkey123')")
            conn.commit()
            conn.close()
            result = jellyfin_admin_ops.discover_api_key(td)
        self.assertEqual(result, "jfkey123")


class TestIsMediaServerResetPath(unittest.TestCase):
    def test_known_path(self):
        self.assertTrue(admin_mod.is_media_server_reset_path("/api/jellyfin/reset"))

    def test_unknown_path(self):
        self.assertFalse(admin_mod.is_media_server_reset_path("/api/unknown"))


if __name__ == "__main__":
    unittest.main()
