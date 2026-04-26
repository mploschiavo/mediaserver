"""Tests for content services: versions, downloads, stats, indexers."""

import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[3]
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

    def test_iterates_category_media_not_dead_string(self):
        """The registry uses category="media" for jellyfin/emby/plex/etc.
        An earlier shape used "media-server"; the rename was never
        propagated to ``get_media_server_libraries``, so the filter
        matched ZERO services and the function silently returned
        ``{libraries: []}``. The /api/libraries handler then reported
        ``live: []`` + ``source: defaults`` and the dashboard banner
        mis-blamed JELLYFIN_API_KEY (which was actually fine).

        Lock the filter to "media": at least one Jellyfin-class service
        in the live registry MUST match. Detecting this with a
        live-registry check (rather than mocking SERVICES) means a
        future registry-rename that drops "media" without updating this
        function will trip the test."""
        from media_stack.api.services.registry import SERVICES
        media_services = [s for s in SERVICES if s.category == "media"]
        self.assertTrue(
            any(s.id == "jellyfin" for s in media_services),
            "Jellyfin must be registered with category='media' so "
            "get_media_server_libraries can find it. If the registry "
            "category was renamed, update content.py to match.",
        )

    def test_returns_libraries_when_jellyfin_responds(self):
        """End-to-end shape check: with a valid key in env and a
        responding Jellyfin, the function returns parsed libraries
        (not the empty-fallback). Catches regressions where the loop
        silently no-ops (the bug shape that hit production
        2026-04-25)."""
        folders = [
            {"Name": "Movies", "CollectionType": "movies",
             "Locations": ["/media/movies"], "ItemId": "vf-1"},
            {"Name": "TV Shows", "CollectionType": "tvshows",
             "Locations": ["/media/tv"], "ItemId": "vf-2"},
        ]
        # First urlopen call → /Library/VirtualFolders. Subsequent
        # calls → per-library /Items?...&Limit=0 returning a
        # TotalRecordCount each. We don't care about call order beyond
        # "the first hit is folders" — the helper returns 0 on errors,
        # so over-mocking would hide real bugs.
        def fake_urlopen(req, timeout=5):
            url = req.full_url if hasattr(req, "full_url") else str(req)
            r = MagicMock()
            r.__enter__ = MagicMock(return_value=r)
            r.__exit__ = MagicMock(return_value=False)
            if "VirtualFolders" in url:
                r.read.return_value = json.dumps(folders).encode()
            elif "ParentId=vf-1" in url:
                r.read.return_value = json.dumps({"TotalRecordCount": 108}).encode()
            elif "ParentId=vf-2" in url:
                r.read.return_value = json.dumps({"TotalRecordCount": 10}).encode()
            else:
                r.read.return_value = b'{"TotalRecordCount": 0}'
            return r
        with patch("media_stack.api.services.content.read_service_api_key",
                   return_value="fake-key"), \
             patch("urllib.request.urlopen", side_effect=fake_urlopen):
            svc = content_mod.ContentService()
            result = svc.get_media_server_libraries()
        libs = result.get("libraries", [])
        self.assertEqual(len(libs), 2,
                         f"Expected 2 libraries, got {result!r}. "
                         "If 0, the category filter is dead-stringing again.")
        # Field names must match the UI's LiveLibraryEntry contract:
        # collection_type / item_count, NOT type / count. Without
        # these, LibraryStatsTiles falls back to counting configured
        # libraries (1/1/1/1) instead of summing live item_count.
        first = libs[0]
        self.assertEqual(first["name"], "Movies")
        self.assertEqual(first["collection_type"], "movies",
                         "Field must be `collection_type` (UI contract); "
                         "the old name `type` triggers the 1/1/1/1 "
                         "fallback in LibraryStatsTiles.")
        self.assertEqual(first["item_count"], 108,
                         "Field must be `item_count` populated from "
                         "/Items.TotalRecordCount; the old name `count` "
                         "or `null` from VirtualFolders.ItemCount fails "
                         "the UI's `entry.item_count > 0` check.")
        self.assertEqual(libs[1]["item_count"], 10)

    def test_jellyfin_library_item_count_uses_correct_item_type(self):
        """The per-library item-count helper maps collection_type to
        the IncludeItemTypes filter the dashboard cares about:
          movies   → Movie
          tvshows  → Series
          music    → Audio   (matches the Tracks tile)
          books    → Book
          boxsets  → BoxSet
        A typo (e.g. "tvshow" → "TVSeries") would silently return
        0-for-everything, looking identical to a Jellyfin outage."""
        captured: list[str] = []
        r = MagicMock()
        r.__enter__ = MagicMock(return_value=r)
        r.__exit__ = MagicMock(return_value=False)
        r.read.return_value = b'{"TotalRecordCount": 7}'
        def fake_urlopen(req, timeout=5):
            captured.append(req.full_url)
            return r
        svc = content_mod.ContentService()
        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            for ctype, expected_type in [
                ("movies", "Movie"), ("tvshows", "Series"),
                ("music", "Audio"), ("books", "Book"),
            ]:
                count = svc._jellyfin_library_item_count(
                    "jellyfin", 8096, "X-Emby-Token", "k",
                    parent_id="pid", collection_type=ctype,
                )
                self.assertEqual(count, 7)
        self.assertEqual(len(captured), 4)
        for url, want in zip(captured, ["Movie", "Series", "Audio", "Book"]):
            self.assertIn(f"IncludeItemTypes={want}", url,
                          f"expected IncludeItemTypes={want} in {url}")
            self.assertIn("Recursive=true", url)
            self.assertIn("Limit=0", url)
            self.assertIn("ParentId=pid", url)


class TestPickPosterUrl(unittest.TestCase):
    """``_pick_poster_url`` extracts the dashboard's RecentAdditionsCard
    artwork from arr's ``images`` array. The card silently rendered
    without artwork in production because the field was never emitted —
    these tests lock in the field-name + cover-type-priority contract."""

    def test_prefers_poster_cover_type(self):
        images = [
            {"coverType": "fanart", "url": "/fanart.jpg"},
            {"coverType": "poster", "url": "/poster.jpg"},
            {"coverType": "banner", "url": "/banner.jpg"},
        ]
        self.assertEqual(content_mod._pick_poster_url(images), "/poster.jpg")

    def test_falls_back_to_first_when_no_poster(self):
        images = [{"coverType": "fanart", "url": "/fanart.jpg"}]
        self.assertEqual(content_mod._pick_poster_url(images), "/fanart.jpg")

    def test_uses_remote_url_when_local_missing(self):
        images = [{"coverType": "poster", "remoteUrl": "https://tmdb.example/p.jpg"}]
        self.assertEqual(
            content_mod._pick_poster_url(images),
            "https://tmdb.example/p.jpg",
        )

    def test_returns_empty_for_invalid_input(self):
        self.assertEqual(content_mod._pick_poster_url(None), "")
        self.assertEqual(content_mod._pick_poster_url("not-a-list"), "")
        self.assertEqual(content_mod._pick_poster_url([]), "")
        self.assertEqual(content_mod._pick_poster_url([{"coverType": "poster"}]), "")


class TestGetRecentShape(unittest.TestCase):
    """The /api/recent payload feeds RecentAdditionsCard. Two production
    bugs hit at once on this endpoint:
      (1) ``added`` was read as ``dateAdded`` — a Sonarr-v2-era field
          name. Modern arr v3+ APIs use ``added``. Result: the date
          shown on every card was empty string.
      (2) No ``poster`` field was emitted, so the card always rendered
          its placeholder regardless of whether artwork existed.
    Lock both in: ``added`` falls through to ``dateAdded``, and posters
    are extracted from the ``images`` array."""

    @patch("media_stack.api.services.content.discover_api_keys",
           return_value={"radarr": "k"})
    @patch("media_stack.api.services.content.SERVICES",
           [MagicMock(id="radarr", host="radarr", port=7878,
                      recent_path="/api/v3/movie")])
    @patch("urllib.request.urlopen")
    def test_uses_added_field_and_extracts_poster(self, mock_urlopen, _):
        body = json.dumps([{
            "title": "Inception",
            "added": "2026-04-25T10:00:00Z",
            "images": [
                {"coverType": "fanart", "url": "/api/v3/MediaCover/1/fanart.jpg"},
                {"coverType": "poster", "url": "/api/v3/MediaCover/1/poster.jpg"},
            ],
        }]).encode()
        resp = MagicMock()
        resp.read.return_value = body
        resp.__enter__ = MagicMock(return_value=resp)
        resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = resp
        result = content_mod.get_recent()
        items = result["recent"]["radarr"]
        self.assertEqual(len(items), 1)
        item = items[0]
        self.assertEqual(item["title"], "Inception")
        self.assertEqual(item["added"], "2026-04-25",
                         "must read `added`, not the legacy `dateAdded`")
        self.assertEqual(item["poster"], "/api/v3/MediaCover/1/poster.jpg",
                         "must extract poster from the images array")

    @patch("media_stack.api.services.content.discover_api_keys",
           return_value={"sonarr": "k"})
    @patch("media_stack.api.services.content.SERVICES",
           [MagicMock(id="sonarr", host="sonarr", port=8989,
                      recent_path="/api/v3/series")])
    @patch("urllib.request.urlopen")
    def test_falls_back_to_dateAdded_for_legacy_arr(self, mock_urlopen, _):
        """Some installs may still run pre-v3 arr APIs that use
        dateAdded. Don't regress them — the fallback path stays."""
        body = json.dumps([{
            "title": "Legacy Show", "dateAdded": "2025-01-01",
        }]).encode()
        resp = MagicMock()
        resp.read.return_value = body
        resp.__enter__ = MagicMock(return_value=resp)
        resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = resp
        result = content_mod.get_recent()
        items = result["recent"]["sonarr"]
        self.assertEqual(items[0]["added"], "2025-01-01")


class TestGetDownloadHistory(unittest.TestCase):
    @patch("media_stack.api.services.content.discover_api_keys", return_value={})
    def test_returns_history(self, _):
        result = content_mod.get_download_history()
        self.assertIn("history", result)


if __name__ == "__main__":
    unittest.main()
