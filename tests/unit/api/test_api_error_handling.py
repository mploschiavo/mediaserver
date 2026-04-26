"""Tests for API error handling: JSON parsing, health probes, webhooks."""

import io
import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.api.services.health import _probe_login  # noqa: E402


class TestReadJsonBody(unittest.TestCase):
    """Test _read_json_body error handling in server.py."""

    def _make_handler(self, body_bytes=b"", content_length=None):
        from media_stack.api.server import ControllerAPIHandler
        handler = MagicMock(spec=ControllerAPIHandler)
        handler.rfile = io.BytesIO(body_bytes)
        handler.headers = MagicMock()
        if content_length is not None:
            handler.headers.get.return_value = str(content_length)
        else:
            handler.headers.get.return_value = str(len(body_bytes))
        handler.command = "POST"
        handler.path = "/test"
        # Call the real method
        return handler

    def test_valid_json(self):
        from media_stack.api.server import ControllerAPIHandler
        body = json.dumps({"key": "value"}).encode()
        handler = self._make_handler(body, len(body))
        result = ControllerAPIHandler._read_json_body(handler)
        self.assertEqual(result, {"key": "value"})

    def test_empty_body(self):
        from media_stack.api.server import ControllerAPIHandler
        handler = self._make_handler(b"", 0)
        result = ControllerAPIHandler._read_json_body(handler)
        self.assertEqual(result, {})

    def test_malformed_json_returns_empty(self):
        from media_stack.api.server import ControllerAPIHandler
        body = b"{bad json"
        handler = self._make_handler(body, len(body))
        result = ControllerAPIHandler._read_json_body(handler)
        self.assertEqual(result, {})

    def test_zero_content_length(self):
        from media_stack.api.server import ControllerAPIHandler
        handler = self._make_handler(b"", 0)
        result = ControllerAPIHandler._read_json_body(handler)
        self.assertEqual(result, {})


class TestProbeLogin(unittest.TestCase):
    @patch("urllib.request.urlopen")
    def test_json_credentials_ok(self, mock_open):
        resp = MagicMock()
        resp.read.return_value = json.dumps({"AccessToken": "abc123"}).encode()
        resp.__enter__ = lambda s: s
        resp.__exit__ = MagicMock(return_value=False)
        mock_open.return_value = resp
        result = _probe_login("host", 8096, "/auth", "json_credentials", "admin", "pass")
        self.assertEqual(result, "ok")

    @patch("urllib.request.urlopen")
    def test_json_credentials_fail_no_token(self, mock_open):
        resp = MagicMock()
        resp.read.return_value = json.dumps({}).encode()
        resp.__enter__ = lambda s: s
        resp.__exit__ = MagicMock(return_value=False)
        mock_open.return_value = resp
        result = _probe_login("host", 8096, "/auth", "json_credentials", "admin", "wrong")
        self.assertEqual(result, "fail")

    @patch("urllib.request.urlopen")
    def test_basic_mode_ok(self, mock_open):
        resp = MagicMock()
        resp.__enter__ = lambda s: s
        resp.__exit__ = MagicMock(return_value=False)
        mock_open.return_value = resp
        result = _probe_login("host", 8080, "/", "basic", "admin", "pass")
        self.assertEqual(result, "ok")

    @patch("urllib.request.urlopen")
    def test_form_mode_ok(self, mock_open):
        resp = MagicMock()
        resp.read.return_value = b"Ok."
        resp.url = "http://host:8080/dashboard"
        resp.__enter__ = lambda s: s
        resp.__exit__ = MagicMock(return_value=False)
        mock_open.return_value = resp
        result = _probe_login("host", 8080, "/login", "form", "admin", "pass")
        self.assertEqual(result, "ok")

    @patch("urllib.request.urlopen")
    def test_form_mode_fail_response(self, mock_open):
        resp = MagicMock()
        resp.read.return_value = b"Fails."
        resp.url = "http://host:8080/login"
        resp.__enter__ = lambda s: s
        resp.__exit__ = MagicMock(return_value=False)
        mock_open.return_value = resp
        result = _probe_login("host", 8080, "/login", "form", "admin", "wrong")
        self.assertEqual(result, "fail")

    def test_http_401_returns_fail(self):
        import urllib.error
        with patch("urllib.request.urlopen", side_effect=urllib.error.HTTPError(
                "http://test", 401, "Unauthorized", {}, None)):
            result = _probe_login("host", 8080, "/", "basic", "admin", "wrong")
        self.assertEqual(result, "fail")

    def test_http_500_returns_error(self):
        import urllib.error
        with patch("urllib.request.urlopen", side_effect=urllib.error.HTTPError(
                "http://test", 500, "Server Error", {}, None)):
            result = _probe_login("host", 8080, "/", "basic", "admin", "pass")
        self.assertEqual(result, "error")

    def test_url_error_returns_error(self):
        import urllib.error
        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("refused")):
            result = _probe_login("host", 8080, "/", "basic", "admin", "pass")
        self.assertEqual(result, "error")

    def test_timeout_returns_error(self):
        with patch("urllib.request.urlopen", side_effect=TimeoutError("timed out")):
            result = _probe_login("host", 8080, "/", "basic", "admin", "pass")
        self.assertEqual(result, "error")

    def test_unknown_mode_returns_na(self):
        result = _probe_login("host", 8080, "/", "unknownmode", "admin", "pass")
        self.assertEqual(result, "n/a")


class TestFireWebhooks(unittest.TestCase):
    def test_empty_urls_noop(self):
        from media_stack.api.webhooks import _fire_webhooks
        from media_stack.api.state import ControllerState
        state = ControllerState()
        state.webhook_urls = []
        _fire_webhooks(state, "test", {})  # No exception

    @patch("urllib.request.urlopen")
    def test_successful_delivery(self, mock_open):
        from media_stack.api.webhooks import _fire_webhooks
        from media_stack.api.state import ControllerState
        state = ControllerState()
        state.webhook_urls = ["http://example.com/hook"]
        _fire_webhooks(state, "test", {"key": "val"})
        mock_open.assert_called_once()

    @patch("urllib.request.urlopen", side_effect=ConnectionRefusedError("refused"))
    def test_failed_delivery_does_not_raise(self, _):
        from media_stack.api.webhooks import _fire_webhooks
        from media_stack.api.state import ControllerState
        state = ControllerState()
        state.webhook_urls = ["http://example.com/hook"]
        _fire_webhooks(state, "test", {})  # Should not raise

    @patch("urllib.request.urlopen")
    def test_multiple_urls(self, mock_open):
        from media_stack.api.webhooks import _fire_webhooks
        from media_stack.api.state import ControllerState
        state = ControllerState()
        state.webhook_urls = ["http://a.com", "http://b.com"]
        _fire_webhooks(state, "test", {})
        self.assertEqual(mock_open.call_count, 2)


if __name__ == "__main__":
    unittest.main()
