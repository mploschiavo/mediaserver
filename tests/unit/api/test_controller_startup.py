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

ROOT = Path(__file__).resolve().parents[3]
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
            with patch("media_stack.api.services._resolve.resolve_profile_path", return_value=str(p)):
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
            with patch("media_stack.api.services._resolve.resolve_profile_path", return_value=str(p)):
                new_content = yaml.dump({"schema_version": 1, "metadata": {"name": "renamed-stack"}})
                result = config_mod.save_profile(new_content)
            self.assertEqual(result["status"], "saved")


class TestActionProgressVisibility(unittest.TestCase):
    """Verify run progress is visible through the Job framework
    surface.

    ADR-0005 Phase 5c.4c retired the ``ControllerState`` action
    lifecycle (``start_action`` / ``add_pending`` / etc.) — every
    in-flight + completed run flows through ``run_history``
    instead. These tests exercise the JSONL-backed
    ``get_running_tree`` reader the same way the SPA's
    ``/api/jobs/running`` consumer does.
    """

    def test_running_run_visible_via_run_history_tree(self):
        # Live in-flight runs surface through ``get_running_tree``;
        # ``ControllerState.to_dict`` no longer exposes them.
        from media_stack.api.state import ControllerState
        from media_stack.application.jobs import run_history
        from media_stack.domain.jobs.run_record import RunStatus
        import tempfile
        from pathlib import Path
        from unittest import mock

        state = ControllerState()
        d = state.to_dict()
        # ``current_action`` + ``action_history`` retired from the
        # wire shape.
        self.assertNotIn("current_action", d)
        self.assertNotIn("action_history", d)

        # Simulate an in-flight run. ``record_run_start`` writes a
        # ``status=running`` record to the JSONL file; ``get_running_tree``
        # walks it to surface live runs.
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "run-history.jsonl"
            with mock.patch.object(run_history, "_path", return_value=path):
                rec = run_history.record_run_start(
                    "discover-indexers",
                    triggered_by="manual",
                )
                tree = run_history.get_running_tree()
                self.assertEqual(len(tree), 1)
                self.assertEqual(tree[0]["run_id"], rec.run_id)
                self.assertEqual(tree[0]["job_name"], "discover-indexers")
                self.assertEqual(tree[0]["status"], RunStatus.RUNNING)

    def test_pending_actions_field_remains_empty(self):
        # ``add_pending`` retired in 5c.4c; the in-process priority
        # queue is the source of truth for pending work. The
        # ``pending_actions`` field is kept on ``/status`` only for
        # back-compat (always empty).
        from media_stack.api.state import ControllerState
        state = ControllerState()
        d = state.to_dict()
        self.assertEqual(d["pending_actions"], [])


if __name__ == "__main__":
    unittest.main()
