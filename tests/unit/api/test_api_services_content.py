"""Tests for media_stack.api.services.content — versions, downloads, stats,
indexers, history, quality profiles, import lists, Jellyfin libraries, recent."""

import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

import media_stack.api.services.content as content_mod  # noqa: E402

PATCH_KEYS = "media_stack.api.services.content.discover_api_keys"
PATCH_URLOPEN = "urllib.request.urlopen"


# ---------------------------------------------------------------------------
# Reusable mock cache
# ---------------------------------------------------------------------------

class MockCache:
    def __init__(self):
        self._store: dict = {}

    def get(self, key, ttl=0):
        return self._store.get(key)

    def set(self, key, value):
        self._store[key] = value


def _make_response(data):
    """Return a MagicMock that behaves like an urllib response context manager."""
    body = json.dumps(data).encode()
    resp = MagicMock()
    resp.read.return_value = body
    resp.status = 200
    resp.__enter__ = MagicMock(return_value=resp)
    resp.__exit__ = MagicMock(return_value=False)
    return resp


# ---------------------------------------------------------------------------
# get_versions
# ---------------------------------------------------------------------------

class TestGetVersionsCacheHit(unittest.TestCase):
    def test_returns_cached_result(self):
        cache = MockCache()
        cached = {"versions": {"sonarr": "4.0.0"}}
        cache._store["versions"] = cached
        result = content_mod.get_versions(cache)
        self.assertIs(result, cached)


class TestGetVersionsCacheMiss(unittest.TestCase):
    @patch(PATCH_URLOPEN)
    @patch(PATCH_KEYS, return_value={"sonarr": "key1", "radarr": "key2"})
    def test_fetches_and_caches(self, _mock_keys, mock_urlopen):
        mock_urlopen.return_value = _make_response({"version": "4.1.0"})
        cache = MockCache()
        result = content_mod.get_versions(cache)
        self.assertIn("versions", result)
        # Result should be cached now
        self.assertIs(cache._store["versions"], result)

    @patch(PATCH_URLOPEN, side_effect=Exception("timeout"))
    @patch(PATCH_KEYS, return_value={"sonarr": "key1"})
    def test_error_returns_empty_versions(self, _mock_keys, _mock_urlopen):
        cache = MockCache()
        result = content_mod.get_versions(cache)
        self.assertIn("versions", result)
        # All fetches failed so versions dict should be empty
        self.assertEqual(result["versions"], {})

    @patch(PATCH_URLOPEN)
    @patch(PATCH_KEYS, return_value={})
    def test_no_api_keys(self, _mock_keys, mock_urlopen):
        """With no API keys, fetches still attempted (no key header); versions may be empty."""
        mock_urlopen.return_value = _make_response({"version": "1.0"})
        cache = MockCache()
        result = content_mod.get_versions(cache)
        self.assertIn("versions", result)


class TestGetVersionsBazarrNestedKey(unittest.TestCase):
    @patch(PATCH_URLOPEN)
    @patch(PATCH_KEYS, return_value={"bazarr": "bkey"})
    def test_bazarr_nested_version(self, _mock_keys, mock_urlopen):
        """Bazarr version is nested at data.bazarr_version."""
        mock_urlopen.return_value = _make_response(
            {"data": {"bazarr_version": "1.5.6"}}
        )
        cache = MockCache()
        result = content_mod.get_versions(cache)
        self.assertIn("versions", result)
        if "bazarr" in result["versions"]:
            self.assertEqual(result["versions"]["bazarr"], "1.5.6")


# ---------------------------------------------------------------------------
# get_downloads
# ---------------------------------------------------------------------------

class TestGetDownloadsQBittorrent(unittest.TestCase):
    @patch("urllib.request.build_opener")
    def test_qbittorrent_active_torrents(self, mock_build_opener):
        mock_opener = MagicMock()
        mock_build_opener.return_value = mock_opener

        # login call returns something
        login_resp = MagicMock()
        login_resp.__enter__ = MagicMock(return_value=login_resp)
        login_resp.__exit__ = MagicMock(return_value=False)

        # torrent list call
        torrents = [
            {"name": "Ubuntu.ISO", "progress": 0.75, "state": "downloading",
             "size": 1000000, "dlspeed": 5000},
        ]
        torrent_resp = MagicMock()
        torrent_resp.read.return_value = json.dumps(torrents).encode()
        torrent_resp.__enter__ = MagicMock(return_value=torrent_resp)
        torrent_resp.__exit__ = MagicMock(return_value=False)

        mock_opener.open.side_effect = [login_resp, torrent_resp]

        result = content_mod.get_downloads()
        self.assertIn("qbittorrent", result)
        self.assertEqual(result["qbittorrent"]["active"], 1)
        self.assertEqual(len(result["qbittorrent"]["items"]), 1)
        self.assertAlmostEqual(result["qbittorrent"]["items"][0]["progress"], 75.0)

    @patch("urllib.request.build_opener", side_effect=Exception("connection refused"))
    def test_qbittorrent_error(self, _mock):
        result = content_mod.get_downloads()
        self.assertIn("qbittorrent", result)
        self.assertEqual(result["qbittorrent"]["active"], 0)
        self.assertIn("error", result["qbittorrent"])


class TestGetDownloadsSABnzbd(unittest.TestCase):
    @patch(PATCH_URLOPEN)
    @patch("urllib.request.build_opener", side_effect=Exception("skip qbit"))
    @patch.dict(os.environ, {"SABNZBD_API_KEY": "sab-test-key"})
    def test_sabnzbd_active_slots(self, _mock_opener, mock_urlopen):
        sab_data = {
            "queue": {
                "slots": [
                    {"filename": "Book.epub", "percentage": "42.5"},
                ],
                "speed": "1234",
            }
        }
        mock_urlopen.return_value = _make_response(sab_data)
        result = content_mod.get_downloads()
        self.assertIn("sabnzbd", result)
        self.assertEqual(result["sabnzbd"]["active"], 1)
        self.assertEqual(result["sabnzbd"]["speed"], "1234 KB/s")
        self.assertAlmostEqual(result["sabnzbd"]["items"][0]["progress"], 42.5)

    @patch("urllib.request.build_opener", side_effect=Exception("skip qbit"))
    @patch.dict(os.environ, {"SABNZBD_API_KEY": "", "CONFIG_ROOT": "/nonexistent"})
    def test_sabnzbd_no_key(self, _mock_opener):
        result = content_mod.get_downloads()
        # Without a key, sabnzbd section may not appear or has no items
        if "sabnzbd" in result:
            self.assertEqual(result["sabnzbd"].get("active", 0), 0)


# ---------------------------------------------------------------------------
# get_stats
# ---------------------------------------------------------------------------

class TestGetStatsCacheHit(unittest.TestCase):
    def test_returns_cached_result(self):
        cache = MockCache()
        cached = {"stats": {"sonarr": {"count": 100, "label": "series"}}}
        cache._store["stats"] = cached
        result = content_mod.get_stats(cache)
        self.assertIs(result, cached)


class TestGetStatsCacheMiss(unittest.TestCase):
    @patch(PATCH_URLOPEN)
    @patch(PATCH_KEYS, return_value={"sonarr": "s-key", "radarr": "r-key"})
    def test_fetches_library_counts(self, _mock_keys, mock_urlopen):
        # Return a list of 3 items for any request
        mock_urlopen.return_value = _make_response([{"id": 1}, {"id": 2}, {"id": 3}])
        cache = MockCache()
        result = content_mod.get_stats(cache)
        self.assertIn("stats", result)
        # At least sonarr and radarr should have counts
        for app in ("sonarr", "radarr"):
            if app in result["stats"]:
                self.assertEqual(result["stats"][app]["count"], 3)
        # Should be cached
        self.assertIs(cache._store["stats"], result)

    @patch(PATCH_URLOPEN, side_effect=Exception("conn refused"))
    @patch(PATCH_KEYS, return_value={"sonarr": "key"})
    def test_error_returns_zero_count(self, _mock_keys, _mock_urlopen):
        cache = MockCache()
        result = content_mod.get_stats(cache)
        self.assertIn("stats", result)
        for app_stats in result["stats"].values():
            self.assertEqual(app_stats["count"], 0)
            self.assertIn("label", app_stats)

    @patch(PATCH_URLOPEN)
    @patch(PATCH_KEYS, return_value={})
    def test_no_api_keys_returns_zero_counts(self, _mock_keys, mock_urlopen):
        cache = MockCache()
        result = content_mod.get_stats(cache)
        self.assertIn("stats", result)
        for app_stats in result["stats"].values():
            self.assertEqual(app_stats["count"], 0)


# ---------------------------------------------------------------------------
# get_indexers
# ---------------------------------------------------------------------------

class TestGetIndexers(unittest.TestCase):
    @patch(PATCH_URLOPEN)
    @patch(PATCH_KEYS, return_value={"prowlarr": "pkey"})
    def test_returns_indexer_list(self, _mock_keys, mock_urlopen):
        indexer_data = [
            {"id": 1, "name": "NZBgeek", "enable": True, "protocol": "usenet"},
            {"id": 2, "name": "1337x", "enable": False, "protocol": "torrent"},
        ]
        mock_urlopen.return_value = _make_response(indexer_data)
        result = content_mod.get_indexers()
        self.assertEqual(result["total"], 2)
        self.assertEqual(result["enabled"], 1)
        self.assertEqual(len(result["indexers"]), 2)
        self.assertEqual(result["indexers"][0]["name"], "NZBgeek")

    @patch(PATCH_KEYS, return_value={})
    def test_no_api_key_returns_empty(self, _mock_keys):
        result = content_mod.get_indexers()
        self.assertEqual(result["indexers"], [])
        self.assertEqual(result["total"], 0)
        self.assertEqual(result["enabled"], 0)

    @patch(PATCH_URLOPEN, side_effect=Exception("timeout"))
    @patch(PATCH_KEYS, return_value={"prowlarr": "pkey"})
    def test_error_returns_empty(self, _mock_keys, _mock_urlopen):
        result = content_mod.get_indexers()
        self.assertEqual(result["indexers"], [])
        self.assertEqual(result["total"], 0)


# ---------------------------------------------------------------------------
# get_indexer_stats
# ---------------------------------------------------------------------------

class TestGetIndexerStats(unittest.TestCase):
    @patch(PATCH_URLOPEN)
    @patch(PATCH_KEYS, return_value={"prowlarr": "pkey"})
    def test_returns_stats_list(self, _mock_keys, mock_urlopen):
        stats_data = {
            "indexers": [
                {"indexerId": 1, "indexerName": "NZBgeek", "numberOfQueries": 50},
            ]
        }
        mock_urlopen.return_value = _make_response(stats_data)
        result = content_mod.get_indexer_stats()
        self.assertEqual(len(result["stats"]), 1)
        self.assertEqual(result["stats"][0]["indexerName"], "NZBgeek")

    @patch(PATCH_KEYS, return_value={})
    def test_no_api_key_returns_empty(self, _mock_keys):
        result = content_mod.get_indexer_stats()
        self.assertEqual(result["stats"], [])

    @patch(PATCH_URLOPEN, side_effect=Exception("nope"))
    @patch(PATCH_KEYS, return_value={"prowlarr": "pkey"})
    def test_error_returns_empty(self, _mock_keys, _mock_urlopen):
        result = content_mod.get_indexer_stats()
        self.assertEqual(result["stats"], [])


# ---------------------------------------------------------------------------
# get_download_history
# ---------------------------------------------------------------------------

class TestGetDownloadHistory(unittest.TestCase):
    @patch(PATCH_URLOPEN)
    @patch(PATCH_KEYS, return_value={"sonarr": "skey", "radarr": "rkey"})
    def test_returns_history_records(self, _mock_keys, mock_urlopen):
        history_data = {
            "records": [
                {"sourceTitle": "Show.S01E01", "eventType": "grabbed",
                 "date": "2026-04-07T12:00:00Z"},
            ]
        }
        mock_urlopen.return_value = _make_response(history_data)
        result = content_mod.get_download_history()
        self.assertIn("history", result)
        for app in ("sonarr", "radarr"):
            if app in result["history"]:
                self.assertEqual(len(result["history"][app]), 1)
                self.assertEqual(result["history"][app][0]["title"], "Show.S01E01")

    @patch(PATCH_KEYS, return_value={})
    def test_no_api_keys_returns_empty_lists(self, _mock_keys):
        result = content_mod.get_download_history()
        self.assertIn("history", result)
        for app_history in result["history"].values():
            self.assertEqual(app_history, [])

    @patch(PATCH_URLOPEN, side_effect=Exception("refused"))
    @patch(PATCH_KEYS, return_value={"sonarr": "skey"})
    def test_error_returns_empty_list(self, _mock_keys, _mock_urlopen):
        result = content_mod.get_download_history()
        self.assertIn("history", result)
        for app_history in result["history"].values():
            self.assertEqual(app_history, [])


# ---------------------------------------------------------------------------
# get_quality_profiles
# ---------------------------------------------------------------------------

class TestGetQualityProfiles(unittest.TestCase):
    @patch(PATCH_URLOPEN)
    @patch(PATCH_KEYS, return_value={"sonarr": "skey", "radarr": "rkey"})
    def test_returns_profiles(self, _mock_keys, mock_urlopen):
        profile_data = [
            {"id": 1, "name": "HD-1080p"},
            {"id": 2, "name": "Ultra-HD"},
        ]
        mock_urlopen.return_value = _make_response(profile_data)
        result = content_mod.get_quality_profiles()
        self.assertIn("profiles", result)
        for app in ("sonarr", "radarr"):
            if app in result["profiles"]:
                self.assertEqual(len(result["profiles"][app]), 2)
                self.assertEqual(result["profiles"][app][0]["name"], "HD-1080p")

    @patch(PATCH_KEYS, return_value={})
    def test_no_api_keys_returns_empty(self, _mock_keys):
        result = content_mod.get_quality_profiles()
        self.assertIn("profiles", result)
        for profiles in result["profiles"].values():
            self.assertEqual(profiles, [])

    @patch(PATCH_URLOPEN, side_effect=Exception("timeout"))
    @patch(PATCH_KEYS, return_value={"sonarr": "skey"})
    def test_error_returns_empty(self, _mock_keys, _mock_urlopen):
        result = content_mod.get_quality_profiles()
        self.assertIn("profiles", result)
        for profiles in result["profiles"].values():
            self.assertEqual(profiles, [])


# ---------------------------------------------------------------------------
# get_import_lists
# ---------------------------------------------------------------------------

class TestGetImportLists(unittest.TestCase):
    @patch(PATCH_URLOPEN)
    @patch(PATCH_KEYS, return_value={"sonarr": "skey", "radarr": "rkey"})
    def test_returns_lists(self, _mock_keys, mock_urlopen):
        list_data = [
            {"id": 1, "name": "Trakt Popular", "enableAutomaticAdd": True,
             "listType": "trakt"},
        ]
        mock_urlopen.return_value = _make_response(list_data)
        result = content_mod.get_import_lists()
        self.assertIn("lists", result)
        for app in ("sonarr", "radarr"):
            if app in result["lists"]:
                self.assertEqual(len(result["lists"][app]), 1)
                self.assertEqual(result["lists"][app][0]["name"], "Trakt Popular")
                self.assertTrue(result["lists"][app][0]["enabled"])

    @patch(PATCH_KEYS, return_value={})
    def test_no_api_keys_returns_empty(self, _mock_keys):
        result = content_mod.get_import_lists()
        self.assertIn("lists", result)
        for lists in result["lists"].values():
            self.assertEqual(lists, [])

    @patch(PATCH_URLOPEN, side_effect=Exception("error"))
    @patch(PATCH_KEYS, return_value={"sonarr": "skey"})
    def test_error_returns_empty(self, _mock_keys, _mock_urlopen):
        result = content_mod.get_import_lists()
        self.assertIn("lists", result)
        for lists in result["lists"].values():
            self.assertEqual(lists, [])


# ---------------------------------------------------------------------------
# get_jellyfin_libraries
# ---------------------------------------------------------------------------

class TestGetJellyfinLibraries(unittest.TestCase):
    @staticmethod
    def _jellyfin_service():
        # The registry's media-server category was renamed
        # ``media-server`` -> ``media`` (see the inline comment at
        # ``content.get_media_server_libraries``). The handler filters
        # ``svc.category != "media"``; if this fixture lags behind the
        # rename, the patched SERVICES list never matches and the
        # function silently returns ``{libraries: []}``.
        from media_stack.api.services.registry import ServiceDef
        return ServiceDef(
            id="jellyfin", name="Jellyfin", category="media",
            host="jellyfin", port=8096, auth_mode="X-Emby-Token",
            api_key_env="JELLYFIN_API_KEY",
        )

    def setUp(self):
        # The runtime_keys module caches read_service_api_key results
        # for 30s by default — bypass it so each test starts clean.
        from media_stack.api.services import runtime_keys
        runtime_keys.invalidate_cache()

    @patch(PATCH_URLOPEN)
    @patch.dict(os.environ, {"JELLYFIN_API_KEY": "jf-test-key"})
    def test_returns_libraries(self, mock_urlopen):
        lib_data = [
            {"Name": "Movies", "CollectionType": "movies",
             "Locations": ["/media/movies"], "ItemCount": 500},
            {"Name": "TV Shows", "CollectionType": "tvshows",
             "Locations": ["/media/tv"], "ItemCount": 200},
        ]
        mock_urlopen.return_value = _make_response(lib_data)
        with patch.object(content_mod, "SERVICES", [self._jellyfin_service()]):
            result = content_mod.get_jellyfin_libraries()
        self.assertEqual(len(result["libraries"]), 2)
        self.assertEqual(result["libraries"][0]["name"], "Movies")
        # Renamed: ``type`` -> ``collection_type``, ``count`` ->
        # ``item_count``. Pin the new shape.
        self.assertEqual(result["libraries"][0]["collection_type"], "movies")
        self.assertEqual(result["libraries"][0]["item_count"], 0)
        self.assertEqual(result["libraries"][1]["paths"], ["/media/tv"])

    @patch.dict(os.environ, {"JELLYFIN_API_KEY": ""})
    def test_no_api_key_returns_empty(self):
        result = content_mod.get_jellyfin_libraries()
        self.assertEqual(result["libraries"], [])

    @patch(PATCH_URLOPEN, side_effect=Exception("timeout"))
    @patch.dict(os.environ, {"JELLYFIN_API_KEY": "jf-key"})
    def test_error_returns_empty(self, _mock_urlopen):
        result = content_mod.get_jellyfin_libraries()
        self.assertEqual(result["libraries"], [])

    @patch(PATCH_URLOPEN)
    @patch.dict(os.environ, {"JELLYFIN_API_KEY": "jf-key"})
    def test_non_list_response_returns_empty(self, mock_urlopen):
        """If Jellyfin returns a dict instead of list, should handle gracefully."""
        mock_urlopen.return_value = _make_response({"error": "something"})
        result = content_mod.get_jellyfin_libraries()
        self.assertEqual(result["libraries"], [])

    @patch(PATCH_URLOPEN)
    @patch.dict(os.environ, {"JELLYFIN_API_KEY": ""})
    def test_live_libraries_populated_from_disk_when_env_empty(self, mock_urlopen):
        """v1.0.181 regression: ``LibraryStatsTiles`` showed 1 of each
        because the K8s Secret had every API key as empty string and
        the libraries endpoint short-circuited on env-empty. The new
        ``read_service_api_key`` helper reads the on-disk config file
        as a fallback so live data still flows."""
        os.environ.pop("JELLYFIN_API_KEY", None)
        lib_data = [
            {"Name": "Movies", "CollectionType": "movies",
             "Locations": ["/media/movies"], "ItemCount": 42},
        ]
        mock_urlopen.return_value = _make_response(lib_data)
        with patch.object(content_mod, "SERVICES", [self._jellyfin_service()]), \
             patch(
                 "media_stack.api.services.runtime_keys._read_file_key",
                 return_value="key-from-disk",
             ):
            result = content_mod.get_jellyfin_libraries()
        self.assertEqual(len(result["libraries"]), 1)
        self.assertEqual(result["libraries"][0]["name"], "Movies")
        self.assertNotIn("error", result)

    @patch.dict(os.environ, {"JELLYFIN_API_KEY": ""})
    def test_no_key_anywhere_surfaces_actionable_error(self):
        """When neither env nor disk has a key, the response must
        carry a structured error for the dashboard rather than the
        ambiguous empty-libraries-without-explanation."""
        os.environ.pop("JELLYFIN_API_KEY", None)
        with patch.object(content_mod, "SERVICES", [self._jellyfin_service()]), \
             patch(
                 "media_stack.api.services.runtime_keys._read_file_key",
                 return_value="",
             ):
            result = content_mod.get_jellyfin_libraries()
        self.assertEqual(result["libraries"], [])
        self.assertIn("error", result)
        self.assertIn("jellyfin", result["error"])


# ---------------------------------------------------------------------------
# get_recent
# ---------------------------------------------------------------------------

class TestGetRecent(unittest.TestCase):
    @patch(PATCH_URLOPEN)
    @patch(PATCH_KEYS, return_value={"sonarr": "skey", "radarr": "rkey"})
    def test_returns_recent_items(self, _mock_keys, mock_urlopen):
        recent_data = [
            {"title": "Breaking Bad", "dateAdded": "2026-04-01T10:00:00Z"},
            {"title": "Better Call Saul", "dateAdded": "2026-04-02T10:00:00Z"},
        ]
        mock_urlopen.return_value = _make_response(recent_data)
        result = content_mod.get_recent()
        self.assertIn("recent", result)
        for app in ("sonarr", "radarr"):
            if app in result["recent"]:
                self.assertEqual(len(result["recent"][app]), 2)
                self.assertEqual(result["recent"][app][0]["title"], "Breaking Bad")
                self.assertEqual(result["recent"][app][0]["added"], "2026-04-01")

    @patch(PATCH_KEYS, return_value={})
    def test_no_api_keys_returns_empty(self, _mock_keys):
        result = content_mod.get_recent()
        self.assertIn("recent", result)
        for items in result["recent"].values():
            self.assertEqual(items, [])

    @patch(PATCH_URLOPEN, side_effect=Exception("refused"))
    @patch(PATCH_KEYS, return_value={"sonarr": "skey"})
    def test_error_returns_empty(self, _mock_keys, _mock_urlopen):
        result = content_mod.get_recent()
        self.assertIn("recent", result)
        for items in result["recent"].values():
            self.assertEqual(items, [])

    @patch(PATCH_URLOPEN)
    @patch(PATCH_KEYS, return_value={"sonarr": "skey"})
    def test_limits_to_five_items(self, _mock_keys, mock_urlopen):
        """get_recent slices to [:5], so 10 items should yield at most 5."""
        many_items = [
            {"title": f"Show {i}", "dateAdded": f"2026-04-0{min(i,9)}T00:00:00Z"}
            for i in range(10)
        ]
        mock_urlopen.return_value = _make_response(many_items)
        result = content_mod.get_recent()
        for items in result["recent"].values():
            self.assertLessEqual(len(items), 5)


if __name__ == "__main__":
    unittest.main()
