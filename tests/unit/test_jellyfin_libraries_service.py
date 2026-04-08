"""Tests for JellyfinLibrariesService."""

import sys
import json
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.services.apps.jellyfin.libraries_service import (  # noqa: E402
    JellyfinLibrariesDependencies,
    JellyfinLibrariesService,
)


def _make_deps(**overrides):
    defaults = {
        "log": mock.Mock(),
        "bool_cfg": lambda cfg, key, default: cfg.get(key, default),
        "coerce_list": lambda v: list(v) if isinstance(v, (list, tuple)) else ([v] if v else []),
        "normalize_url": lambda u: u.rstrip("/"),
        "wait_for_service": mock.Mock(),
        "resolve_api_key": mock.Mock(return_value="test-api-key"),
        "jellyfin_request": mock.Mock(return_value=(200, [], "")),
        "build_query_path": lambda path, params: path + "?" + "&".join(
            f"{k}={v}" for k, v in params.items()
        ),
        "reorder_provider_names": lambda names, priority: (
            [p for p in priority if p in names] + [n for n in names if n not in priority]
        ),
        "apply_artwork_profile": lambda options, supported, profile: options or [],
    }
    defaults.update(overrides)
    return JellyfinLibrariesDependencies(**defaults)


def _make_service(**overrides):
    deps = _make_deps(**overrides)
    return JellyfinLibrariesService(deps=deps), deps


# ---------------------------------------------------------------------------
# _normalize_names
# ---------------------------------------------------------------------------


class TestNormalizeNames(unittest.TestCase):
    def test_basic_dedup(self):
        result = JellyfinLibrariesService._normalize_names(["Foo", "foo", "Bar"])
        self.assertEqual(result, ["Foo", "Bar"])

    def test_strips_whitespace(self):
        result = JellyfinLibrariesService._normalize_names(["  Foo  ", "Bar  "])
        self.assertEqual(result, ["Foo", "Bar"])

    def test_skips_empty(self):
        result = JellyfinLibrariesService._normalize_names(["", "  ", None, "Valid"])
        self.assertEqual(result, ["Valid"])

    def test_preserves_order(self):
        result = JellyfinLibrariesService._normalize_names(["C", "A", "B"])
        self.assertEqual(result, ["C", "A", "B"])

    def test_empty_list(self):
        result = JellyfinLibrariesService._normalize_names([])
        self.assertEqual(result, [])

    def test_all_duplicates(self):
        result = JellyfinLibrariesService._normalize_names(["A", "a", "A"])
        self.assertEqual(result, ["A"])


# ---------------------------------------------------------------------------
# _names_from_option_info
# ---------------------------------------------------------------------------


class TestNamesFromOptionInfo(unittest.TestCase):
    def test_extracts_names(self):
        svc, _ = _make_service()
        entries = [{"Name": "TMDb"}, {"Name": "Fanart"}, {"Name": "TMDb"}]
        result = svc._names_from_option_info(entries)
        self.assertEqual(result, ["TMDb", "Fanart"])

    def test_uses_lowercase_name_key(self):
        svc, _ = _make_service()
        entries = [{"name": "Provider1"}]
        result = svc._names_from_option_info(entries)
        self.assertEqual(result, ["Provider1"])

    def test_skips_non_dict(self):
        svc, _ = _make_service()
        entries = ["string", 42, {"Name": "Valid"}]
        result = svc._names_from_option_info(entries)
        self.assertEqual(result, ["Valid"])

    def test_skips_empty_names(self):
        svc, _ = _make_service()
        entries = [{"Name": ""}, {"Name": "  "}, {"Name": "Good"}]
        result = svc._names_from_option_info(entries)
        self.assertEqual(result, ["Good"])


# ---------------------------------------------------------------------------
# _default_image_options
# ---------------------------------------------------------------------------


class TestDefaultImageOptions(unittest.TestCase):
    def test_extracts_type_limit_minwidth(self):
        entries = [{"Type": "Backdrop", "Limit": 3, "MinWidth": 1280}]
        result = JellyfinLibrariesService._default_image_options(entries)
        self.assertEqual(result, [{"Type": "Backdrop", "Limit": 3, "MinWidth": 1280}])

    def test_defaults_to_zero(self):
        entries = [{"Type": "Primary"}]
        result = JellyfinLibrariesService._default_image_options(entries)
        self.assertEqual(result, [{"Type": "Primary", "Limit": 0, "MinWidth": 0}])

    def test_skips_non_dict(self):
        entries = ["invalid", {"Type": "Logo", "Limit": 1, "MinWidth": 0}]
        result = JellyfinLibrariesService._default_image_options(entries)
        self.assertEqual(len(result), 1)

    def test_skips_empty_type(self):
        entries = [{"Type": "", "Limit": 1}]
        result = JellyfinLibrariesService._default_image_options(entries)
        self.assertEqual(result, [])

    def test_none_limit_defaults(self):
        entries = [{"Type": "Thumb", "Limit": None, "MinWidth": None}]
        result = JellyfinLibrariesService._default_image_options(entries)
        self.assertEqual(result, [{"Type": "Thumb", "Limit": 0, "MinWidth": 0}])


# ---------------------------------------------------------------------------
# _normalize_type_options
# ---------------------------------------------------------------------------


class TestNormalizeTypeOptions(unittest.TestCase):
    def test_basic_normalization(self):
        svc, _ = _make_service()
        entries = [{
            "Type": "Movie",
            "MetadataFetchers": ["TMDb", "OMDB"],
            "MetadataFetcherOrder": ["TMDb", "OMDB"],
            "ImageFetchers": ["TMDb"],
            "ImageFetcherOrder": ["TMDb"],
            "ImageOptions": [{"Type": "Backdrop", "Limit": 3, "MinWidth": 1280}],
        }]
        result = svc._normalize_type_options(entries)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["Type"], "Movie")
        self.assertEqual(result[0]["MetadataFetchers"], ["TMDb", "OMDB"])

    def test_lowercase_keys(self):
        svc, _ = _make_service()
        entries = [{
            "type": "Series",
            "metadataFetchers": ["A"],
            "metadataFetcherOrder": ["A"],
            "imageFetchers": ["B"],
            "imageFetcherOrder": ["B"],
            "imageOptions": [],
        }]
        result = svc._normalize_type_options(entries)
        self.assertEqual(result[0]["Type"], "Series")
        self.assertEqual(result[0]["MetadataFetchers"], ["A"])

    def test_skips_non_dict(self):
        svc, _ = _make_service()
        entries = ["invalid", {"Type": "Movie", "MetadataFetchers": []}]
        result = svc._normalize_type_options(entries)
        self.assertEqual(len(result), 1)

    def test_skips_empty_type(self):
        svc, _ = _make_service()
        entries = [{"Type": "", "MetadataFetchers": []}]
        result = svc._normalize_type_options(entries)
        self.assertEqual(result, [])


# ---------------------------------------------------------------------------
# _type_options_from_available_payload
# ---------------------------------------------------------------------------


class TestTypeOptionsFromAvailablePayload(unittest.TestCase):
    def test_extracts_from_payload(self):
        svc, _ = _make_service()
        payload = {
            "TypeOptions": [{
                "Type": "Movie",
                "MetadataFetchers": [{"Name": "TMDb"}, {"Name": "OMDB"}],
                "ImageFetchers": [{"Name": "Fanart"}],
                "DefaultImageOptions": [{"Type": "Backdrop", "Limit": 3, "MinWidth": 1280}],
                "SupportedImageTypes": ["Backdrop", "Primary"],
            }],
        }
        result = svc._type_options_from_available_payload(payload)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["Type"], "Movie")
        self.assertEqual(result[0]["MetadataFetchers"], ["TMDb", "OMDB"])
        self.assertEqual(result[0]["ImageFetchers"], ["Fanart"])
        self.assertEqual(result[0]["_supported_image_types"], ["Backdrop", "Primary"])

    def test_empty_payload(self):
        svc, _ = _make_service()
        result = svc._type_options_from_available_payload({})
        self.assertEqual(result, [])

    def test_skips_entries_without_type(self):
        svc, _ = _make_service()
        payload = {"TypeOptions": [{"MetadataFetchers": [{"Name": "X"}]}]}
        result = svc._type_options_from_available_payload(payload)
        self.assertEqual(result, [])


# ---------------------------------------------------------------------------
# _reconcile_type_options
# ---------------------------------------------------------------------------


class TestReconcileTypeOptions(unittest.TestCase):
    def test_merges_current_and_available(self):
        svc, _ = _make_service()
        current = [{
            "Type": "Movie",
            "MetadataFetchers": ["TMDb"],
            "MetadataFetcherOrder": ["TMDb"],
            "ImageFetchers": ["Fanart"],
            "ImageFetcherOrder": ["Fanart"],
            "ImageOptions": [],
        }]
        available = {
            "TypeOptions": [{
                "Type": "Movie",
                "MetadataFetchers": [{"Name": "TMDb"}, {"Name": "OMDB"}],
                "ImageFetchers": [{"Name": "Fanart"}, {"Name": "TMDb"}],
                "DefaultImageOptions": [],
                "SupportedImageTypes": [],
            }],
        }
        result = svc._reconcile_type_options(current, available, [], [], {})
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["Type"], "Movie")
        self.assertIn("TMDb", result[0]["MetadataFetchers"])
        self.assertIn("OMDB", result[0]["MetadataFetchers"])

    def test_applies_metadata_priority(self):
        svc, _ = _make_service()
        current = [{
            "Type": "Movie",
            "MetadataFetchers": ["A", "B", "C"],
            "MetadataFetcherOrder": ["A", "B", "C"],
            "ImageFetchers": [],
            "ImageFetcherOrder": [],
            "ImageOptions": [],
        }]
        available = {"TypeOptions": []}
        result = svc._reconcile_type_options(current, available, ["C", "A"], [], {})
        order = result[0]["MetadataFetcherOrder"]
        self.assertEqual(order[0], "C")
        self.assertEqual(order[1], "A")

    def test_empty_current_and_available(self):
        svc, _ = _make_service()
        result = svc._reconcile_type_options([], {}, [], [], {})
        self.assertEqual(result, [])

    def test_filters_order_to_only_available_fetchers(self):
        svc, _ = _make_service()
        current = [{
            "Type": "Movie",
            "MetadataFetchers": ["A"],
            "MetadataFetcherOrder": ["A", "B"],
            "ImageFetchers": ["C"],
            "ImageFetcherOrder": ["C", "D"],
            "ImageOptions": [],
        }]
        result = svc._reconcile_type_options(current, {"TypeOptions": []}, [], [], {})
        self.assertNotIn("B", result[0]["MetadataFetcherOrder"])
        self.assertNotIn("D", result[0]["ImageFetcherOrder"])


# ---------------------------------------------------------------------------
# ensure
# ---------------------------------------------------------------------------


class TestEnsure(unittest.TestCase):
    def test_disabled_skips(self):
        svc, deps = _make_service()
        svc.ensure({"jellyfin_libraries": {"enabled": False}}, "/config", 60)
        deps.wait_for_service.assert_not_called()

    def test_missing_section_returns_early(self):
        svc, deps = _make_service()
        svc.ensure({}, "/config", 60)
        deps.wait_for_service.assert_not_called()

    def test_missing_api_key_raises(self):
        svc, _ = _make_service(resolve_api_key=mock.Mock(return_value=""))
        with self.assertRaises(RuntimeError) as ctx:
            svc.ensure({"jellyfin_libraries": {"enabled": True}}, "/config", 60)
        self.assertIn("API key unavailable", str(ctx.exception))

    def test_empty_libraries_list_logs_warning(self):
        svc, deps = _make_service()
        svc.ensure({"jellyfin_libraries": {"enabled": True, "libraries": []}}, "/config", 60)
        deps.log.assert_any_call("[WARN] Jellyfin libraries: enabled but no libraries were declared.")

    def test_listing_virtual_folders_failure_raises(self):
        svc, _ = _make_service(
            jellyfin_request=mock.Mock(return_value=(500, None, "Server error")),
        )
        cfg = {"jellyfin_libraries": {"enabled": True, "libraries": [{"name": "Movies", "paths": ["/movies"]}]}}
        with self.assertRaises(RuntimeError) as ctx:
            svc.ensure(cfg, "/config", 60)
        self.assertIn("failed listing virtual folders", str(ctx.exception))

    def test_library_already_exists_with_matching_paths(self):
        existing = [{"Name": "Movies", "Locations": ["/movies"], "ItemId": "id1", "LibraryOptions": {}}]

        def fake_request(url, path, api_key, **kwargs):
            if path == "/Library/VirtualFolders":
                return 200, existing, ""
            return 200, {}, ""

        svc, deps = _make_service(jellyfin_request=fake_request)
        cfg = {
            "jellyfin_libraries": {
                "enabled": True,
                "libraries": [{"name": "Movies", "collection_type": "movies", "paths": ["/movies"]}],
                "tuning": {"enabled": False},
            }
        }
        svc.ensure(cfg, "/config", 60)
        deps.log.assert_any_call("[OK] Jellyfin libraries: already present: Movies")

    def test_library_exists_with_different_paths_logs_warning(self):
        existing = [{"Name": "Movies", "Locations": ["/old-movies"], "ItemId": "id1", "LibraryOptions": {}}]

        def fake_request(url, path, api_key, **kwargs):
            if path == "/Library/VirtualFolders":
                return 200, existing, ""
            return 200, {}, ""

        svc, deps = _make_service(jellyfin_request=fake_request)
        cfg = {
            "jellyfin_libraries": {
                "enabled": True,
                "libraries": [{"name": "Movies", "collection_type": "movies", "paths": ["/new-movies"]}],
                "tuning": {"enabled": False},
            }
        }
        svc.ensure(cfg, "/config", 60)
        log_messages = [str(c) for c in deps.log.call_args_list]
        self.assertTrue(any("paths differ" in m for m in log_messages))

    def test_creates_library_when_not_exists(self):
        call_count = [0]
        post_paths = []

        def fake_request(url, path, api_key, **kwargs):
            call_count[0] += 1
            if path == "/Library/VirtualFolders" and kwargs.get("method") is None:
                if call_count[0] <= 2:
                    return 200, [], ""
                return 200, [
                    {"Name": "Movies", "Locations": ["/movies"], "ItemId": "new-id",
                     "LibraryOptions": {}, "CollectionType": "movies"},
                ], ""
            if path.startswith("/Library/VirtualFolders?") and kwargs.get("method") == "POST":
                post_paths.append(path)
                return 204, None, ""
            if path.startswith("/Libraries/AvailableOptions"):
                return 200, {}, ""
            if path == "/Library/VirtualFolders/LibraryOptions" and kwargs.get("method") == "POST":
                return 204, None, ""
            if path == "/Library/Refresh":
                return 204, None, ""
            return 200, {}, ""

        svc, deps = _make_service(jellyfin_request=fake_request)
        cfg = {
            "jellyfin_libraries": {
                "enabled": True,
                "libraries": [{"name": "Movies", "collection_type": "movies", "paths": ["/movies"]}],
            }
        }
        svc.ensure(cfg, "/config", 60)
        self.assertTrue(any("name=Movies" in p for p in post_paths))

    def test_create_library_failure_raises(self):
        def fake_request(url, path, api_key, **kwargs):
            if path == "/Library/VirtualFolders" and kwargs.get("method") is None:
                return 200, [], ""
            if path.startswith("/Library/VirtualFolders?"):
                return 500, None, "Creation error"
            return 200, {}, ""

        svc, _ = _make_service(jellyfin_request=fake_request)
        cfg = {
            "jellyfin_libraries": {
                "enabled": True,
                "libraries": [{"name": "Movies", "collection_type": "movies", "paths": ["/movies"]}],
            }
        }
        with self.assertRaises(RuntimeError) as ctx:
            svc.ensure(cfg, "/config", 60)
        self.assertIn("failed creating", str(ctx.exception))

    def test_tuning_disabled_skips_tune(self):
        existing = [{"Name": "Movies", "Locations": ["/movies"], "ItemId": "id1", "LibraryOptions": {"EnableRealtimeMonitor": False}}]

        tune_posts = []

        def fake_request(url, path, api_key, **kwargs):
            if path == "/Library/VirtualFolders":
                return 200, existing, ""
            if path == "/Library/VirtualFolders/LibraryOptions":
                tune_posts.append(kwargs.get("payload"))
                return 204, None, ""
            return 200, {}, ""

        svc, _ = _make_service(jellyfin_request=fake_request)
        cfg = {
            "jellyfin_libraries": {
                "enabled": True,
                "libraries": [{"name": "Movies", "collection_type": "movies", "paths": ["/movies"]}],
                "tuning": {"enabled": False},
            }
        }
        svc.ensure(cfg, "/config", 60)
        self.assertEqual(len(tune_posts), 0)

    def test_tuning_sets_realtime_monitor(self):
        existing = [{
            "Name": "Movies", "Locations": ["/movies"], "ItemId": "id1",
            "CollectionType": "movies",
            "LibraryOptions": {
                "EnableRealtimeMonitor": False,
                "TypeOptions": [],
            },
        }]
        tune_payloads = []

        def fake_request(url, path, api_key, **kwargs):
            if path == "/Library/VirtualFolders" and kwargs.get("method") is None:
                return 200, existing, ""
            if path.startswith("/Libraries/AvailableOptions"):
                return 200, {"TypeOptions": []}, ""
            if path == "/Library/VirtualFolders/LibraryOptions" and kwargs.get("method") == "POST":
                tune_payloads.append(kwargs.get("payload"))
                return 204, None, ""
            if path == "/Library/Refresh":
                return 204, None, ""
            return 200, {}, ""

        svc, _ = _make_service(jellyfin_request=fake_request)
        cfg = {
            "jellyfin_libraries": {
                "enabled": True,
                "libraries": [{"name": "Movies", "collection_type": "movies", "paths": ["/movies"]}],
                "tuning": {"enabled": True, "enable_realtime_monitor": True},
            }
        }
        svc.ensure(cfg, "/config", 60)
        self.assertTrue(len(tune_payloads) >= 1)
        self.assertTrue(tune_payloads[0]["LibraryOptions"]["EnableRealtimeMonitor"])

    def test_tuning_sets_trickplay_movies(self):
        existing = [{
            "Name": "Movies", "Locations": ["/movies"], "ItemId": "id1",
            "CollectionType": "movies",
            "LibraryOptions": {
                "EnableTrickplayImageExtraction": False,
                "ExtractTrickplayImagesDuringLibraryScan": False,
                "TypeOptions": [],
            },
        }]
        tune_payloads = []

        def fake_request(url, path, api_key, **kwargs):
            if path == "/Library/VirtualFolders" and kwargs.get("method") is None:
                return 200, existing, ""
            if path.startswith("/Libraries/AvailableOptions"):
                return 200, {"TypeOptions": []}, ""
            if path == "/Library/VirtualFolders/LibraryOptions" and kwargs.get("method") == "POST":
                tune_payloads.append(kwargs.get("payload"))
                return 204, None, ""
            if path == "/Library/Refresh":
                return 204, None, ""
            return 200, {}, ""

        svc, _ = _make_service(jellyfin_request=fake_request)
        cfg = {
            "jellyfin_libraries": {
                "enabled": True,
                "libraries": [{"name": "Movies", "collection_type": "movies", "paths": ["/movies"]}],
                "tuning": {"enabled": True, "enable_preview_thumbnails_movies": True},
            }
        }
        svc.ensure(cfg, "/config", 60)
        self.assertTrue(len(tune_payloads) >= 1)
        opts = tune_payloads[0]["LibraryOptions"]
        self.assertTrue(opts["EnableTrickplayImageExtraction"])
        self.assertTrue(opts["ExtractTrickplayImagesDuringLibraryScan"])

    def test_tuning_sets_trickplay_tv(self):
        existing = [{
            "Name": "TV", "Locations": ["/tv"], "ItemId": "id2",
            "CollectionType": "tvshows",
            "LibraryOptions": {
                "EnableTrickplayImageExtraction": False,
                "ExtractTrickplayImagesDuringLibraryScan": False,
                "TypeOptions": [],
            },
        }]
        tune_payloads = []

        def fake_request(url, path, api_key, **kwargs):
            if path == "/Library/VirtualFolders" and kwargs.get("method") is None:
                return 200, existing, ""
            if path.startswith("/Libraries/AvailableOptions"):
                return 200, {"TypeOptions": []}, ""
            if path == "/Library/VirtualFolders/LibraryOptions" and kwargs.get("method") == "POST":
                tune_payloads.append(kwargs.get("payload"))
                return 204, None, ""
            if path == "/Library/Refresh":
                return 204, None, ""
            return 200, {}, ""

        svc, _ = _make_service(jellyfin_request=fake_request)
        cfg = {
            "jellyfin_libraries": {
                "enabled": True,
                "libraries": [{"name": "TV", "collection_type": "tvshows", "paths": ["/tv"]}],
                "tuning": {"enabled": True, "enable_preview_thumbnails_tv": True},
            }
        }
        svc.ensure(cfg, "/config", 60)
        self.assertTrue(len(tune_payloads) >= 1)
        opts = tune_payloads[0]["LibraryOptions"]
        self.assertTrue(opts["EnableTrickplayImageExtraction"])

    def test_tuning_no_change_logs_match(self):
        existing = [{
            "Name": "Movies", "Locations": ["/movies"], "ItemId": "id1",
            "CollectionType": "movies",
            "LibraryOptions": {"TypeOptions": []},
        }]

        def fake_request(url, path, api_key, **kwargs):
            if path == "/Library/VirtualFolders" and kwargs.get("method") is None:
                return 200, existing, ""
            if path.startswith("/Libraries/AvailableOptions"):
                return 200, {"TypeOptions": []}, ""
            return 200, {}, ""

        svc, deps = _make_service(jellyfin_request=fake_request)
        cfg = {
            "jellyfin_libraries": {
                "enabled": True,
                "libraries": [{"name": "Movies", "collection_type": "movies", "paths": ["/movies"]}],
                "tuning": {"enabled": True},
            }
        }
        svc.ensure(cfg, "/config", 60)
        log_messages = [str(c) for c in deps.log.call_args_list]
        self.assertTrue(any("already matches" in m for m in log_messages))

    def test_tune_update_failure_raises(self):
        existing = [{
            "Name": "Movies", "Locations": ["/movies"], "ItemId": "id1",
            "CollectionType": "movies",
            "LibraryOptions": {
                "EnableRealtimeMonitor": False,
                "TypeOptions": [],
            },
        }]

        def fake_request(url, path, api_key, **kwargs):
            if path == "/Library/VirtualFolders" and kwargs.get("method") is None:
                return 200, existing, ""
            if path.startswith("/Libraries/AvailableOptions"):
                return 200, {"TypeOptions": []}, ""
            if path == "/Library/VirtualFolders/LibraryOptions":
                return 500, None, "Failed"
            return 200, {}, ""

        svc, _ = _make_service(jellyfin_request=fake_request)
        cfg = {
            "jellyfin_libraries": {
                "enabled": True,
                "libraries": [{"name": "Movies", "collection_type": "movies", "paths": ["/movies"]}],
                "tuning": {"enabled": True},
            }
        }
        with self.assertRaises(RuntimeError) as ctx:
            svc.ensure(cfg, "/config", 60)
        self.assertIn("failed updating options", str(ctx.exception))

    def test_scan_triggered_after_changes(self):
        call_count = [0]
        refresh_called = [False]

        def fake_request(url, path, api_key, **kwargs):
            call_count[0] += 1
            if path == "/Library/VirtualFolders" and kwargs.get("method") is None:
                if call_count[0] <= 2:
                    return 200, [], ""
                return 200, [
                    {"Name": "Movies", "Locations": ["/movies"], "ItemId": "new-id",
                     "CollectionType": "movies", "LibraryOptions": {"TypeOptions": []}},
                ], ""
            if path.startswith("/Library/VirtualFolders?") and kwargs.get("method") == "POST":
                return 204, None, ""
            if path.startswith("/Libraries/AvailableOptions"):
                return 200, {"TypeOptions": []}, ""
            if path == "/Library/VirtualFolders/LibraryOptions" and kwargs.get("method") == "POST":
                return 204, None, ""
            if path == "/Library/Refresh" and kwargs.get("method") == "POST":
                refresh_called[0] = True
                return 204, None, ""
            return 200, {}, ""

        svc, _ = _make_service(jellyfin_request=fake_request)
        cfg = {
            "jellyfin_libraries": {
                "enabled": True,
                "libraries": [{"name": "Movies", "collection_type": "movies", "paths": ["/movies"]}],
                "tuning": {"enabled": True, "scan_all_libraries_after_reconcile": True},
            }
        }
        svc.ensure(cfg, "/config", 60)
        self.assertTrue(refresh_called[0])

    def test_scan_not_triggered_when_no_changes(self):
        existing = [{
            "Name": "Movies", "Locations": ["/movies"], "ItemId": "id1",
            "CollectionType": "movies",
            "LibraryOptions": {"TypeOptions": []},
        }]
        refresh_called = [False]

        def fake_request(url, path, api_key, **kwargs):
            if path == "/Library/VirtualFolders":
                return 200, existing, ""
            if path.startswith("/Libraries/AvailableOptions"):
                return 200, {"TypeOptions": []}, ""
            if path == "/Library/Refresh":
                refresh_called[0] = True
                return 204, None, ""
            return 200, {}, ""

        svc, _ = _make_service(jellyfin_request=fake_request)
        cfg = {
            "jellyfin_libraries": {
                "enabled": True,
                "libraries": [{"name": "Movies", "collection_type": "movies", "paths": ["/movies"]}],
                "tuning": {"enabled": True, "scan_all_libraries_after_reconcile": True},
            }
        }
        svc.ensure(cfg, "/config", 60)
        self.assertFalse(refresh_called[0])

    def test_missing_item_id_skips_tune(self):
        existing = [{
            "Name": "Movies", "Locations": ["/movies"], "ItemId": "",
            "LibraryOptions": {"TypeOptions": []},
        }]

        def fake_request(url, path, api_key, **kwargs):
            if path == "/Library/VirtualFolders":
                return 200, existing, ""
            return 200, {}, ""

        svc, deps = _make_service(jellyfin_request=fake_request)
        cfg = {
            "jellyfin_libraries": {
                "enabled": True,
                "libraries": [{"name": "Movies", "collection_type": "movies", "paths": ["/movies"]}],
                "tuning": {"enabled": True},
            }
        }
        svc.ensure(cfg, "/config", 60)
        log_messages = [str(c) for c in deps.log.call_args_list]
        self.assertTrue(any("missing ItemId" in m for m in log_messages))

    def test_missing_library_options_skips_tune(self):
        existing = [{
            "Name": "Movies", "Locations": ["/movies"], "ItemId": "id1",
        }]

        def fake_request(url, path, api_key, **kwargs):
            if path == "/Library/VirtualFolders":
                return 200, existing, ""
            return 200, {}, ""

        svc, deps = _make_service(jellyfin_request=fake_request)
        cfg = {
            "jellyfin_libraries": {
                "enabled": True,
                "libraries": [{"name": "Movies", "collection_type": "movies", "paths": ["/movies"]}],
                "tuning": {"enabled": True},
            }
        }
        svc.ensure(cfg, "/config", 60)
        log_messages = [str(c) for c in deps.log.call_args_list]
        self.assertTrue(any("missing LibraryOptions" in m for m in log_messages))

    def test_skip_library_without_name(self):
        def fake_request(url, path, api_key, **kwargs):
            if path == "/Library/VirtualFolders":
                return 200, [], ""
            return 200, {}, ""

        svc, deps = _make_service(jellyfin_request=fake_request)
        cfg = {
            "jellyfin_libraries": {
                "enabled": True,
                "libraries": [{"name": "", "paths": ["/movies"]}],
            }
        }
        svc.ensure(cfg, "/config", 60)
        final_log = deps.log.call_args_list[-1][0][0]
        self.assertIn("added=0", final_log)

    def test_skip_library_without_paths(self):
        def fake_request(url, path, api_key, **kwargs):
            if path == "/Library/VirtualFolders":
                return 200, [], ""
            return 200, {}, ""

        svc, deps = _make_service(jellyfin_request=fake_request)
        cfg = {
            "jellyfin_libraries": {
                "enabled": True,
                "libraries": [{"name": "Movies", "paths": []}],
            }
        }
        svc.ensure(cfg, "/config", 60)
        final_log = deps.log.call_args_list[-1][0][0]
        self.assertIn("added=0", final_log)

    def test_skip_non_dict_library_entry(self):
        def fake_request(url, path, api_key, **kwargs):
            if path == "/Library/VirtualFolders":
                return 200, [], ""
            return 200, {}, ""

        svc, deps = _make_service(jellyfin_request=fake_request)
        cfg = {
            "jellyfin_libraries": {
                "enabled": True,
                "libraries": ["not-a-dict"],
            }
        }
        svc.ensure(cfg, "/config", 60)
        final_log = deps.log.call_args_list[-1][0][0]
        self.assertIn("added=0", final_log)

    def test_path_trailing_slash_normalization(self):
        existing = [{"Name": "Movies", "Locations": ["/movies/"], "ItemId": "id1", "LibraryOptions": {}}]

        def fake_request(url, path, api_key, **kwargs):
            if path == "/Library/VirtualFolders":
                return 200, existing, ""
            return 200, {}, ""

        svc, deps = _make_service(jellyfin_request=fake_request)
        cfg = {
            "jellyfin_libraries": {
                "enabled": True,
                "libraries": [{"name": "Movies", "collection_type": "movies", "paths": ["/movies"]}],
                "tuning": {"enabled": False},
            }
        }
        svc.ensure(cfg, "/config", 60)
        deps.log.assert_any_call("[OK] Jellyfin libraries: already present: Movies")

    def test_library_name_case_insensitive(self):
        existing = [{"Name": "MOVIES", "Locations": ["/movies"], "ItemId": "id1", "LibraryOptions": {}}]

        def fake_request(url, path, api_key, **kwargs):
            if path == "/Library/VirtualFolders":
                return 200, existing, ""
            return 200, {}, ""

        svc, deps = _make_service(jellyfin_request=fake_request)
        cfg = {
            "jellyfin_libraries": {
                "enabled": True,
                "libraries": [{"name": "movies", "collection_type": "movies", "paths": ["/movies"]}],
                "tuning": {"enabled": False},
            }
        }
        svc.ensure(cfg, "/config", 60)
        deps.log.assert_any_call("[OK] Jellyfin libraries: already present: movies")

    def test_available_options_cache(self):
        """Second library with same collection_type should use cached available options."""
        existing = [
            {"Name": "Movies", "Locations": ["/movies"], "ItemId": "id1",
             "CollectionType": "movies", "LibraryOptions": {"TypeOptions": []}},
            {"Name": "More Movies", "Locations": ["/more-movies"], "ItemId": "id2",
             "CollectionType": "movies", "LibraryOptions": {"TypeOptions": []}},
        ]
        available_calls = []

        def fake_request(url, path, api_key, **kwargs):
            if path == "/Library/VirtualFolders":
                return 200, existing, ""
            if path.startswith("/Libraries/AvailableOptions"):
                available_calls.append(path)
                return 200, {"TypeOptions": []}, ""
            return 200, {}, ""

        svc, _ = _make_service(jellyfin_request=fake_request)
        cfg = {
            "jellyfin_libraries": {
                "enabled": True,
                "libraries": [
                    {"name": "Movies", "collection_type": "movies", "paths": ["/movies"]},
                    {"name": "More Movies", "collection_type": "movies", "paths": ["/more-movies"]},
                ],
                "tuning": {"enabled": True},
            }
        }
        svc.ensure(cfg, "/config", 60)
        self.assertEqual(len(available_calls), 1)

    def test_refresh_failure_logs_warning(self):
        call_count = [0]

        def fake_request(url, path, api_key, **kwargs):
            call_count[0] += 1
            if path == "/Library/VirtualFolders" and kwargs.get("method") is None:
                if call_count[0] <= 2:
                    return 200, [], ""
                return 200, [
                    {"Name": "Movies", "Locations": ["/movies"], "ItemId": "new-id",
                     "CollectionType": "movies", "LibraryOptions": {"TypeOptions": []}},
                ], ""
            if path.startswith("/Library/VirtualFolders?") and kwargs.get("method") == "POST":
                return 204, None, ""
            if path.startswith("/Libraries/AvailableOptions"):
                return 200, {"TypeOptions": []}, ""
            if path == "/Library/VirtualFolders/LibraryOptions" and kwargs.get("method") == "POST":
                return 204, None, ""
            if path == "/Library/Refresh":
                return 500, None, "Refresh error"
            return 200, {}, ""

        svc, deps = _make_service(jellyfin_request=fake_request)
        cfg = {
            "jellyfin_libraries": {
                "enabled": True,
                "libraries": [{"name": "Movies", "collection_type": "movies", "paths": ["/movies"]}],
                "tuning": {"enabled": True, "scan_all_libraries_after_reconcile": True},
            }
        }
        svc.ensure(cfg, "/config", 60)
        log_messages = [str(c) for c in deps.log.call_args_list]
        self.assertTrue(any("failed to trigger library refresh" in m for m in log_messages))

    def test_default_tuning_values(self):
        """When tuning section is absent, defaults should apply."""
        existing = [{
            "Name": "Movies", "Locations": ["/movies"], "ItemId": "id1",
            "CollectionType": "movies",
            "LibraryOptions": {
                "PreferredMetadataLanguage": "de",
                "MetadataCountryCode": "DE",
                "TypeOptions": [],
            },
        }]
        tune_payloads = []

        def fake_request(url, path, api_key, **kwargs):
            if path == "/Library/VirtualFolders" and kwargs.get("method") is None:
                return 200, existing, ""
            if path.startswith("/Libraries/AvailableOptions"):
                return 200, {"TypeOptions": []}, ""
            if path == "/Library/VirtualFolders/LibraryOptions" and kwargs.get("method") == "POST":
                tune_payloads.append(kwargs.get("payload"))
                return 204, None, ""
            if path == "/Library/Refresh":
                return 204, None, ""
            return 200, {}, ""

        svc, _ = _make_service(jellyfin_request=fake_request)
        cfg = {
            "jellyfin_libraries": {
                "enabled": True,
                "libraries": [{"name": "Movies", "collection_type": "movies", "paths": ["/movies"]}],
            }
        }
        svc.ensure(cfg, "/config", 60)
        self.assertTrue(len(tune_payloads) >= 1)
        opts = tune_payloads[0]["LibraryOptions"]
        self.assertEqual(opts["PreferredMetadataLanguage"], "en")
        self.assertEqual(opts["MetadataCountryCode"], "US")

    def test_wait_for_service_called(self):
        svc, _ = _make_service(resolve_api_key=mock.Mock(return_value=""))
        cfg = {"jellyfin_libraries": {"enabled": True, "url": "http://myhost:9090"}}
        with self.assertRaises(RuntimeError):
            svc.ensure(cfg, "/config", 120)
        svc.deps.wait_for_service.assert_called_once_with(
            "Jellyfin", "http://myhost:9090", "/System/Info/Public", 120,
        )

    def test_reconcile_summary_log(self):
        existing = [{
            "Name": "Movies", "Locations": ["/movies"], "ItemId": "id1",
            "CollectionType": "movies",
            "LibraryOptions": {"TypeOptions": []},
        }]

        def fake_request(url, path, api_key, **kwargs):
            if path == "/Library/VirtualFolders":
                return 200, existing, ""
            if path.startswith("/Libraries/AvailableOptions"):
                return 200, {"TypeOptions": []}, ""
            return 200, {}, ""

        svc, deps = _make_service(jellyfin_request=fake_request)
        cfg = {
            "jellyfin_libraries": {
                "enabled": True,
                "libraries": [{"name": "Movies", "collection_type": "movies", "paths": ["/movies"]}],
                "tuning": {"enabled": True},
            }
        }
        svc.ensure(cfg, "/config", 60)
        final_log = deps.log.call_args_list[-1][0][0]
        self.assertIn("added=0", final_log)
        self.assertIn("tuned=0", final_log)

    def test_reload_folders_failure_after_create_raises(self):
        call_count = [0]

        def fake_request(url, path, api_key, **kwargs):
            call_count[0] += 1
            if path == "/Library/VirtualFolders" and kwargs.get("method") is None:
                if call_count[0] == 1:
                    return 200, [], ""
                return 500, None, "Reload error"
            if path.startswith("/Library/VirtualFolders?") and kwargs.get("method") == "POST":
                return 204, None, ""
            return 200, {}, ""

        svc, _ = _make_service(jellyfin_request=fake_request)
        cfg = {
            "jellyfin_libraries": {
                "enabled": True,
                "libraries": [{"name": "Movies", "collection_type": "movies", "paths": ["/movies"]}],
            }
        }
        with self.assertRaises(RuntimeError) as ctx:
            svc.ensure(cfg, "/config", 60)
        self.assertIn("failed reloading folders", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
