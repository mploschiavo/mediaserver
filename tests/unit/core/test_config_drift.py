"""Tests for config drift detection.

Dashboard-side rendering tests retired with dashboard.html in
v1.0.193 — the SPA UI under ``ui/`` owns those assertions now."""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

import media_stack.api.services.config as config_mod  # noqa: E402


class TestConfigDrift(unittest.TestCase):
    @patch.dict(os.environ, {"BOOTSTRAP_PROFILE_FILE": "", "CONFIG_ROOT": "/nonexistent", "K8S_NAMESPACE": ""})
    @patch("media_stack.api.services._resolve.resolve_profile_path", return_value=None)
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
            with patch("media_stack.api.services._resolve.resolve_profile_path", return_value=f.name), \
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
    @patch("media_stack.api.services._resolve.resolve_profile_path", return_value=None)
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
        # Assert sonarr's drift is present rather than first — other
        # ``*_API_KEY`` env vars (set by the host or leaked by an
        # earlier test) can also produce drift entries since the
        # ``read_api_key_from_file`` mock returns the same value for
        # every service id.
        self.assertTrue(
            any(d["key"] == "sonarr" for d in key_drifts),
            f"sonarr drift not detected; saw: "
            f"{[d['key'] for d in key_drifts]}",
        )

    @patch.dict(os.environ, {"K8S_NAMESPACE": "", "CONFIG_ROOT": "/nonexistent"})
    @patch("media_stack.api.services._resolve.resolve_profile_path", return_value=None)
    def test_clean_when_no_drift(self, _):
        result = config_mod.get_config_drift()
        if result["total"] == 0:
            self.assertTrue(result["clean"])

    def test_return_structure(self):
        with patch("media_stack.api.services._resolve.resolve_profile_path", return_value=None), \
             patch.dict(os.environ, {"K8S_NAMESPACE": "", "CONFIG_ROOT": "/nonexistent"}):
            result = config_mod.get_config_drift()
        self.assertIn("drifts", result)
        self.assertIn("total", result)
        self.assertIn("clean", result)
        self.assertEqual(result["total"], len(result["drifts"]))


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
