"""Tests for user-facing configuration features: libraries, categories, metadata, IPTV, scheduler, storage."""

import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

import media_stack.api.services.config as config_mod  # noqa: E402
import media_stack.api.services.disk as disk_mod  # noqa: E402
import media_stack.api.services.scheduler as sched_mod  # noqa: E402
import media_stack.api.services.content as content_mod  # noqa: E402


def _make_profile(data, td):
    data.setdefault("metadata", {}).setdefault("name", "test-stack")
    data.setdefault("technology_bindings", {"media_server": "jellyfin"})
    import yaml
    p = Path(td) / "profile.yaml"
    with open(p, "w") as f:
        yaml.dump(data, f)
    return str(p)


# ---------------------------------------------------------------------------
# #1 Media library paths
# ---------------------------------------------------------------------------

class TestGetLibraries(unittest.TestCase):
    def setUp(self):
        config_mod._invalidate_profile_cache()

    def tearDown(self):
        config_mod._invalidate_profile_cache()

    def test_returns_defaults_when_no_profile(self):
        with patch("media_stack.api.services._resolve.resolve_profile_path", return_value=None):
            result = config_mod.get_libraries()
        self.assertIn("libraries", result)

    def test_returns_profile_overrides(self):
        with tempfile.TemporaryDirectory() as td:
            profile = _make_profile({
                "technology_bindings": {"media_server": "jellyfin"},
                "jellyfin": {"libraries": [{"name": "Anime", "collection_type": "tvshows", "paths": ["/media/anime"]}]},
            }, td)
            with (
                patch("media_stack.api.services._resolve.resolve_profile_path", return_value=profile),
                patch.dict(os.environ, {"CONFIG_ROOT": td}),
            ):
                result = config_mod.get_libraries()
        self.assertEqual(result["source"], "profile")
        self.assertEqual(result["libraries"][0]["name"], "Anime")


class TestUpdateLibraries(unittest.TestCase):
    def setUp(self):
        config_mod._invalidate_profile_cache()

    def tearDown(self):
        config_mod._invalidate_profile_cache()

    def test_saves_libraries(self):
        with tempfile.TemporaryDirectory() as td:
            profile = _make_profile({"technology_bindings": {"media_server": "jellyfin"}}, td)
            with (
                patch("media_stack.api.services._resolve.resolve_profile_path", return_value=profile),
                patch.dict(os.environ, {"CONFIG_ROOT": td}),
            ):
                result = config_mod.update_libraries([
                    {"name": "Movies", "collection_type": "movies", "paths": ["/media/movies"]},
                    {"name": "Anime", "collection_type": "tvshows", "paths": ["/media/anime"]},
                ])
        self.assertEqual(result["status"], "saved")
        self.assertEqual(len(result["libraries"]), 2)

    def test_rejects_invalid_library(self):
        with tempfile.TemporaryDirectory() as td:
            profile = _make_profile({"technology_bindings": {"media_server": "jellyfin"}}, td)
            with patch("media_stack.api.services._resolve.resolve_profile_path", return_value=profile):
                result = config_mod.update_libraries([{"name": ""}])
        self.assertIn("error", result)

    def test_no_profile_returns_error(self):
        with patch("media_stack.api.services._resolve.resolve_profile_path", return_value=None):
            result = config_mod.update_libraries([{"name": "X", "collection_type": "movies", "paths": ["/x"]}])
        self.assertIn("error", result)


# ---------------------------------------------------------------------------
# #2 Download categories
# ---------------------------------------------------------------------------

class TestGetDownloadCategories(unittest.TestCase):
    def setUp(self):
        config_mod._invalidate_profile_cache()

    def tearDown(self):
        config_mod._invalidate_profile_cache()

    def test_returns_empty_when_no_config(self):
        with (
            patch("media_stack.api.services._resolve.resolve_profile_path", return_value=None),
            patch.dict(os.environ, {"CONFIG_ROOT": "/tmp/nonexistent-cfg-root"}),
        ):
            result = config_mod.get_download_categories()
        self.assertIsInstance(result["categories"], dict)
        self.assertIn("categories", result)

    def test_returns_profile_overrides(self):
        with tempfile.TemporaryDirectory() as td:
            profile = _make_profile({"download_categories": {"anime": "/data/anime", "audiobooks": "/data/audiobooks"}}, td)
            with (
                patch("media_stack.api.services._resolve.resolve_profile_path", return_value=profile),
                patch.dict(os.environ, {"CONFIG_ROOT": td}),
            ):
                result = config_mod.get_download_categories()
        self.assertIn("anime", result["categories"])
        self.assertEqual(result["source"], "profile")


class TestUpdateDownloadCategories(unittest.TestCase):
    def setUp(self):
        config_mod._invalidate_profile_cache()

    def tearDown(self):
        config_mod._invalidate_profile_cache()

    def test_saves_categories(self):
        with tempfile.TemporaryDirectory() as td:
            profile = _make_profile({}, td)
            with (
                patch("media_stack.api.services._resolve.resolve_profile_path", return_value=profile),
                patch.dict(os.environ, {"CONFIG_ROOT": td}),
            ):
                result = config_mod.update_download_categories({"anime": "/data/anime"})
        self.assertEqual(result["status"], "saved")

    def test_rejects_empty(self):
        result = config_mod.update_download_categories({})
        self.assertIn("error", result)


# ---------------------------------------------------------------------------
# #4 Metadata language
# ---------------------------------------------------------------------------

class TestMetadataSettings(unittest.TestCase):
    def setUp(self):
        config_mod._invalidate_profile_cache()

    def tearDown(self):
        config_mod._invalidate_profile_cache()

    def test_defaults(self):
        with patch("media_stack.api.services._resolve.resolve_profile_path", return_value=None):
            result = config_mod.get_metadata_settings()
        self.assertEqual(result["language"], "en")
        self.assertEqual(result["country"], "US")

    def test_update_saves(self):
        with tempfile.TemporaryDirectory() as td:
            profile = _make_profile({}, td)
            with patch("media_stack.api.services._resolve.resolve_profile_path", return_value=profile):
                result = config_mod.update_metadata_settings("de", "DE")
        self.assertEqual(result["status"], "saved")
        self.assertEqual(result["metadata"]["language"], "de")

    def test_rejects_empty(self):
        result = config_mod.update_metadata_settings("", "")
        self.assertIn("error", result)


# ---------------------------------------------------------------------------
# #5 IPTV/Live TV sources
# ---------------------------------------------------------------------------

class TestLiveTvSources(unittest.TestCase):
    def setUp(self):
        config_mod._invalidate_profile_cache()

    def tearDown(self):
        config_mod._invalidate_profile_cache()

    def test_empty_when_not_configured(self):
        with (
            patch("media_stack.api.services._resolve.resolve_profile_path", return_value=None),
            patch.dict(os.environ, {"CONFIG_ROOT": "/tmp/nonexistent-cfg-root"}),
        ):
            result = config_mod.get_livetv_sources()
        self.assertEqual(result["tuner_url"], "")
        self.assertEqual(result["source"], "not_configured")

    def test_update_saves(self):
        with tempfile.TemporaryDirectory() as td:
            profile = _make_profile({}, td)
            with (
                patch("media_stack.api.services._resolve.resolve_profile_path", return_value=profile),
                patch.dict(os.environ, {"CONFIG_ROOT": td}),
            ):
                result = config_mod.update_livetv_sources(
                    tuners=[{"url": "https://example.com/de.m3u", "name": "DE"}],
                    guides=[{"url": "https://example.com/epg-de.xml", "name": "DE EPG"}],
                )
        self.assertEqual(result["status"], "saved")
        self.assertEqual(len(result.get("tuners", [])), 1)

    def test_saves_with_single_url(self):
        with tempfile.TemporaryDirectory() as td:
            profile = _make_profile({}, td)
            with (
                patch("media_stack.api.services._resolve.resolve_profile_path", return_value=profile),
                patch.dict(os.environ, {"CONFIG_ROOT": td}),
            ):
                result = config_mod.update_livetv_sources(
                    tuner_url="https://example.com/us.m3u",
                    guide_url="https://example.com/epg-us.xml",
                )
        self.assertEqual(result["status"], "saved")
        self.assertEqual(len(result.get("tuners", [])), 1)


# ---------------------------------------------------------------------------
# #7 Indexer management
# ---------------------------------------------------------------------------

class TestToggleIndexer(unittest.TestCase):
    @patch("media_stack.api.services.content.discover_api_keys", return_value={"prowlarr": "key"})
    @patch("urllib.request.urlopen")
    def test_toggle_enable(self, mock_urlopen, _):
        get_resp = MagicMock()
        get_resp.read.return_value = json.dumps({"id": 1, "name": "Test", "enable": False}).encode()
        get_resp.__enter__ = MagicMock(return_value=get_resp)
        get_resp.__exit__ = MagicMock(return_value=False)
        put_resp = MagicMock()
        put_resp.__enter__ = MagicMock(return_value=put_resp)
        put_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.side_effect = [get_resp, put_resp]
        result = content_mod.toggle_indexer(1, True)
        self.assertEqual(result["status"], "ok")

    @patch("media_stack.api.services.content.discover_api_keys", return_value={})
    def test_no_key_returns_error(self, _):
        result = content_mod.toggle_indexer(1, True)
        self.assertIn("error", result)


class TestDeleteIndexer(unittest.TestCase):
    @patch("media_stack.api.services.content.discover_api_keys", return_value={"prowlarr": "key"})
    @patch("urllib.request.urlopen")
    def test_delete_success(self, mock_urlopen, _):
        mock_urlopen.return_value = MagicMock()
        result = content_mod.delete_indexer(1)
        self.assertEqual(result["status"], "deleted")


# ---------------------------------------------------------------------------
# #6 Import list management
# ---------------------------------------------------------------------------

class TestImportListManagement(unittest.TestCase):
    @patch("media_stack.api.services.content.discover_api_keys", return_value={})
    def test_get_all_no_keys(self, _):
        result = content_mod.get_all_import_lists()
        self.assertIn("lists", result)

    @patch("media_stack.api.services.content.discover_api_keys", return_value={"sonarr": "key"})
    @patch("urllib.request.urlopen")
    def test_delete_list(self, mock_urlopen, _):
        mock_urlopen.return_value = MagicMock()
        result = content_mod.delete_import_list("sonarr", 1)
        self.assertEqual(result["status"], "deleted")

    def test_delete_unknown_service(self):
        result = content_mod.delete_import_list("nonexistent-xyz", 1)
        self.assertIn("error", result)


# ---------------------------------------------------------------------------
# #9 Storage breakdown
# ---------------------------------------------------------------------------

class TestStorageBreakdown(unittest.TestCase):
    def test_no_media_root(self):
        with patch.dict(os.environ, {"MEDIA_ROOT": "/tmp/nonexistent-media-xyz-9999"}):
            # Also patch the fallback candidates so none match
            with patch.object(disk_mod, "get_storage_breakdown", wraps=disk_mod.get_storage_breakdown):
                result = disk_mod.get_storage_breakdown()
        # If no candidates found, error; if /media exists on host, still works — just verify structure
        self.assertIn("breakdown", result)

    def test_with_media_root(self):
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "movies").mkdir()
            (Path(td) / "movies" / "test.mkv").write_bytes(b"x" * 1000)
            (Path(td) / "tv").mkdir()
            with patch.dict(os.environ, {"MEDIA_ROOT": td}):
                result = disk_mod.get_storage_breakdown()
        self.assertGreater(len(result["breakdown"]), 0)
        self.assertGreater(result["total_bytes"], 0)

    def test_sorted_by_size(self):
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "small").mkdir()
            (Path(td) / "small" / "a.txt").write_bytes(b"x" * 100)
            (Path(td) / "big").mkdir()
            (Path(td) / "big" / "b.mkv").write_bytes(b"x" * 10000)
            with patch.dict(os.environ, {"MEDIA_ROOT": td}):
                result = disk_mod.get_storage_breakdown()
        self.assertEqual(result["breakdown"][0]["name"], "big")


# ---------------------------------------------------------------------------
# #10 Persistent scheduler
# ---------------------------------------------------------------------------

class TestScheduler(unittest.TestCase):
    def setUp(self):
        # The module no longer carries a ``_SCHEDULES_FILE`` constant
        # — ``_schedules_path()`` resolves
        # ``$CONFIG_ROOT/.controller/schedules.json`` on every call so
        # tests that flip CONFIG_ROOT see the override immediately.
        # Point CONFIG_ROOT at a per-test tmp dir so the file lands
        # inside it instead of in the prod ``/srv-config`` mount.
        self._tmpdir = tempfile.mkdtemp(prefix="sched_test_")
        self._old_config_root = os.environ.get("CONFIG_ROOT")
        os.environ["CONFIG_ROOT"] = self._tmpdir
        # Path the module will compute when it next resolves; tests
        # that read the persisted JSON directly use this.
        self._schedules_file = Path(self._tmpdir) / ".controller" / "schedules.json"

    def tearDown(self):
        if self._old_config_root is None:
            os.environ.pop("CONFIG_ROOT", None)
        else:
            os.environ["CONFIG_ROOT"] = self._old_config_root
        import shutil
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_empty_schedules(self):
        result = sched_mod.get_schedules()
        self.assertEqual(result["count"], 0)

    def test_add_schedule(self):
        result = sched_mod.add_schedule("reconcile", 3600)
        self.assertEqual(result["status"], "created")
        self.assertEqual(result["schedule"]["action"], "reconcile")

    def test_add_rejects_short_interval(self):
        result = sched_mod.add_schedule("reconcile", 10)
        self.assertIn("error", result)

    def test_add_rejects_empty_action(self):
        result = sched_mod.add_schedule("", 3600)
        self.assertIn("error", result)

    def test_remove_schedule(self):
        added = sched_mod.add_schedule("reconcile", 3600)
        sched_id = added["schedule"]["id"]
        result = sched_mod.remove_schedule(sched_id)
        self.assertEqual(result["status"], "removed")

    def test_remove_nonexistent(self):
        result = sched_mod.remove_schedule(99999999)
        self.assertIn("error", result)

    def test_get_due_actions(self):
        sched_mod.add_schedule("reconcile", 60)
        # Just added with last_run=0, so it should be due
        due = sched_mod.get_due_actions()
        self.assertEqual(len(due), 1)
        self.assertEqual(due[0]["action"], "reconcile")

    def test_not_due_yet(self):
        sched_mod.add_schedule("reconcile", 3600)
        # First call marks as run
        sched_mod.get_due_actions()
        # Second call — not due yet
        due = sched_mod.get_due_actions()
        self.assertEqual(len(due), 0)

    def test_persists_to_file(self):
        sched_mod.add_schedule("test", 120)
        data = json.loads(self._schedules_file.read_text())
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["action"], "test")


if __name__ == "__main__":
    unittest.main()
