"""Tests for content services: versions, downloads, stats, indexers."""

import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

import media_stack.api.services.content as content_mod  # noqa: E402


class _FakeCache:
    def __init__(self, data=None):
        self._data = data
    def get(self, key, ttl):
        return self._data
    def set(self, key, value):
        self._data = value


class TestGetVersions(unittest.TestCase):
    def test_returns_cached(self):
        cache = _FakeCache({"versions": {"sonarr": "4.0.0"}})
        result = content_mod.get_versions(cache)
        self.assertEqual(result["versions"]["sonarr"], "4.0.0")

    @patch("media_stack.api.services.content.discover_api_keys", return_value={})
    @patch("urllib.request.urlopen")
    def test_fetches_versions_on_cache_miss(self, mock_urlopen, mock_keys):
        resp = MagicMock()
        resp.read.return_value = json.dumps({"version": "4.0.1"}).encode()
        resp.__enter__ = MagicMock(return_value=resp)
        resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = resp
        cache = _FakeCache(None)
        result = content_mod.get_versions(cache)
        self.assertIn("versions", result)

    @patch("media_stack.api.services.content.discover_api_keys", return_value={})
    @patch("urllib.request.urlopen", side_effect=Exception("timeout"))
    def test_version_fetch_failure_returns_empty(self, mock_urlopen, mock_keys):
        cache = _FakeCache(None)
        result = content_mod.get_versions(cache)
        self.assertIsInstance(result.get("versions", {}), dict)


class TestGetDownloads(unittest.TestCase):
    @patch.dict(os.environ, {"STACK_ADMIN_USERNAME": "admin", "STACK_ADMIN_PASSWORD": "pass"})
    @patch("media_stack.api.services.content._DOWNLOAD_CLIENT_IDS", {})
    def test_no_clients_returns_empty(self):
        result = content_mod.get_downloads()
        self.assertEqual(result, {})

    @patch.dict(os.environ, {"STACK_ADMIN_USERNAME": "admin", "STACK_ADMIN_PASSWORD": "pass"})
    @patch("media_stack.api.services.content._DOWNLOAD_CLIENT_IDS", {"qbittorrent": "torrent"})
    @patch("media_stack.api.services.content.SERVICE_MAP", {})
    def test_missing_service_skipped(self):
        result = content_mod.get_downloads()
        self.assertNotIn("qbittorrent", result)


class TestGetStats(unittest.TestCase):
    def test_returns_cached(self):
        cache = _FakeCache({"stats": {"sonarr": {"count": 42, "label": "series"}}})
        result = content_mod.get_stats(cache)
        self.assertEqual(result["stats"]["sonarr"]["count"], 42)

    @patch("media_stack.api.services.content.discover_api_keys", return_value={})
    def test_cache_miss_returns_stats(self, _):
        cache = _FakeCache(None)
        result = content_mod.get_stats(cache)
        self.assertIn("stats", result)


class TestGetIndexers(unittest.TestCase):
    @patch("media_stack.api.services.content.discover_api_keys", return_value={})
    def test_no_prowlarr_key(self, _):
        result = content_mod.get_indexers()
        self.assertIn("indexers", result)

    @patch("media_stack.api.services.content.discover_api_keys", return_value={"prowlarr": "key123"})
    @patch("urllib.request.urlopen")
    def test_fetches_indexers(self, mock_urlopen, _):
        resp = MagicMock()
        resp.read.return_value = json.dumps([
            {"id": 1, "name": "Test Indexer", "enable": True, "protocol": "torrent", "fields": []},
        ]).encode()
        resp.__enter__ = MagicMock(return_value=resp)
        resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = resp
        result = content_mod.get_indexers()
        self.assertGreater(result.get("total", 0), 0)


class TestGetIndexerStats(unittest.TestCase):
    @patch("media_stack.api.services.content.discover_api_keys", return_value={})
    def test_no_key_returns_empty(self, _):
        result = content_mod.get_indexer_stats()
        self.assertIn("stats", result)

    @patch("media_stack.api.services.content.discover_api_keys", return_value={"prowlarr": "key"})
    @patch("urllib.request.urlopen", side_effect=Exception("fail"))
    def test_http_error_returns_empty(self, *_):
        result = content_mod.get_indexer_stats()
        self.assertEqual(result["stats"], [])


class TestGetQualityProfiles(unittest.TestCase):
    @patch("media_stack.api.services.content.discover_api_keys", return_value={})
    def test_returns_profiles_dict(self, _):
        result = content_mod.get_quality_profiles()
        self.assertIn("profiles", result)


class TestGetImportLists(unittest.TestCase):
    @patch("media_stack.api.services.content.discover_api_keys", return_value={})
    def test_returns_lists_dict(self, _):
        result = content_mod.get_import_lists()
        self.assertIn("lists", result)


class TestGetRecent(unittest.TestCase):
    @patch("media_stack.api.services.content.discover_api_keys", return_value={})
    def test_returns_recent(self, _):
        result = content_mod.get_recent()
        self.assertIn("recent", result)


class TestGetJellyfinLibraries(unittest.TestCase):
    @patch.dict(os.environ, {"JELLYFIN_API_KEY": ""})
    def test_no_key_returns_empty(self):
        result = content_mod.get_jellyfin_libraries()
        self.assertIn("libraries", result)


class TestGetDownloadHistory(unittest.TestCase):
    @patch("media_stack.api.services.content.discover_api_keys", return_value={})
    def test_returns_history(self, _):
        result = content_mod.get_download_history()
        self.assertIn("history", result)


if __name__ == "__main__":
    unittest.main()
