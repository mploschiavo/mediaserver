"""Unit tests for media_stack.api.services.disk."""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.api.services import disk as MODULE


# ---------------------------------------------------------------------------
# _load_guardrail_config
# ---------------------------------------------------------------------------

class TestLoadGuardrailConfig(unittest.TestCase):
    def test_valid_config(self):
        cfg = {
            "disk_guardrails": {
                "enabled": True,
                "max_used_percent": 70,
                "target_used_percent": 60,
                "monitor_path": "/srv-stack",
                "qbit_cleanup": {
                    "enabled": True,
                    "min_completion_age_hours": 48,
                    "min_ratio": 1.5,
                    "min_seeding_time_minutes": 360,
                    "max_delete_per_run": 50,
                    "delete_files": False,
                    "categories": ["tv-sonarr"],
                },
            }
        }
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(cfg, f)
            f.flush()
            cfg_path = f.name
        try:
            with patch("media_stack.api.services.disk.resolve_config_path", return_value=cfg_path):
                result = MODULE._load_guardrail_config()
                self.assertTrue(result["enabled"])
                self.assertEqual(result["max_used_percent"], 70.0)
                self.assertEqual(result["target_used_percent"], 60.0)
                self.assertEqual(result["monitor_path"], "/srv-stack")
                self.assertTrue(result["qbit_cleanup"]["enabled"])
                self.assertEqual(result["qbit_cleanup"]["min_completion_age_hours"], 48.0)
                self.assertEqual(result["qbit_cleanup"]["min_ratio"], 1.5)
                self.assertEqual(result["qbit_cleanup"]["min_seeding_time_minutes"], 360)
                self.assertFalse(result["qbit_cleanup"]["delete_files"])
                self.assertEqual(result["qbit_cleanup"]["categories"], ["tv-sonarr"])
        finally:
            os.unlink(cfg_path)

    def test_missing_config(self):
        with patch("media_stack.api.services.disk.resolve_config_path", return_value=None):
            result = MODULE._load_guardrail_config()
            self.assertFalse(result["enabled"])

    def test_empty_config(self):
        """Config file exists but has no disk_guardrails key."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump({"services": {}}, f)
            f.flush()
            cfg_path = f.name
        try:
            with patch("media_stack.api.services.disk.resolve_config_path", return_value=cfg_path):
                result = MODULE._load_guardrail_config()
                self.assertFalse(result["enabled"])
                # Defaults should be populated
                self.assertEqual(result["max_used_percent"], 65.0)
                self.assertEqual(result["qbit_cleanup"]["min_ratio"], 1.0)
        finally:
            os.unlink(cfg_path)

    def test_corrupt_json_returns_defaults(self):
        """Config file with broken JSON returns safe defaults."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write("{not valid json")
            f.flush()
            cfg_path = f.name
        try:
            with patch("media_stack.api.services.disk.resolve_config_path", return_value=cfg_path):
                result = MODULE._load_guardrail_config()
                self.assertFalse(result["enabled"])
        finally:
            os.unlink(cfg_path)


# ---------------------------------------------------------------------------
# update_guardrails
# ---------------------------------------------------------------------------

class TestUpdateGuardrails(unittest.TestCase):
    def _make_config(self, cfg: dict) -> str:
        """Write a config JSON to a temp file and return its path."""
        f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
        json.dump(cfg, f)
        f.flush()
        f.close()
        return f.name

    def test_valid_updates(self):
        cfg_path = self._make_config({"disk_guardrails": {"enabled": False}})
        try:
            with patch("media_stack.api.services.disk.resolve_config_path", return_value=cfg_path):
                result = MODULE.update_guardrails({
                    "enabled": True,
                    "max_used_percent": 80,
                })
                self.assertEqual(result["status"], "updated")
                self.assertIn("enabled", result["changed"])
                self.assertIn("max_used_percent", result["changed"])
                # Verify persisted
                saved = json.loads(Path(cfg_path).read_text())
                self.assertTrue(saved["disk_guardrails"]["enabled"])
                self.assertEqual(saved["disk_guardrails"]["max_used_percent"], 80)
        finally:
            os.unlink(cfg_path)

    def test_no_changes(self):
        cfg_path = self._make_config({"disk_guardrails": {"enabled": True}})
        try:
            with patch("media_stack.api.services.disk.resolve_config_path", return_value=cfg_path):
                result = MODULE.update_guardrails({"unknown_key": "value"})
                self.assertEqual(result["status"], "no_changes")
        finally:
            os.unlink(cfg_path)

    def test_qbit_prefixed_keys(self):
        """Keys starting with 'qbit_' are routed into the qbit_cleanup sub-dict."""
        cfg_path = self._make_config({"disk_guardrails": {}})
        try:
            with patch("media_stack.api.services.disk.resolve_config_path", return_value=cfg_path):
                result = MODULE.update_guardrails({
                    "qbit_enabled": True,
                    "qbit_min_ratio": 2.0,
                    "qbit_max_delete_per_run": 100,
                })
                self.assertEqual(result["status"], "updated")
                self.assertEqual(len(result["changed"]), 3)
                saved = json.loads(Path(cfg_path).read_text())
                qc = saved["disk_guardrails"]["qbit_cleanup"]
                self.assertTrue(qc["enabled"])
                self.assertEqual(qc["min_ratio"], 2.0)
                self.assertEqual(qc["max_delete_per_run"], 100)
        finally:
            os.unlink(cfg_path)

    def test_missing_config_returns_error(self):
        with patch("media_stack.api.services.disk.resolve_config_path", return_value=None):
            result = MODULE.update_guardrails({"enabled": True})
            self.assertIn("error", result)

    def test_mixed_top_level_and_qbit_keys(self):
        cfg_path = self._make_config({"disk_guardrails": {"enabled": False}})
        try:
            with patch("media_stack.api.services.disk.resolve_config_path", return_value=cfg_path):
                result = MODULE.update_guardrails({
                    "enabled": True,
                    "monitor_path": "/data",
                    "qbit_delete_files": False,
                })
                self.assertEqual(result["status"], "updated")
                self.assertEqual(sorted(result["changed"]), ["enabled", "monitor_path", "qbit_delete_files"])
                saved = json.loads(Path(cfg_path).read_text())
                self.assertTrue(saved["disk_guardrails"]["enabled"])
                self.assertEqual(saved["disk_guardrails"]["monitor_path"], "/data")
                self.assertFalse(saved["disk_guardrails"]["qbit_cleanup"]["delete_files"])
        finally:
            os.unlink(cfg_path)


# ---------------------------------------------------------------------------
# get_disk (mocked)
# ---------------------------------------------------------------------------

class TestGetDisk(unittest.TestCase):
    def test_returns_disk_and_guardrails(self):
        """get_disk returns both disk usage and guardrail config."""
        fake_usage = mock.MagicMock()
        fake_usage.total = 1_000_000_000
        fake_usage.used = 500_000_000
        fake_usage.free = 500_000_000

        tmpdir = tempfile.mkdtemp()
        try:
            with patch.dict(os.environ, {
                "CONFIG_ROOT": tmpdir,
                "MEDIA_ROOT": "",
            }, clear=False):
                with patch("media_stack.api.services.disk.disk_usage", return_value=fake_usage):
                    with patch("media_stack.api.services.disk._load_guardrail_config", return_value={"enabled": False}):
                        result = MODULE.get_disk()
                        self.assertIn("disk", result)
                        self.assertIn("guardrails", result)
                        config_entry = result["disk"]["config"]
                        self.assertEqual(config_entry["total_bytes"], 1_000_000_000)
                        self.assertEqual(config_entry["percent_used"], 50.0)
        finally:
            import shutil
            shutil.rmtree(tmpdir)

    def test_nonexistent_path_reports_error(self):
        """When a path does not exist, get_disk reports an error for that label."""
        with patch.dict(os.environ, {
            "CONFIG_ROOT": "/nonexistent/config/path",
            "MEDIA_ROOT": "",
        }, clear=False):
            with patch("media_stack.api.services.disk._load_guardrail_config", return_value={"enabled": False}):
                result = MODULE.get_disk()
                self.assertIn("config", result["disk"])
                self.assertIn("error", result["disk"]["config"])


if __name__ == "__main__":
    unittest.main()
