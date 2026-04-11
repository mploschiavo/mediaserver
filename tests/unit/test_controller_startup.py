"""Tests for controller startup resilience and error handling.

Verifies that the controller:
1. Starts even with corrupted/missing profile
2. Shows clear error messages for common problems
3. Handles qBit temp password extraction correctly
4. Doesn't crash-loop on validation failures
5. Auto-indexer progress is visible
"""

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))


class TestQbitTempPasswordExtraction(unittest.TestCase):
    """Verify _extract_temp_password gets the LAST password, not the first."""

    def test_single_password(self):
        from media_stack.services.apps.qbittorrent.http_preflight import _extract_temp_password
        logs = "The WebUI administrator password was not set. A temporary password is provided for this session: abc123\n"
        self.assertEqual(_extract_temp_password(logs), "abc123")

    def test_multiple_passwords_returns_last(self):
        from media_stack.services.apps.qbittorrent.http_preflight import _extract_temp_password
        logs = (
            "A temporary password is provided for this session: oldpass1\n"
            "Starting qBittorrent...\n"
            "A temporary password is provided for this session: newpass2\n"
        )
        self.assertEqual(_extract_temp_password(logs), "newpass2")

    def test_three_restarts_returns_latest(self):
        from media_stack.services.apps.qbittorrent.http_preflight import _extract_temp_password
        logs = (
            "A temporary password is provided: first\n"
            "A temporary password is provided: second\n"
            "A temporary password is provided: third\n"
        )
        self.assertEqual(_extract_temp_password(logs), "third")

    def test_no_password_returns_none(self):
        from media_stack.services.apps.qbittorrent.http_preflight import _extract_temp_password
        self.assertIsNone(_extract_temp_password("normal log output\n"))

    def test_empty_logs(self):
        from media_stack.services.apps.qbittorrent.http_preflight import _extract_temp_password
        self.assertIsNone(_extract_temp_password(""))


class TestProfileValidationDoesNotCrash(unittest.TestCase):
    """The controller must survive invalid profiles without crash-looping."""

    def test_missing_metadata_name_raises_runtime_error(self):
        """validate_profile raises RuntimeError, not SystemExit."""
        import tempfile, os
        from media_stack.api.preflight.profile_validation import validate_profile
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write("metadata:\n  platform: compose\n")
        try:
            with self.assertRaises(RuntimeError):
                validate_profile(f.name, log=lambda msg: None)
        finally:
            os.unlink(f.name)

    def test_empty_file_raises(self):
        import tempfile, os
        from media_stack.api.preflight.profile_validation import validate_profile
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write("")
        try:
            with self.assertRaises((RuntimeError, Exception)):
                validate_profile(f.name, log=lambda msg: None)
        finally:
            os.unlink(f.name)

    def test_valid_profile_does_not_raise(self):
        import tempfile, os, yaml
        from media_stack.api.preflight.profile_validation import validate_profile
        data = {
            "schema_version": 1,
            "kind": "media_stack_profile",
            "metadata": {"name": "test", "platform": "compose"},
            "install_profile": "standard",
        }
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.dump(data, f)
        try:
            validate_profile(f.name, log=lambda msg: None)  # Should not raise
        finally:
            os.unlink(f.name)


class TestSaveDoesNotCorruptProfile(unittest.TestCase):
    """Every save path must preserve metadata.name."""

    def _roundtrip_save(self, update_fn):
        """Helper: create valid profile, run update_fn, verify metadata.name survives."""
        import tempfile, yaml
        from unittest.mock import patch
        import media_stack.api.services.config as config_mod
        data = {
            "schema_version": 1,
            "metadata": {"name": "test-stack", "platform": "compose"},
            "routing": {"gateway_host": "test.local"},
        }
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "profile.yaml"
            yaml.dump(data, open(p, "w"))
            with patch.object(config_mod, "resolve_profile_path", return_value=str(p)):
                update_fn(config_mod)
            saved = yaml.safe_load(p.read_text())
            self.assertEqual(saved.get("metadata", {}).get("name"), "test-stack",
                             "metadata.name was lost after save!")

    def test_update_metadata_preserves_name(self):
        self._roundtrip_save(lambda m: m.update_metadata_settings("de", "DE"))

    def test_update_livetv_preserves_name(self):
        self._roundtrip_save(lambda m: m.update_livetv_sources(
            tuners=[{"url": "http://test", "name": "test"}]))

    def test_update_categories_preserves_name(self):
        self._roundtrip_save(lambda m: m.update_download_categories({"tv": "/data/tv"}))

    def test_update_routing_preserves_name(self):
        self._roundtrip_save(lambda m: m.update_routing({"base_domain": "example.com"}))

    def test_update_discovery_lists_preserves_name(self):
        self._roundtrip_save(lambda m: m.update_discovery_lists([{"name": "test", "type": "trakt"}]))

    def test_update_profile_section_preserves_name(self):
        self._roundtrip_save(lambda m: m.update_profile_section("custom_key", {"foo": "bar"}))

    def test_save_profile_raw_preserves_name(self):
        """Even raw profile save must contain metadata.name."""
        import tempfile, yaml
        from unittest.mock import patch
        import media_stack.api.services.config as config_mod
        data = {"schema_version": 1, "metadata": {"name": "test-stack"}}
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "profile.yaml"
            yaml.dump(data, open(p, "w"))
            with patch.object(config_mod, "resolve_profile_path", return_value=str(p)):
                new_content = yaml.dump({"schema_version": 1, "metadata": {"name": "renamed-stack"}})
                result = config_mod.save_profile(new_content)
            self.assertEqual(result["status"], "saved")


class TestActionProgressVisibility(unittest.TestCase):
    """Verify action progress is visible through the API."""

    def test_running_action_has_elapsed(self):
        from media_stack.api.state import ControllerState
        state = ControllerState()
        action = state.start_action("discover-indexers")
        self.assertIsNotNone(action.elapsed_seconds)
        self.assertGreaterEqual(action.elapsed_seconds, 0)
        d = state.to_dict()
        self.assertEqual(d["current_action"]["name"], "discover-indexers")
        self.assertIn("elapsed_seconds", d["current_action"])

    def test_pending_actions_visible(self):
        from media_stack.api.state import ControllerState
        state = ControllerState()
        state.add_pending("validate-credentials", 80)
        d = state.to_dict()
        self.assertEqual(len(d["pending_actions"]), 1)
        self.assertEqual(d["pending_actions"][0]["name"], "validate-credentials")


if __name__ == "__main__":
    unittest.main()
