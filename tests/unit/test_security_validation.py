"""Tests for security: input validation, service names, webhook URLs, env vars, path traversal."""

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.api.services.ops import get_snapshot_detail, diff_snapshots  # noqa: E402


class _MockHandler:
    """Minimal mock for ControllerAPIHandler."""

    def __init__(self, path="", body=None):
        self.path = path
        self._body = body or {}
        self._responses = []
        self.state = MagicMock()
        self.state.webhook_urls = set()
        self.action_trigger = None
        # Attributes read by the global preflight (rate limit + CSRF).
        # Default: localhost client + no cookie (API-client path, CSRF
        # smart-default lets it through).
        self.client_address = ("127.0.0.1", 0)
        self.headers = MagicMock()
        self.headers.get.side_effect = lambda name, default="": default

    def _read_json_body(self):
        return self._body

    def _json_response(self, status, body):
        self._responses.append((status, body))

    @property
    def last_status(self):
        return self._responses[-1][0] if self._responses else None

    @property
    def last_body(self):
        return self._responses[-1][1] if self._responses else None


class TestServiceNameValidation(unittest.TestCase):
    def _dispatch(self, handler):
        from media_stack.api.handlers_post import handle
        handle(handler)

    def test_unknown_service_returns_400(self):
        h = _MockHandler("/api/restart/totally-fake-service")
        self._dispatch(h)
        self.assertEqual(h.last_status, 400)
        self.assertIn("Unknown service", h.last_body.get("error", ""))

    def test_known_service_accepted(self):
        from media_stack.api.services.registry import SERVICES
        if not SERVICES:
            self.skipTest("No services in registry")
        svc_id = SERVICES[0].id
        h = _MockHandler(f"/api/restart/{svc_id}")
        with patch("media_stack.api.services.admin.restart_service", return_value={"status": "ok"}):
            self._dispatch(h)
        self.assertEqual(h.last_status, 200)

    def test_controller_service_allowed(self):
        h = _MockHandler("/api/restart/controller")
        with patch("media_stack.api.services.admin.restart_service", return_value={"status": "ok"}):
            self._dispatch(h)
        self.assertEqual(h.last_status, 200)

    def test_unknown_service_lists_known(self):
        h = _MockHandler("/api/restart/zzz-fake")
        self._dispatch(h)
        self.assertIn("known", h.last_body)


class TestWebhookURLValidation(unittest.TestCase):
    def _dispatch(self, handler):
        from media_stack.api.handlers_post import handle
        handle(handler)

    def test_valid_http_url(self):
        h = _MockHandler("/webhooks", {"url": "http://example.com/hook"})
        h.state.webhook_urls = set()
        self._dispatch(h)
        self.assertEqual(h.last_status, 200)
        self.assertIn("http://example.com/hook", h.state.webhook_urls)

    def test_valid_https_url(self):
        h = _MockHandler("/webhooks", {"url": "https://example.com/hook"})
        h.state.webhook_urls = set()
        self._dispatch(h)
        self.assertEqual(h.last_status, 200)

    def test_ftp_url_rejected(self):
        h = _MockHandler("/webhooks", {"url": "ftp://example.com"})
        self._dispatch(h)
        self.assertEqual(h.last_status, 400)

    def test_javascript_url_rejected(self):
        h = _MockHandler("/webhooks", {"url": "javascript:alert(1)"})
        self._dispatch(h)
        self.assertEqual(h.last_status, 400)

    def test_empty_netloc_rejected(self):
        h = _MockHandler("/webhooks", {"url": "http://"})
        self._dispatch(h)
        self.assertEqual(h.last_status, 400)

    def test_no_scheme_rejected(self):
        h = _MockHandler("/webhooks", {"url": "example.com/hook"})
        self._dispatch(h)
        self.assertEqual(h.last_status, 400)


class TestEnvVarPrefixValidation(unittest.TestCase):
    def _dispatch(self, handler):
        from media_stack.api.handlers_post import handle
        handle(handler)

    def test_platform_prefix_accepted(self):
        h = _MockHandler("/api/envvars", {"key": "STACK_MY_VAR", "value": "test"})
        self._dispatch(h)
        self.assertEqual(h.last_status, 200)

    def test_disallowed_prefix_rejected(self):
        h = _MockHandler("/api/envvars", {"key": "PATH", "value": "/evil"})
        self._dispatch(h)
        self.assertEqual(h.last_status, 400)

    def test_empty_key_rejected(self):
        h = _MockHandler("/api/envvars", {"key": "", "value": "test"})
        self._dispatch(h)
        self.assertEqual(h.last_status, 400)

    def test_controller_prefix_allowed(self):
        h = _MockHandler("/api/envvars", {"key": "CONTROLLER_PORT", "value": "9100"})
        self._dispatch(h)
        self.assertEqual(h.last_status, 200)

    def test_bootstrap_prefix_allowed(self):
        h = _MockHandler("/api/envvars", {"key": "BOOTSTRAP_MODE", "value": "full"})
        self._dispatch(h)
        self.assertEqual(h.last_status, 200)


class TestSnapshotPathTraversal(unittest.TestCase):
    def test_dotdot_in_detail(self):
        result = get_snapshot_detail("../../etc/passwd")
        self.assertIn("error", result)
        self.assertIn("Invalid", result["error"])

    def test_slash_in_detail(self):
        result = get_snapshot_detail("foo/bar.json")
        self.assertIn("error", result)

    def test_backslash_in_detail(self):
        result = get_snapshot_detail("foo\\bar.json")
        self.assertIn("error", result)

    def test_dotdot_in_diff_a(self):
        result = diff_snapshots("../evil", "snapshot-ok.json")
        self.assertIn("error", result)

    def test_dotdot_in_diff_b(self):
        result = diff_snapshots("snapshot-ok.json", "../evil")
        self.assertIn("error", result)

    def test_valid_snapshot_name_passes_validation(self):
        # Will fail with "not found" not "Invalid" — that means validation passed
        result = get_snapshot_detail("snapshot-20260409T120000.json")
        self.assertNotIn("Invalid", result.get("error", ""))


if __name__ == "__main__":
    unittest.main()
