"""Tests for controller_serve.py — HTTP API serve mode."""

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch, call

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.cli.commands.controller_serve import (  # noqa: E402
    _validate_key_against_service,
)


class TestValidateKeyAgainstService(unittest.TestCase):
    """Test the config mount mismatch detector."""

    @patch("media_stack.api.services.registry.SERVICES", [])
    def test_no_canary_service_returns_early(self):
        log = MagicMock()
        _validate_key_against_service({}, "/srv-config", log)
        log.assert_not_called()

    def test_no_discovered_keys_returns_early(self):
        log = MagicMock()
        _validate_key_against_service({}, "/srv-config", log)
        # No keys = no canary = returns early
        log.assert_not_called()

    @patch("urllib.request.urlopen")
    def test_valid_key_no_warning(self, mock_urlopen):
        from media_stack.api.services.registry import SERVICES
        # Find a canary service
        canary = next((s for s in SERVICES if s.api_key_env and s.auth_path and s.api_key_format == "xml"), None)
        if not canary:
            self.skipTest("No canary service in registry")
        resp = MagicMock()
        resp.status = 200
        resp.__enter__ = MagicMock(return_value=resp)
        resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = resp
        log = MagicMock()
        _validate_key_against_service({canary.api_key_env: "testkey"}, "/srv-config", log)
        # Should not warn — 200 means key is valid
        for c in log.call_args_list:
            self.assertNotIn("WARN", str(c))

    @patch("urllib.request.urlopen")
    def test_invalid_key_warns(self, mock_urlopen):
        import urllib.error
        from media_stack.api.services.registry import SERVICES
        canary = next((s for s in SERVICES if s.api_key_env and s.auth_path and s.api_key_format == "xml"), None)
        if not canary:
            self.skipTest("No canary service in registry")
        mock_urlopen.side_effect = urllib.error.HTTPError("url", 401, "Unauthorized", {}, None)
        log = MagicMock()
        _validate_key_against_service({canary.api_key_env: "badkey"}, "/srv-config", log)
        # Should warn about mismatch
        warned = any("[WARN]" in str(c) for c in log.call_args_list)
        self.assertTrue(warned)

    @patch("urllib.request.urlopen", side_effect=Exception("not ready"))
    def test_service_not_ready_no_warning(self, _):
        from media_stack.api.services.registry import SERVICES
        canary = next((s for s in SERVICES if s.api_key_env and s.auth_path and s.api_key_format == "xml"), None)
        if not canary:
            self.skipTest("No canary service in registry")
        log = MagicMock()
        _validate_key_against_service({canary.api_key_env: "key"}, "/srv-config", log)
        # Should not warn — service not ready is expected during bootstrap
        for c in log.call_args_list:
            self.assertNotIn("WARN", str(c))


class TestServeModuleImports(unittest.TestCase):
    """Verify key imports from controller_serve work."""

    def test_run_serve_importable(self):
        from media_stack.cli.commands.controller_serve import _run_serve
        self.assertTrue(callable(_run_serve))

    def test_validate_key_importable(self):
        from media_stack.cli.commands.controller_serve import _validate_key_against_service
        self.assertTrue(callable(_validate_key_against_service))


class TestActionTriggerQueue(unittest.TestCase):
    """Test the action queue prioritization (unit-level, no server)."""

    def test_priority_queue_ordering(self):
        import queue
        q = queue.PriorityQueue()
        q.put((50, 1, "reconcile", {}))
        q.put((10, 2, "bootstrap", {}))
        q.put((80, 3, "validate-credentials", {}))
        # Lowest number = highest priority
        first = q.get()
        self.assertEqual(first[2], "bootstrap")
        second = q.get()
        self.assertEqual(second[2], "reconcile")

    def test_same_priority_fifo(self):
        import queue
        q = queue.PriorityQueue()
        q.put((50, 1, "first", {}))
        q.put((50, 2, "second", {}))
        self.assertEqual(q.get()[2], "first")
        self.assertEqual(q.get()[2], "second")

    def test_action_priority_values(self):
        from media_stack.api.server import ACTION_PRIORITY
        self.assertLess(ACTION_PRIORITY["bootstrap"], ACTION_PRIORITY["reconcile"])
        # validate-credentials runs early (priority 20) so users see logins faster
        self.assertLess(ACTION_PRIORITY["validate-credentials"], ACTION_PRIORITY["reconcile"])

    def test_all_known_actions_have_priority(self):
        from media_stack.api.server import ACTION_PRIORITY
        from media_stack.api.services.known_actions import KNOWN_ACTIONS
        for action in KNOWN_ACTIONS:
            self.assertIn(action, ACTION_PRIORITY, f"{action} missing from ACTION_PRIORITY")


class TestInstrumentedLog(unittest.TestCase):
    """Test log instrumentation feeds SSE buffer."""

    def test_log_feeds_state(self):
        from media_stack.api.state import ControllerState
        state = ControllerState()
        # Simulate _instrumented_log
        state.append_log("test line")
        logs = state.get_logs_since(0)
        self.assertEqual(len(logs), 1)
        self.assertEqual(logs[0][2], "test line")

    def test_multiple_logs(self):
        from media_stack.api.state import ControllerState
        state = ControllerState()
        for i in range(10):
            state.append_log(f"line {i}")
        logs = state.get_logs_since(0)
        self.assertEqual(len(logs), 10)


if __name__ == "__main__":
    unittest.main()
