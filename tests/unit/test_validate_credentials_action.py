"""Tests for the validate-credentials action handler."""

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.cli.commands.action_handlers import action_validate_credentials  # noqa: E402


class TestActionValidateCredentials(unittest.TestCase):
    """Tests for action_validate_credentials() handler."""

    @patch("media_stack.api.services.health.probe_credentials")
    @patch("media_stack.cli.commands.action_handlers.runtime_platform")
    def test_all_pass(self, mock_rp, mock_probe):
        mock_probe.return_value = {
            "credentials": {"sonarr": "ok", "radarr": "ok"},
            "ok": 2, "total": 2,
        }
        action_validate_credentials()
        logs = [call.args[0] for call in mock_rp.log.call_args_list]
        self.assertTrue(any("[OK] All 2" in m for m in logs))

    @patch("media_stack.api.services.admin.reset_password",
           return_value={"status": "updated", "services": ["radarr"], "errors": [], "restarted": []})
    @patch("media_stack.api.services.health.probe_credentials")
    @patch("media_stack.cli.commands.action_handlers.runtime_platform")
    def test_some_fail(self, mock_rp, mock_probe, mock_reset):
        mock_probe.side_effect = [
            {"credentials": {"sonarr": "ok", "radarr": "fail"}, "ok": 1, "total": 2},
            {"credentials": {"radarr": "ok"}, "ok": 1, "total": 1},
        ]
        action_validate_credentials()
        logs = [call.args[0] for call in mock_rp.log.call_args_list]
        # radarr failed initially, then auto-synced
        self.assertTrue(any("radarr" in m and "password sync" in m for m in logs))
        mock_reset.assert_called_once()

    @patch("media_stack.api.services.health.probe_credentials")
    @patch("media_stack.cli.commands.action_handlers.runtime_platform")
    def test_error_services(self, mock_rp, mock_probe):
        mock_probe.return_value = {
            "credentials": {"sonarr": "error"},
            "ok": 0, "total": 1,
        }
        action_validate_credentials()
        logs = [call.args[0] for call in mock_rp.log.call_args_list]
        self.assertTrue(any("unreachable" in m for m in logs))

    @patch("media_stack.api.services.health.probe_credentials")
    @patch("media_stack.cli.commands.action_handlers.runtime_platform")
    def test_no_services(self, mock_rp, mock_probe):
        mock_probe.return_value = {"credentials": {}, "ok": 0, "total": 0}
        action_validate_credentials()
        logs = [call.args[0] for call in mock_rp.log.call_args_list]
        self.assertTrue(any("No services" in m for m in logs))

    @patch("media_stack.api.services.health.probe_credentials")
    @patch("media_stack.cli.commands.action_handlers.runtime_platform")
    def test_logs_per_service_ok(self, mock_rp, mock_probe):
        mock_probe.return_value = {
            "credentials": {"sonarr": "ok"},
            "ok": 1, "total": 1,
        }
        action_validate_credentials()
        logs = [call.args[0] for call in mock_rp.log.call_args_list]
        self.assertTrue(any("[CRED] sonarr: passed" in m for m in logs))

    @patch("media_stack.api.services.admin.reset_password",
           return_value={"status": "updated", "services": [], "errors": ["radarr: fail"], "restarted": []})
    @patch("media_stack.api.services.health.probe_credentials")
    @patch("media_stack.cli.commands.action_handlers.runtime_platform")
    def test_logs_per_service_fail(self, mock_rp, mock_probe, mock_reset):
        mock_probe.side_effect = [
            {"credentials": {"radarr": "fail"}, "ok": 0, "total": 1},
            {"credentials": {"radarr": "fail"}, "ok": 0, "total": 1},
        ]
        action_validate_credentials()
        logs = [call.args[0] for call in mock_rp.log.call_args_list]
        self.assertTrue(any("radarr" in m and "password sync" in m for m in logs))

    @patch("media_stack.api.services.health.probe_credentials")
    @patch("media_stack.cli.commands.action_handlers.runtime_platform")
    def test_does_not_raise(self, mock_rp, mock_probe):
        mock_probe.side_effect = Exception("network error")
        with self.assertRaises(Exception):
            action_validate_credentials()

    @patch("media_stack.api.services.admin.reset_password",
           return_value={"status": "updated", "services": [], "errors": ["radarr: no API key"], "restarted": []})
    @patch("media_stack.api.services.health.probe_credentials")
    @patch("media_stack.cli.commands.action_handlers.runtime_platform")
    def test_mixed_statuses(self, mock_rp, mock_probe, mock_reset):
        mock_probe.side_effect = [
            {"credentials": {"sonarr": "ok", "radarr": "fail", "lidarr": "error"}, "ok": 1, "total": 3},
            {"credentials": {"radarr": "fail"}, "ok": 0, "total": 1},
        ]
        action_validate_credentials()
        logs = [call.args[0] for call in mock_rp.log.call_args_list]
        self.assertTrue(any("sonarr: passed" in m for m in logs))
        self.assertTrue(any("radarr" in m and "password sync" in m for m in logs))
        self.assertTrue(any("lidarr: unreachable" in m for m in logs))

    @patch("media_stack.api.services.health.probe_credentials")
    @patch("media_stack.cli.commands.action_handlers.runtime_platform")
    def test_action_in_known_actions(self, mock_rp, mock_probe):
        from media_stack.api.handlers_post import KNOWN_ACTIONS
        self.assertIn("validate-credentials", KNOWN_ACTIONS)

    @patch("media_stack.api.services.health.probe_credentials")
    @patch("media_stack.cli.commands.action_handlers.runtime_platform")
    def test_action_has_priority(self, mock_rp, mock_probe):
        from media_stack.api.server import ACTION_PRIORITY
        self.assertIn("validate-credentials", ACTION_PRIORITY)
        self.assertEqual(ACTION_PRIORITY["validate-credentials"], 80)


if __name__ == "__main__":
    unittest.main()
