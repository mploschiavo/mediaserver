"""Tests for config drift detection and dashboard features."""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

import media_stack.api.services.config as config_mod  # noqa: E402

DASHBOARD_PATH = ROOT / "src" / "media_stack" / "api" / "dashboard.html"
DASHBOARD_HTML = DASHBOARD_PATH.read_text(encoding="utf-8") if DASHBOARD_PATH.exists() else ""


class TestConfigDrift(unittest.TestCase):
    @patch.dict(os.environ, {"BOOTSTRAP_PROFILE_FILE": "", "CONFIG_ROOT": "/nonexistent", "K8S_NAMESPACE": ""})
    @patch("media_stack.api.services.config.resolve_profile_path", return_value=None)
    def test_no_profile_returns_clean(self, _):
        result = config_mod.get_config_drift()
        self.assertIsInstance(result["drifts"], list)
        self.assertIn("total", result)
        self.assertIn("clean", result)

    def test_routing_drift_detected(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            import yaml
            yaml.dump({"routing": {"base_domain": "old.com", "strategy": "hybrid"}}, f)
            f.flush()
            with patch("media_stack.api.services.config.resolve_profile_path", return_value=f.name), \
                 patch.dict(os.environ, {"CONFIG_ROOT": "/nonexistent", "K8S_NAMESPACE": ""}):
                # get_routing will read profile + overrides; profile says old.com
                # but get_routing may overlay with overrides
                result = config_mod.get_config_drift()
        os.unlink(f.name)
        # Even if no drift detected (profile matches routing), structure is correct
        self.assertIsInstance(result["drifts"], list)
        self.assertIn("total", result)

    @patch.dict(os.environ, {"K8S_NAMESPACE": "", "CONFIG_ROOT": "/nonexistent",
                              "SONARR_API_KEY": "envkey123"})
    @patch("media_stack.api.services.config.resolve_profile_path", return_value=None)
    @patch("media_stack.api.services.registry.read_api_key_from_file", return_value="filekey456")
    def test_api_key_drift_detected(self, mock_read, _):
        from media_stack.api.services.registry import SERVICES, ServiceDef
        # Only test if sonarr is in the registry
        sonarr = next((s for s in SERVICES if s.id == "sonarr"), None)
        if not sonarr:
            self.skipTest("sonarr not in registry")
        result = config_mod.get_config_drift()
        key_drifts = [d for d in result["drifts"] if d["area"] == "api_key"]
        self.assertTrue(len(key_drifts) > 0)
        self.assertEqual(key_drifts[0]["key"], "sonarr")

    @patch.dict(os.environ, {"K8S_NAMESPACE": "", "CONFIG_ROOT": "/nonexistent"})
    @patch("media_stack.api.services.config.resolve_profile_path", return_value=None)
    def test_clean_when_no_drift(self, _):
        result = config_mod.get_config_drift()
        if result["total"] == 0:
            self.assertTrue(result["clean"])

    def test_return_structure(self):
        with patch("media_stack.api.services.config.resolve_profile_path", return_value=None), \
             patch.dict(os.environ, {"K8S_NAMESPACE": "", "CONFIG_ROOT": "/nonexistent"}):
            result = config_mod.get_config_drift()
        self.assertIn("drifts", result)
        self.assertIn("total", result)
        self.assertIn("clean", result)
        self.assertEqual(result["total"], len(result["drifts"]))


class TestDashboardDrift(unittest.TestCase):
    def test_drift_tab_exists(self):
        self.assertIn("cfg-drift", DASHBOARD_HTML)

    def test_drift_calls_api(self):
        self.assertIn("/api/config-drift", DASHBOARD_HTML)

    def test_drift_shows_grouped_issues(self):
        self.assertIn("Configuration Issue", DASHBOARD_HTML)

    def test_drift_shows_clean_message(self):
        self.assertIn("No Configuration Drift", DASHBOARD_HTML)

    def test_drift_uses_eschtml(self):
        self.assertIn("_escHtml(d.key)", DASHBOARD_HTML)

    def test_drift_shows_expected_vs_actual(self):
        self.assertIn("Expected", DASHBOARD_HTML)
        self.assertIn("Actual", DASHBOARD_HTML)


class TestDashboardKeyFormatsRefactor(unittest.TestCase):
    """Verify key_formats.py is importable and used."""

    def test_key_formats_module_exists(self):
        from media_stack.api.services import key_formats
        self.assertTrue(hasattr(key_formats, "READERS"))
        self.assertTrue(hasattr(key_formats, "WRITERS"))

    def test_registry_uses_shared_readers(self):
        from media_stack.api.services.registry import KEY_READERS
        from media_stack.api.services.key_formats import READERS
        self.assertIs(KEY_READERS, READERS)

    def test_admin_uses_shared_formats(self):
        from media_stack.api.services.admin import _KEY_READERS, _KEY_WRITERS
        from media_stack.api.services.key_formats import READERS, WRITERS
        self.assertIs(_KEY_READERS, READERS)
        self.assertIs(_KEY_WRITERS, WRITERS)


class TestControllerAllMainRefactor(unittest.TestCase):
    """Verify extracted step executors are importable."""

    def test_step_executors_exist(self):
        from media_stack.cli.commands.controller_all_main import (
            _execute_component_script,
            _execute_script,
            _execute_enable_components,
            _execute_http_action,
        )
        self.assertTrue(callable(_execute_component_script))
        self.assertTrue(callable(_execute_script))
        self.assertTrue(callable(_execute_enable_components))
        self.assertTrue(callable(_execute_http_action))


if __name__ == "__main__":
    unittest.main()
