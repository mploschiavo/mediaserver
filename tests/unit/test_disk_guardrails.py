"""Tests for disk guardrail configuration and cleanup preview."""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

import media_stack.api.services.disk as disk_mod  # noqa: E402


def _make_config(guardrails=None, tmp_dir=None):
    cfg = {"disk_guardrails": guardrails or {}}
    p = Path(tmp_dir) / "config.json"
    p.write_text(json.dumps(cfg))
    return str(p)


class TestLoadGuardrailConfig(unittest.TestCase):
    @patch("media_stack.api.services.disk.resolve_config_path", return_value=None)
    def test_no_config_returns_disabled(self, _):
        g = disk_mod._load_guardrail_config()
        self.assertFalse(g["enabled"])

    def test_full_config(self):
        with tempfile.TemporaryDirectory() as td:
            cfg_path = _make_config(
                {"enabled": True, "max_used_percent": 80, "target_used_percent": 70,
                 "qbit_cleanup": {"enabled": True, "min_ratio": 2.0}}, td)
            with patch("media_stack.api.services.disk.resolve_config_path", return_value=cfg_path):
                g = disk_mod._load_guardrail_config()
        self.assertTrue(g["enabled"])
        self.assertEqual(g["max_used_percent"], 80)
        self.assertEqual(g["qbit_cleanup"]["min_ratio"], 2.0)

    def test_defaults_when_keys_missing(self):
        with tempfile.TemporaryDirectory() as td:
            cfg_path = _make_config({"enabled": False}, td)
            with patch("media_stack.api.services.disk.resolve_config_path", return_value=cfg_path):
                g = disk_mod._load_guardrail_config()
        self.assertEqual(g["max_used_percent"], 65)
        self.assertEqual(g["target_used_percent"], 58)


class TestUpdateGuardrails(unittest.TestCase):
    @patch("media_stack.api.services.disk.resolve_config_path", return_value=None)
    def test_no_config_returns_error(self, _):
        result = disk_mod.update_guardrails({"enabled": True})
        self.assertIn("error", result)

    def test_valid_update(self):
        with tempfile.TemporaryDirectory() as td:
            cfg_path = _make_config({}, td)
            with patch("media_stack.api.services.disk.resolve_config_path", return_value=cfg_path):
                result = disk_mod.update_guardrails({"enabled": True, "max_used_percent": 75})
        self.assertEqual(result["status"], "updated")
        self.assertIn("enabled", result["changed"])

    def test_no_changes(self):
        with tempfile.TemporaryDirectory() as td:
            cfg_path = _make_config({}, td)
            with patch("media_stack.api.services.disk.resolve_config_path", return_value=cfg_path):
                result = disk_mod.update_guardrails({"unknown_key": True})
        self.assertEqual(result["status"], "no_changes")

    def test_qbit_prefixed_params(self):
        with tempfile.TemporaryDirectory() as td:
            cfg_path = _make_config({}, td)
            with patch("media_stack.api.services.disk.resolve_config_path", return_value=cfg_path):
                result = disk_mod.update_guardrails({"qbit_min_ratio": 2.0, "qbit_enabled": True})
        self.assertEqual(result["status"], "updated")
        self.assertIn("qbit_min_ratio", result["changed"])

    def test_persists_to_file(self):
        with tempfile.TemporaryDirectory() as td:
            cfg_path = _make_config({}, td)
            with patch("media_stack.api.services.disk.resolve_config_path", return_value=cfg_path):
                disk_mod.update_guardrails({"max_used_percent": 90})
            cfg = json.loads(Path(cfg_path).read_text())
            self.assertEqual(cfg["disk_guardrails"]["max_used_percent"], 90)

    def test_disallowed_keys_ignored(self):
        with tempfile.TemporaryDirectory() as td:
            cfg_path = _make_config({}, td)
            with patch("media_stack.api.services.disk.resolve_config_path", return_value=cfg_path):
                result = disk_mod.update_guardrails({"badkey": 1, "enabled": True})
        self.assertEqual(result["changed"], ["enabled"])


class TestGetDisk(unittest.TestCase):
    @patch.dict(os.environ, {"CONFIG_ROOT": "/nonexistent"})
    def test_missing_path(self):
        with patch("media_stack.api.services.disk.resolve_config_path", return_value=None):
            result = disk_mod.get_disk()
        self.assertIn("disk", result)

    @patch("media_stack.api.services.disk.disk_usage")
    @patch.dict(os.environ, {"CONFIG_ROOT": "/tmp"})
    def test_percent_calculation(self, mock_du):
        mock_du.return_value = MagicMock(total=1000, used=650, free=350)
        with patch("media_stack.api.services.disk.resolve_config_path", return_value=None):
            result = disk_mod.get_disk()
        config_disk = result["disk"].get("config", {})
        if "percent_used" in config_disk:
            self.assertEqual(config_disk["percent_used"], 65.0)

    @patch("media_stack.api.services.disk.disk_usage")
    @patch.dict(os.environ, {"CONFIG_ROOT": "/tmp"})
    def test_guardrails_included(self, mock_du):
        mock_du.return_value = MagicMock(total=1000, used=500, free=500)
        with patch("media_stack.api.services.disk.resolve_config_path", return_value=None):
            result = disk_mod.get_disk()
        self.assertIn("guardrails", result)


class TestPreviewCleanup(unittest.TestCase):
    @patch("media_stack.api.services.disk._load_guardrail_config", return_value={"enabled": False})
    def test_disabled_guardrails(self, _):
        result = disk_mod.preview_cleanup()
        self.assertEqual(result["candidates"], [])
        self.assertIn("disabled", result.get("message", "").lower())

    @patch("media_stack.api.services.disk._load_guardrail_config",
           return_value={"enabled": True, "qbit_cleanup": {"enabled": False}})
    def test_disabled_qbit_cleanup(self, _):
        result = disk_mod.preview_cleanup()
        self.assertIn("disabled", result.get("message", "").lower())

    @patch("media_stack.api.services.disk._load_guardrail_config",
           return_value={"enabled": True, "max_used_percent": 65,
                         "qbit_cleanup": {"enabled": True, "min_completion_age_hours": 36,
                                         "min_ratio": 1.0, "min_seeding_time_minutes": 720}})
    @patch("media_stack.api.services.disk.get_disk", return_value={"disk": {}})
    @patch("media_stack.api.services.disk.default_torrent_client_url", return_value="http://localhost:8080")
    @patch("urllib.request.build_opener")
    def test_qbit_login_failure(self, mock_opener, *_):
        mock_opener.return_value.open.side_effect = ConnectionRefusedError("refused")
        result = disk_mod.preview_cleanup()
        self.assertIn("error", result)

    @patch("media_stack.api.services.disk._load_guardrail_config",
           return_value={"enabled": True, "max_used_percent": 65,
                         "qbit_cleanup": {"enabled": True, "min_completion_age_hours": 0,
                                         "min_ratio": 0, "min_seeding_time_minutes": 0}})
    @patch("media_stack.api.services.disk.get_disk", return_value={"disk": {}})
    @patch("media_stack.api.services.disk.default_torrent_client_url", return_value="http://localhost:8080")
    @patch("urllib.request.build_opener")
    def test_candidates_returned(self, mock_opener, *_):
        import io
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps([
            {"name": "test-torrent", "completion_on": 1, "ratio": 5.0, "seeding_time": 99999, "size": 1000, "category": "tv"},
        ]).encode()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        opener = mock_opener.return_value
        opener.open.side_effect = [MagicMock(), mock_resp]
        result = disk_mod.preview_cleanup()
        self.assertGreater(len(result.get("candidates", [])), 0)

    def test_update_guardrails_max_delete_per_run(self):
        with tempfile.TemporaryDirectory() as td:
            cfg_path = _make_config({}, td)
            with patch("media_stack.api.services.disk.resolve_config_path", return_value=cfg_path):
                result = disk_mod.update_guardrails({"qbit_max_delete_per_run": 50})
            self.assertEqual(result["status"], "updated")

    def test_update_guardrails_delete_files(self):
        with tempfile.TemporaryDirectory() as td:
            cfg_path = _make_config({}, td)
            with patch("media_stack.api.services.disk.resolve_config_path", return_value=cfg_path):
                result = disk_mod.update_guardrails({"qbit_delete_files": False})
            self.assertEqual(result["status"], "updated")


if __name__ == "__main__":
    unittest.main()
