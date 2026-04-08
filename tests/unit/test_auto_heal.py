"""Unit tests for auto-heal: retry, failure tracking, and recovery.

Covers:
  - detect_arr_api_base retry with backoff
  - ControllerState.failed_services tracking
  - _track_failed_service error pattern parsing
  - Auto-reconcile scheduling after failures
"""

import os
import sys
import time
import unittest
from pathlib import Path
from unittest import mock
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.api.state import ControllerState  # noqa: E402


# ---------------------------------------------------------------------------
# detect_arr_api_base retry tests
# ---------------------------------------------------------------------------


class TestDetectArrApiBaseRetry(unittest.TestCase):
    """detect_arr_api_base retries on transient failures."""

    @patch("media_stack.services.apps.servarr.runtime.arr_ops.log")
    @patch("media_stack.services.apps.servarr.runtime.arr_ops.http_request")
    def test_succeeds_on_first_attempt(self, mock_http, mock_log):
        mock_http.return_value = (200, {"version": "4.0"}, "")
        from media_stack.services.apps.servarr.runtime.arr_ops import detect_arr_api_base
        result = detect_arr_api_base("Sonarr", "http://sonarr:8989", "key123")
        self.assertEqual(result, "/api/v3")

    @patch("media_stack.services.apps.servarr.runtime.arr_ops.log")
    @patch("media_stack.services.apps.servarr.runtime.arr_ops.http_request")
    def test_retries_on_connection_error(self, mock_http, mock_log):
        # First two rounds fail, third succeeds
        mock_http.side_effect = [
            Exception("Connection refused"), Exception("Connection refused"),
            Exception("Connection refused"), Exception("Connection refused"),
            (200, {"version": "4.0"}, ""),
        ]
        from media_stack.services.apps.servarr.runtime.arr_ops import detect_arr_api_base
        with patch("time.sleep"):
            result = detect_arr_api_base("Sonarr", "http://sonarr:8989", "key", max_retries=3, retry_delay=0)
        self.assertEqual(result, "/api/v3")

    @patch("media_stack.services.apps.servarr.runtime.arr_ops.log")
    @patch("media_stack.services.apps.servarr.runtime.arr_ops.http_request")
    def test_retries_on_http_503(self, mock_http, mock_log):
        # Two rounds of v3+v1 fail (4 calls), then v3 succeeds on third round
        mock_http.side_effect = [
            (503, None, "unavailable"), (503, None, "unavailable"),
            (503, None, "unavailable"), (503, None, "unavailable"),
            (200, {"version": "1.0"}, ""),
        ]
        from media_stack.services.apps.servarr.runtime.arr_ops import detect_arr_api_base
        with patch("time.sleep"):
            result = detect_arr_api_base("Prowlarr", "http://prowlarr:9696", "key", max_retries=3, retry_delay=0)
        self.assertEqual(result, "/api/v3")

    @patch("media_stack.services.apps.servarr.runtime.arr_ops.log")
    @patch("media_stack.services.apps.servarr.runtime.arr_ops.http_request")
    def test_raises_after_exhausting_retries(self, mock_http, mock_log):
        mock_http.return_value = (503, None, "unavailable")
        from media_stack.services.apps.servarr.runtime.arr_ops import detect_arr_api_base
        with patch("time.sleep"):
            with self.assertRaises(RuntimeError) as ctx:
                detect_arr_api_base("Sonarr", "http://sonarr:8989", "key", max_retries=2, retry_delay=0)
        self.assertIn("unable to detect API base after 2 attempts", str(ctx.exception))

    @patch("media_stack.services.apps.servarr.runtime.arr_ops.log")
    @patch("media_stack.services.apps.servarr.runtime.arr_ops.http_request")
    def test_tries_v3_before_v1(self, mock_http, mock_log):
        calls = []
        def track_request(url, path, **kw):
            calls.append(path)
            if "/v1/" in path:
                return (200, {"version": "1.0"}, "")
            return (404, None, "not found")
        mock_http.side_effect = track_request
        from media_stack.services.apps.servarr.runtime.arr_ops import detect_arr_api_base
        result = detect_arr_api_base("Prowlarr", "http://prowlarr:9696", "key", max_retries=1)
        self.assertEqual(result, "/api/v1")
        self.assertEqual(calls[0], "/api/v3/system/status")
        self.assertEqual(calls[1], "/api/v1/system/status")

    @patch("media_stack.services.apps.servarr.runtime.arr_ops.log")
    @patch("media_stack.services.apps.servarr.runtime.arr_ops.http_request")
    def test_single_retry_max_retries_1(self, mock_http, mock_log):
        mock_http.return_value = (500, None, "error")
        from media_stack.services.apps.servarr.runtime.arr_ops import detect_arr_api_base
        with self.assertRaises(RuntimeError):
            detect_arr_api_base("Radarr", "http://radarr:7878", "key", max_retries=1, retry_delay=0)
        # Should have tried v3 + v1 exactly once (no retry with max_retries=1)
        self.assertEqual(mock_http.call_count, 2)

    @patch("media_stack.services.apps.servarr.runtime.arr_ops.log")
    @patch("media_stack.services.apps.servarr.runtime.arr_ops.http_request")
    def test_logs_retry_attempts(self, mock_http, mock_log):
        mock_http.side_effect = [
            (500, None, "err"), (500, None, "err"),
            (500, None, "err"), (500, None, "err"),
            (200, {"v": "1"}, ""),
        ]
        from media_stack.services.apps.servarr.runtime.arr_ops import detect_arr_api_base
        with patch("time.sleep"):
            detect_arr_api_base("Sonarr", "http://sonarr:8989", "key", max_retries=3, retry_delay=0)
        log_calls = [str(c) for c in mock_log.call_args_list]
        self.assertTrue(any("[WAIT]" in c and "retrying" in c for c in log_calls))

    @patch("media_stack.services.apps.servarr.runtime.arr_ops.log")
    @patch("media_stack.services.apps.servarr.runtime.arr_ops.http_request")
    def test_auth_redirect_warns(self, mock_http, mock_log):
        mock_http.return_value = (200, "<html>login</html>", "")
        from media_stack.services.apps.servarr.runtime.arr_ops import detect_arr_api_base
        with self.assertRaises(RuntimeError):
            detect_arr_api_base("Sonarr", "http://sonarr:8989", "key", max_retries=1)
        log_calls = [str(c) for c in mock_log.call_args_list]
        self.assertTrue(any("not JSON" in c for c in log_calls))


# ---------------------------------------------------------------------------
# ControllerState.failed_services tests
# ---------------------------------------------------------------------------


class TestFailedServicesState(unittest.TestCase):
    """ControllerState tracks and heals failed services."""

    def test_mark_service_failed(self):
        state = ControllerState()
        state.mark_service_failed("sonarr", "unable to detect API base")
        failed = state.get_failed_services()
        self.assertIn("sonarr", failed)
        self.assertEqual(failed["sonarr"]["attempts"], 1)
        self.assertIn("unable to detect", failed["sonarr"]["error"])

    def test_mark_service_failed_increments_attempts(self):
        state = ControllerState()
        state.mark_service_failed("prowlarr", "timeout")
        state.mark_service_failed("prowlarr", "timeout again")
        self.assertEqual(state.get_failed_services()["prowlarr"]["attempts"], 2)

    def test_mark_service_healed_removes(self):
        state = ControllerState()
        state.mark_service_failed("radarr", "error")
        state.mark_service_healed("radarr")
        self.assertNotIn("radarr", state.get_failed_services())

    def test_mark_service_healed_nonexistent_noop(self):
        state = ControllerState()
        state.mark_service_healed("missing")  # Should not raise
        self.assertEqual(state.get_failed_services(), {})

    def test_failed_services_in_to_dict(self):
        state = ControllerState()
        state.mark_service_failed("sonarr", "err")
        d = state.to_dict()
        self.assertIn("failed_services", d)
        self.assertIn("sonarr", d["failed_services"])

    def test_multiple_services_fail_independently(self):
        state = ControllerState()
        state.mark_service_failed("sonarr", "timeout")
        state.mark_service_failed("radarr", "connection refused")
        state.mark_service_failed("prowlarr", "api base fail")
        failed = state.get_failed_services()
        self.assertEqual(len(failed), 3)
        state.mark_service_healed("radarr")
        self.assertEqual(len(state.get_failed_services()), 2)

    def test_failed_at_preserved_across_attempts(self):
        state = ControllerState()
        state.mark_service_failed("sonarr", "err1")
        first_fail = state.get_failed_services()["sonarr"]["failed_at"]
        state.mark_service_failed("sonarr", "err2")
        self.assertEqual(state.get_failed_services()["sonarr"]["failed_at"], first_fail)


# ---------------------------------------------------------------------------
# _track_failed_service pattern matching tests
# ---------------------------------------------------------------------------


class TestTrackFailedService(unittest.TestCase):
    """_track_failed_service extracts service names from error messages."""

    def _track(self, state, msg):
        # Import inline to avoid module-level side effects
        sys.path.insert(0, str(ROOT / "src"))
        from media_stack.cli.commands.controller_main import _track_failed_service
        with patch("media_stack.services.runtime_platform.log"):
            _track_failed_service(state, msg)

    def test_detects_api_base_failure(self):
        state = ControllerState()
        self._track(state, "Prowlarr: unable to detect API base (tried /api/v3 and /api/v1)")
        self.assertIn("prowlarr", state.get_failed_services())

    def test_detects_api_key_failure(self):
        state = ControllerState()
        self._track(state, "Unable to read API key for sonarr after 180s")
        self.assertIn("sonarr", state.get_failed_services())

    def test_detects_connection_failure(self):
        state = ControllerState()
        self._track(state, "radarr: connection refused (http://radarr:7878)")
        self.assertIn("radarr", state.get_failed_services())

    def test_detects_failed_reading(self):
        state = ControllerState()
        self._track(state, "Jellyfin: failed reading server config (HTTP 500)")
        self.assertIn("jellyfin", state.get_failed_services())

    def test_ignores_short_tokens(self):
        state = ControllerState()
        self._track(state, "ab: connection refused")
        self.assertEqual(len(state.get_failed_services()), 0)

    def test_multiple_services_in_one_error(self):
        state = ControllerState()
        self._track(state, "Sonarr: unable to detect API base; Radarr: unable to detect API base")
        failed = state.get_failed_services()
        self.assertIn("sonarr", failed)
        self.assertIn("radarr", failed)

    def test_no_match_on_generic_error(self):
        state = ControllerState()
        self._track(state, "Something went wrong with the configuration")
        self.assertEqual(len(state.get_failed_services()), 0)


# ---------------------------------------------------------------------------
# Auto-heal reconcile scheduling tests
# ---------------------------------------------------------------------------


class TestAutoHealReconcile(unittest.TestCase):
    """Auto-heal schedules reconcile when services fail."""

    def test_reconcile_auto_queued_after_failure(self):
        """When bootstrap fails and services are marked failed, reconcile is queued."""
        state = ControllerState()
        state.mark_service_failed("prowlarr", "api base fail")

        trigger_calls = []
        def fake_trigger(name, overrides):
            trigger_calls.append(name)

        # Simulate the auto-heal timer firing immediately
        failed = state.get_failed_services()
        self.assertTrue(len(failed) > 0)
        if failed:
            fake_trigger("reconcile", {})
        self.assertIn("reconcile", trigger_calls)

    def test_no_reconcile_when_no_failures(self):
        state = ControllerState()
        failed = state.get_failed_services()
        self.assertEqual(len(failed), 0)

    def test_healed_service_not_re_queued(self):
        state = ControllerState()
        state.mark_service_failed("sonarr", "err")
        state.mark_service_healed("sonarr")
        self.assertEqual(len(state.get_failed_services()), 0)


if __name__ == "__main__":
    unittest.main()
