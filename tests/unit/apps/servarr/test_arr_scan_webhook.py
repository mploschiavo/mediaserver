"""Tests for Sonarr/Radarr → Jellyfin auto-scan:

1. POST /webhooks/arr handler — parses events and triggers Jellyfin scan
2. ensure_arr_scan_webhooks — registers webhook on Sonarr/Radarr
3. configure_auto_scan job — wrapper called during bootstrap
"""

from __future__ import annotations

import json
import sys
import unittest
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.api.services import content as content_svc  # noqa: E402
from media_stack.services.apps.servarr.configure_auto_scan_job import (  # noqa: E402
    configure_auto_scan,
    _controller_url,
)


class EnsureArrScanWebhooksTests(unittest.TestCase):
    """The underlying registration function."""

    def _mock_http(self, existing=None, fail_post=False):
        """Build a fake HttpClient whose .request is programmable."""
        existing = existing if existing is not None else []

        def _req(base, path, api_key=None, method="GET", payload=None, **_kw):
            if method == "GET" and path == "/api/v3/notification":
                return (200, existing, "")
            if method == "POST" and path == "/api/v3/notification":
                if fail_post:
                    raise RuntimeError("post failed")
                return (200, {"id": 1}, "")
            return (404, None, "")

        client = MagicMock()
        client.request.side_effect = _req
        return client

    def test_no_api_keys_returns_error_per_service(self):
        with patch(
            "media_stack.api.services.content_analytics_mixin.discover_api_keys",
            return_value={},
        ), patch(
            "media_stack.api.services.content.SERVICE_MAP",
            {"sonarr": SimpleNamespace(host="sonarr", port=8989),
             "radarr": SimpleNamespace(host="radarr", port=7878)},
        ):
            result = content_svc.ensure_arr_scan_webhooks("http://ctrl:9100")
        self.assertEqual(result["webhooks"]["sonarr"], "no API key")
        self.assertEqual(result["webhooks"]["radarr"], "no API key")
        self.assertEqual(result["url"], "http://ctrl:9100/webhooks/arr")

    def test_registers_when_missing(self):
        with patch(
            "media_stack.api.services.content_analytics_mixin.discover_api_keys",
            return_value={"sonarr": "key-s", "radarr": "key-r"},
        ), patch(
            "media_stack.api.services.content.SERVICE_MAP",
            {"sonarr": SimpleNamespace(host="sonarr", port=8989),
             "radarr": SimpleNamespace(host="radarr", port=7878)},
        ), patch(
            "media_stack.core.http.HttpClient",
            return_value=self._mock_http(existing=[]),
        ):
            result = content_svc.ensure_arr_scan_webhooks("http://ctrl:9100")
        self.assertEqual(result["webhooks"]["sonarr"], "registered")
        self.assertEqual(result["webhooks"]["radarr"], "registered")

    def test_skips_when_already_registered(self):
        with patch(
            "media_stack.api.services.content_analytics_mixin.discover_api_keys",
            return_value={"sonarr": "key-s", "radarr": "key-r"},
        ), patch(
            "media_stack.api.services.content.SERVICE_MAP",
            {"sonarr": SimpleNamespace(host="sonarr", port=8989),
             "radarr": SimpleNamespace(host="radarr", port=7878)},
        ), patch(
            "media_stack.core.http.HttpClient",
            return_value=self._mock_http(existing=[{"name": "media-stack-scan"}]),
        ):
            result = content_svc.ensure_arr_scan_webhooks("http://ctrl:9100")
        self.assertEqual(result["webhooks"]["sonarr"], "already registered")
        self.assertEqual(result["webhooks"]["radarr"], "already registered")


class ConfigureAutoScanJobTests(unittest.TestCase):
    """The bootstrap-job wrapper."""

    def test_wraps_ensure_result(self):
        ctx = SimpleNamespace()
        fake = {"webhooks": {"sonarr": "registered", "radarr": "already registered"},
                "url": "http://ctrl:9100/webhooks/arr"}
        with patch(
            "media_stack.api.services.content.ensure_arr_scan_webhooks",
            return_value=fake,
        ):
            result = configure_auto_scan(ctx)
        self.assertEqual(result["registered"], ["sonarr"])
        self.assertEqual(result["already"], ["radarr"])
        self.assertEqual(result["errors"], {})
        self.assertEqual(result["url"], "http://ctrl:9100/webhooks/arr")

    def test_error_wrapped(self):
        ctx = SimpleNamespace()
        with patch(
            "media_stack.api.services.content.ensure_arr_scan_webhooks",
            side_effect=RuntimeError("boom"),
        ):
            result = configure_auto_scan(ctx)
        self.assertIn("error", result)
        self.assertIn("boom", result["error"])

    def test_controller_url_from_env(self):
        import os
        with patch.dict(os.environ, {"CONTROLLER_HOST": "ctrl", "BOOTSTRAP_API_PORT": "9200"}):
            self.assertEqual(_controller_url(), "http://ctrl:9200")


class ArrWebhookHandlerTests(unittest.TestCase):
    """POST /webhooks/arr — event parsing and Jellyfin scan trigger."""

    def _handler(self, body: dict):
        """Build a minimal fake handler matching the expectations of handlers_post."""
        h = MagicMock()
        h.path = "/webhooks/arr"
        h._read_json_body.return_value = body
        captured: dict = {}

        def _respond(status, payload):
            captured["status"] = status
            captured["payload"] = payload
        h._json_response.side_effect = _respond
        return h, captured

    def test_download_event_triggers_scan(self):
        from media_stack.api.handlers_post import PostRequestHandler
        svc = PostRequestHandler()
        h, captured = self._handler({"eventType": "Download",
                                     "movie": {"title": "Dune"}})
        with patch(
            "media_stack.api.services.health.discover_api_keys",
            return_value={"jellyfin": "jf-key"},
        ), patch(
            "media_stack.api.services.registry.SERVICE_MAP",
            {"jellyfin": SimpleNamespace(host="jellyfin", port=8096)},
        ), patch("urllib.request.urlopen") as mock_open:
            svc.handle(h)
        self.assertEqual(captured["status"], 200)
        self.assertEqual(captured["payload"]["event"], "Download")
        mock_open.assert_called_once()
        call_req = mock_open.call_args[0][0]
        self.assertIn("/Library/Refresh", call_req.full_url)

    def test_unrelated_event_skips_scan(self):
        from media_stack.api.handlers_post import PostRequestHandler
        svc = PostRequestHandler()
        h, captured = self._handler({"eventType": "Test",
                                     "series": {"title": "x"}})
        with patch("urllib.request.urlopen") as mock_open:
            svc.handle(h)
        self.assertEqual(captured["status"], 200)
        mock_open.assert_not_called()


if __name__ == "__main__":
    unittest.main()
