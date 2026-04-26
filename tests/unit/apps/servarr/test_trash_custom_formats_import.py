"""Tests for TRASHguides custom-format import.

Covers:
1. _fetch_trash_custom_formats — parses index, fetches per-CF details
2. import_trash_custom_formats — service-level happy path, skip-existing,
   error handling, and missing API key/service
3. POST /api/custom-formats/import — handler wiring
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

import media_stack.services.apps.servarr.quality_preset_service as qp_mod  # noqa: E402


def _urlopen_responder(by_url: dict):
    """Return a urlopen mock that yields bytes based on the requested URL."""
    def _side(req, timeout=None):
        url = req if isinstance(req, str) else req.full_url
        body = by_url.get(url)
        if body is None:
            raise RuntimeError(f"unexpected URL: {url}")
        resp = MagicMock()
        resp.read.return_value = (body if isinstance(body, bytes)
                                   else json.dumps(body).encode("utf-8"))
        resp.__enter__ = lambda s: s
        resp.__exit__ = MagicMock(return_value=False)
        return resp
    return _side


class FetchTrashCustomFormatsTests(unittest.TestCase):
    def test_parses_index_and_fetches_details(self):
        index_url = "https://example.com/path/index.json"
        index = [
            {"trash_id": "abc123", "name": "x264"},
            {"trash_id": "def456", "name": "x265"},
        ]
        detail_abc = {"name": "x264", "trash_id": "abc123",
                      "specifications": [{"implementation": "ReleaseTitleSpecification"}]}
        detail_def = {"name": "x265", "trash_id": "def456",
                      "specifications": [{"implementation": "ReleaseTitleSpecification"}]}
        responses = {
            index_url: index,
            "https://example.com/path/abc123.json": detail_abc,
            "https://example.com/path/def456.json": detail_def,
        }
        svc = qp_mod.QualityPresetService()
        with patch("urllib.request.urlopen", side_effect=_urlopen_responder(responses)):
            payloads = svc._fetch_trash_custom_formats(index_url, "sonarr")
        self.assertEqual(len(payloads), 2)
        names = {p["name"] for p in payloads}
        self.assertEqual(names, {"x264", "x265"})
        # trash_id must be stripped (arr API rejects it)
        self.assertNotIn("trash_id", payloads[0])
        self.assertNotIn("trash_id", payloads[1])

    def test_raises_when_index_not_list(self):
        index_url = "https://example.com/path/index.json"
        responses = {index_url: {"not": "a list"}}
        svc = qp_mod.QualityPresetService()
        with patch("urllib.request.urlopen", side_effect=_urlopen_responder(responses)):
            with self.assertRaises(ValueError):
                svc._fetch_trash_custom_formats(index_url, "sonarr")


class ImportTrashCustomFormatsTests(unittest.TestCase):
    def _mock_http(self, existing_cfs=None, post_fail=False):
        existing_cfs = existing_cfs or []

        def _req(base, path, api_key=None, method="GET", payload=None, **_kw):
            if method == "GET" and path == "/api/v3/customformat":
                return (200, existing_cfs, "")
            if method == "POST" and path == "/api/v3/customformat":
                if post_fail:
                    raise RuntimeError("post exploded")
                return (200, {"id": 99}, "")
            return (404, None, "")

        client = MagicMock()
        client.request.side_effect = _req
        return client

    def test_missing_service(self):
        with patch.object(qp_mod, "_instance", qp_mod._instance), patch(
            "media_stack.api.services.registry.SERVICE_MAP", {}
        ):
            result = qp_mod.import_trash_custom_formats("nope", "http://x")
        self.assertIn("error", result)
        self.assertIn("not found", result["error"])

    def test_missing_api_key(self):
        with patch(
            "media_stack.api.services.registry.SERVICE_MAP",
            {"sonarr": SimpleNamespace(host="sonarr", port=8989)},
        ), patch(
            "media_stack.api.services.health.discover_api_keys",
            return_value={},
        ):
            result = qp_mod.import_trash_custom_formats("sonarr", "http://x")
        self.assertIn("error", result)
        self.assertIn("API key", result["error"])

    def test_happy_path_imports_new_skips_existing(self):
        payloads = [
            {"name": "x264", "specifications": []},
            {"name": "x265", "specifications": []},
            {"name": "EXISTING", "specifications": []},
        ]
        with patch(
            "media_stack.api.services.registry.SERVICE_MAP",
            {"sonarr": SimpleNamespace(host="sonarr", port=8989)},
        ), patch(
            "media_stack.api.services.health.discover_api_keys",
            return_value={"sonarr": "key"},
        ), patch.object(
            qp_mod.QualityPresetService, "_fetch_trash_custom_formats",
            return_value=payloads,
        ), patch(
            "media_stack.core.http.HttpClient",
            return_value=self._mock_http(existing_cfs=[{"name": "EXISTING"}]),
        ):
            result = qp_mod.import_trash_custom_formats("sonarr", "http://x")
        self.assertEqual(result["imported"], ["x264", "x265"])
        self.assertEqual(result["skipped"], ["EXISTING"])
        self.assertEqual(result["errors"], [])
        self.assertEqual(result["total_available"], 3)

    def test_fetch_error_returned(self):
        with patch(
            "media_stack.api.services.registry.SERVICE_MAP",
            {"sonarr": SimpleNamespace(host="sonarr", port=8989)},
        ), patch(
            "media_stack.api.services.health.discover_api_keys",
            return_value={"sonarr": "key"},
        ), patch.object(
            qp_mod.QualityPresetService, "_fetch_trash_custom_formats",
            side_effect=RuntimeError("network down"),
        ):
            result = qp_mod.import_trash_custom_formats("sonarr", "http://x")
        self.assertIn("error", result)
        self.assertIn("Fetch failed", result["error"])

    def test_post_errors_collected(self):
        payloads = [{"name": "broken", "specifications": []}]
        with patch(
            "media_stack.api.services.registry.SERVICE_MAP",
            {"sonarr": SimpleNamespace(host="sonarr", port=8989)},
        ), patch(
            "media_stack.api.services.health.discover_api_keys",
            return_value={"sonarr": "key"},
        ), patch.object(
            qp_mod.QualityPresetService, "_fetch_trash_custom_formats",
            return_value=payloads,
        ), patch(
            "media_stack.core.http.HttpClient",
            return_value=self._mock_http(existing_cfs=[], post_fail=True),
        ):
            result = qp_mod.import_trash_custom_formats("sonarr", "http://x")
        self.assertEqual(result["imported"], [])
        self.assertEqual(len(result["errors"]), 1)
        self.assertIn("broken:", result["errors"][0])


class ImportHandlerTests(unittest.TestCase):
    """POST /api/custom-formats/import routing."""

    def _handler(self, body: dict):
        h = MagicMock()
        h.path = "/api/custom-formats/import"
        h._read_json_body.return_value = body
        captured: dict = {}

        def _respond(status, payload):
            captured["status"] = status
            captured["payload"] = payload
        h._json_response.side_effect = _respond
        return h, captured

    def test_validates_required_fields(self):
        from media_stack.api.handlers_post import PostRequestHandler
        svc = PostRequestHandler()
        h, captured = self._handler({})
        svc.handle(h)
        self.assertEqual(captured["status"], 400)
        self.assertIn("error", captured["payload"])

    def test_delegates_to_service(self):
        from media_stack.api.handlers_post import PostRequestHandler
        svc = PostRequestHandler()
        h, captured = self._handler({"service": "sonarr", "index_url": "http://x"})
        with patch(
            "media_stack.services.apps.servarr.quality_preset_service.import_trash_custom_formats",
            return_value={"imported": ["x264"], "skipped": [], "errors": [],
                          "total_available": 1, "service": "sonarr"},
        ):
            svc.handle(h)
        self.assertEqual(captured["status"], 200)
        self.assertEqual(captured["payload"]["imported"], ["x264"])


if __name__ == "__main__":
    unittest.main()
