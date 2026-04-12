"""Tests for config backup/restore — validation, rollback, API key restore."""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

import media_stack.api.services.config as config_mod  # noqa: E402
from media_stack.api.services.registry import ServiceDef  # noqa: E402
import media_stack.api.services.registry as registry_mod  # noqa: E402


def _svc(id: str, **kw) -> ServiceDef:
    return ServiceDef(id=id, name=id.capitalize(), **kw)


FAKE_SVCS = [
    _svc("sonarr", api_key_env="SONARR_API_KEY", api_key_config="sonarr/config.xml",
         api_key_format="xml"),
    _svc("radarr", api_key_env="RADARR_API_KEY", api_key_config="radarr/config.xml",
         api_key_format="xml", password_config="radarr/config.xml"),
]
FAKE_SVC_MAP = {s.id: s for s in FAKE_SVCS}


class TestGetBackup(unittest.TestCase):
    """get_backup should produce a complete, restorable JSON backup."""

    def test_backup_contains_version_and_timestamp(self):
        with tempfile.TemporaryDirectory() as tmpdir, \
             patch.dict(os.environ, {"CONFIG_ROOT": tmpdir}), \
             patch("media_stack.api.services._resolve.resolve_profile_path", return_value=""):
            raw = config_mod.get_backup(MagicMock(to_dict=lambda: {}))
            data = json.loads(raw)
            self.assertEqual(data["version"], "2")
            self.assertIn("timestamp", data)

    def test_backup_includes_service_configs(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "sonarr").mkdir()
            (Path(tmpdir) / "sonarr" / "config.xml").write_text("<Config><ApiKey>abc</ApiKey></Config>")
            with patch.dict(os.environ, {"CONFIG_ROOT": tmpdir}), \
                 patch("media_stack.api.services._resolve.resolve_profile_path", return_value=""), \
                 patch.object(registry_mod, "SERVICES", FAKE_SVCS):
                raw = config_mod.get_backup(MagicMock(to_dict=lambda: {}))
                data = json.loads(raw)
            self.assertIn("sonarr/config.xml", data.get("service_configs", {}))

    def test_backup_includes_full_api_keys(self):
        with tempfile.TemporaryDirectory() as tmpdir, \
             patch.dict(os.environ, {"CONFIG_ROOT": tmpdir, "SONARR_API_KEY": "full-key-value-12345"}), \
             patch("media_stack.api.services._resolve.resolve_profile_path", return_value=""):
            raw = config_mod.get_backup(MagicMock(to_dict=lambda: {}))
            data = json.loads(raw)
            self.assertEqual(data["api_keys"]["SONARR_API_KEY"], "full-key-value-12345")
            self.assertIn("...", data["api_keys_masked"]["SONARR_API_KEY"])

    def test_backup_includes_valid_config_paths(self):
        with tempfile.TemporaryDirectory() as tmpdir, \
             patch.dict(os.environ, {"CONFIG_ROOT": tmpdir}), \
             patch("media_stack.api.services._resolve.resolve_profile_path", return_value=""), \
             patch.object(registry_mod, "SERVICES", FAKE_SVCS):
            raw = config_mod.get_backup(MagicMock(to_dict=lambda: {}))
            data = json.loads(raw)
            self.assertIn("sonarr/config.xml", data["valid_config_paths"])

    def test_backup_skips_huge_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "sonarr").mkdir()
            (Path(tmpdir) / "sonarr" / "config.xml").write_text("x" * 200_000)
            with patch.dict(os.environ, {"CONFIG_ROOT": tmpdir}), \
                 patch("media_stack.api.services._resolve.resolve_profile_path", return_value=""), \
                 patch.object(registry_mod, "SERVICES", FAKE_SVCS):
                raw = config_mod.get_backup(MagicMock(to_dict=lambda: {}))
                data = json.loads(raw)
            self.assertNotIn("sonarr/config.xml", data.get("service_configs", {}))


class TestRestoreBackup(unittest.TestCase):
    """restore_backup should validate, backup, restore, and rollback."""

    def test_restore_writes_config_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "sonarr").mkdir()
            backup = {"version": "2", "service_configs": {"sonarr/config.xml": "<Config/>"}}
            with patch.dict(os.environ, {"CONFIG_ROOT": tmpdir}), \
                 patch.object(registry_mod, "SERVICES", FAKE_SVCS):
                result = config_mod.restore_backup(backup)
            self.assertEqual(result["status"], "ok")
            self.assertIn("sonarr/config.xml", result["restored"])
            self.assertEqual((Path(tmpdir) / "sonarr" / "config.xml").read_text(), "<Config/>")

    def test_restore_rejects_bad_version(self):
        result = config_mod.restore_backup({"version": "99", "service_configs": {}})
        self.assertEqual(result["status"], "error")
        self.assertIn("unsupported", result["error"])

    def test_restore_rejects_path_traversal(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            backup = {"version": "2", "service_configs": {"../../etc/passwd": "hacked"}}
            with patch.dict(os.environ, {"CONFIG_ROOT": tmpdir}), \
                 patch.object(registry_mod, "SERVICES", FAKE_SVCS):
                result = config_mod.restore_backup(backup)
            self.assertIn("skipped unsafe path", result["errors"][0])

    def test_restore_skips_unknown_paths(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            backup = {"version": "2", "service_configs": {"unknown/file.txt": "data"}}
            with patch.dict(os.environ, {"CONFIG_ROOT": tmpdir}), \
                 patch.object(registry_mod, "SERVICES", FAKE_SVCS):
                result = config_mod.restore_backup(backup)
            self.assertIn("unknown/file.txt", result["skipped"])
            self.assertEqual(len(result["restored"]), 0)

    def test_restore_creates_pre_restore_backup(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "sonarr").mkdir()
            (Path(tmpdir) / "sonarr" / "config.xml").write_text("<Old/>")
            backup = {"version": "2", "service_configs": {"sonarr/config.xml": "<New/>"}}
            with patch.dict(os.environ, {"CONFIG_ROOT": tmpdir}), \
                 patch.object(registry_mod, "SERVICES", FAKE_SVCS):
                result = config_mod.restore_backup(backup)
            self.assertEqual(result["pre_restore_count"], 1)

    def test_restore_restores_api_keys_to_env(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            backup = {
                "version": "2",
                "service_configs": {},
                "api_keys": {"SONARR_API_KEY": "restored-key-123"},
            }
            with patch.dict(os.environ, {"CONFIG_ROOT": tmpdir}, clear=False), \
                 patch.object(registry_mod, "SERVICES", FAKE_SVCS):
                result = config_mod.restore_backup(backup)
                self.assertIn("SONARR_API_KEY", result["keys_restored"])
                self.assertEqual(os.environ.get("SONARR_API_KEY"), "restored-key-123")

    def test_restore_skips_masked_api_keys(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            backup = {
                "version": "2",
                "service_configs": {},
                "api_keys": {"SONARR_API_KEY": "abcd1234..."},
            }
            with patch.dict(os.environ, {"CONFIG_ROOT": tmpdir, "SONARR_API_KEY": "original"}, clear=False), \
                 patch.object(registry_mod, "SERVICES", FAKE_SVCS):
                result = config_mod.restore_backup(backup)
                self.assertEqual(len(result["keys_restored"]), 0)
                self.assertEqual(os.environ.get("SONARR_API_KEY"), "original")

    def test_restore_rejects_missing_service_configs(self):
        result = config_mod.restore_backup({"version": "2", "service_configs": "not-a-dict"})
        self.assertEqual(result["status"], "error")

    def test_restore_rollback_on_majority_failure(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "sonarr").mkdir()
            original = "<Original/>"
            (Path(tmpdir) / "sonarr" / "config.xml").write_text(original)
            # Create a backup with one valid path and two that will fail
            backup = {
                "version": "2",
                "service_configs": {
                    "sonarr/config.xml": "<New/>",
                },
            }
            # Patch write to fail for all paths
            real_write = Path.write_text
            call_count = [0]

            def failing_write(self_path, *a, **kw):
                call_count[0] += 1
                raise OSError("disk full")

            with patch.dict(os.environ, {"CONFIG_ROOT": tmpdir}), \
                 patch.object(registry_mod, "SERVICES", FAKE_SVCS), \
                 patch.object(Path, "write_text", failing_write):
                result = config_mod.restore_backup(backup)

            # Should have errors (write failed)
            self.assertTrue(len(result.get("errors", [])) > 0)


if __name__ == "__main__":
    unittest.main()
