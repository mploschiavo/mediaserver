"""Tests for user-facing settings persistence.

Every setting a user can change via the dashboard/API MUST survive
a controller restart. This test file acts as a registry of all
mutable settings and verifies each one is persisted.

When adding a new user-facing setting:
1. Add it to PERSISTED_SETTINGS below
2. Run this test — it will fail if persistence isn't wired up
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.api.state import ControllerState


# ---------------------------------------------------------------------------
# Registry of all user-facing settings that MUST persist across restarts.
# Each entry: (config_key, test_value, description)
# ---------------------------------------------------------------------------

PERSISTED_SETTINGS = [
    ("auto_download_content", True, "Auto-downloads toggle"),
    ("preconfigure_api_keys", True, "Pre-configure API keys toggle"),
    ("apply_initial_preferences", True, "Apply initial preferences toggle"),
    ("_webhook_urls", ["http://example.com/hook"], "Webhook notification URLs"),
    ("_log_level", "DEBUG", "Controller log level"),
]


class TestAllSettingsPersist(unittest.TestCase):
    """Every setting in PERSISTED_SETTINGS must survive a simulated restart."""

    def test_each_setting_survives_restart(self):
        """Write each setting, create a new state, verify it's restored."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_file = str(Path(tmpdir) / "runtime-config.json")

            for key, value, desc in PERSISTED_SETTINGS:
                # Write
                s1 = ControllerState()
                s1._RUNTIME_CONFIG_FILE = config_file
                s1.update_config({key: value})

                # Verify written to disk
                self.assertTrue(
                    Path(config_file).is_file(),
                    f"Setting '{key}' ({desc}) was not written to disk",
                )
                on_disk = json.loads(Path(config_file).read_text())
                self.assertIn(
                    key, on_disk,
                    f"Setting '{key}' ({desc}) is missing from persisted file",
                )

                # Restore in a new state (simulates restart)
                s2 = ControllerState()
                s2._RUNTIME_CONFIG_FILE = config_file
                s2.load_persisted_config()
                restored = s2.runtime_config.get(key)
                self.assertEqual(
                    restored, value,
                    f"Setting '{key}' ({desc}) did not survive restart: "
                    f"expected {value!r}, got {restored!r}",
                )

    def test_webhook_urls_restored_to_state(self):
        """Webhook URLs must be restored to state.webhook_urls, not just runtime_config."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_file = str(Path(tmpdir) / "runtime-config.json")

            s1 = ControllerState()
            s1._RUNTIME_CONFIG_FILE = config_file
            s1.webhook_urls = ["http://a.com", "http://b.com"]
            s1.update_config({"_webhook_urls": list(s1.webhook_urls)})

            s2 = ControllerState()
            s2._RUNTIME_CONFIG_FILE = config_file
            s2.load_persisted_config()
            self.assertEqual(sorted(s2.webhook_urls), ["http://a.com", "http://b.com"])

    def test_log_level_restored_on_load(self):
        """Log level must be applied to runtime_platform on load."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_file = str(Path(tmpdir) / "runtime-config.json")

            s1 = ControllerState()
            s1._RUNTIME_CONFIG_FILE = config_file
            s1.update_config({"_log_level": "DEBUG"})

            with mock.patch("media_stack.services.runtime_platform.set_log_level") as mock_set:
                s2 = ControllerState()
                s2._RUNTIME_CONFIG_FILE = config_file
                s2.load_persisted_config()
                mock_set.assert_called_with("DEBUG")

    def test_multiple_settings_persisted_atomically(self):
        """Multiple settings updated together must all persist."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_file = str(Path(tmpdir) / "runtime-config.json")

            s1 = ControllerState()
            s1._RUNTIME_CONFIG_FILE = config_file
            s1.update_config({
                "auto_download_content": True,
                "_log_level": "WARN",
                "_webhook_urls": ["http://test.com"],
            })

            s2 = ControllerState()
            s2._RUNTIME_CONFIG_FILE = config_file
            s2.load_persisted_config()
            self.assertTrue(s2.runtime_config["auto_download_content"])
            self.assertEqual(s2.runtime_config["_log_level"], "WARN")
            self.assertEqual(s2.runtime_config["_webhook_urls"], ["http://test.com"])

    def test_persisted_settings_registry_is_not_empty(self):
        """Guard against accidentally emptying the registry."""
        self.assertGreater(
            len(PERSISTED_SETTINGS), 3,
            "PERSISTED_SETTINGS registry should have at least 3 entries. "
            "If you removed entries, you likely have a settings persistence gap.",
        )


class TestRuntimeConfigFileIntegrity(unittest.TestCase):
    """Test the persistence mechanism itself."""

    def test_corrupt_file_does_not_crash(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_file = Path(tmpdir) / "runtime-config.json"
            config_file.write_text("not valid json {{{")

            s = ControllerState()
            s._RUNTIME_CONFIG_FILE = str(config_file)
            s.load_persisted_config()  # Should not raise
            self.assertEqual(s.runtime_config, {})

    def test_missing_file_does_not_crash(self):
        s = ControllerState()
        s._RUNTIME_CONFIG_FILE = "/nonexistent/path/config.json"
        s.load_persisted_config()  # Should not raise
        self.assertEqual(s.runtime_config, {})

    def test_update_creates_parent_directories(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_file = str(Path(tmpdir) / "deep" / "nested" / "config.json")
            s = ControllerState()
            s._RUNTIME_CONFIG_FILE = config_file
            s.update_config({"test": True})
            self.assertTrue(Path(config_file).is_file())


if __name__ == "__main__":
    unittest.main()
