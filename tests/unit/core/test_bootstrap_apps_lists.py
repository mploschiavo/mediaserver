import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

import media_stack.services.apps.servarr.runtime_ops as MODULE


class BootstrapAppImportListPayloadTests(unittest.TestCase):
    def test_defaults_quality_and_metadata_profile_ids(self):
        app_cfg = {
            "name": "Lidarr",
            "implementation": "Lidarr",
            "root_folder": "/media/music",
            "capabilities": {"monitor_scope_all_value": "entireArtist"},
        }
        schema = {
            "implementation": "LastFmTag",
            "implementationName": "LastFmTag",
            "configContract": "LastFmTagSettings",
            "fields": [{"name": "tagId", "value": ""}, {"name": "count", "value": 0}],
            "enabled": False,
            "enableAuto": False,
            "monitor": "none",
            "qualityProfileId": 0,
            "metadataProfileId": 0,
            "searchOnAdd": False,
        }
        list_cfg = {
            "name": "Last.fm Top Rock Artists",
            "field_overrides": {"tagId": "rock", "count": 10},
            "enable_auto": True,
            "monitor": "all",
            "search_on_add": True,
        }

        payload = MODULE.build_arr_import_list_payload(
            app_cfg,
            schema,
            list_cfg,
            default_quality_profile_id=4,
            default_metadata_profile_id=7,
        )

        self.assertEqual(payload["qualityProfileId"], 4)
        self.assertEqual(payload["metadataProfileId"], 7)
        self.assertEqual(payload["rootFolderPath"], "/media/music")
        self.assertTrue(payload["enableAuto"])
        self.assertEqual(payload["monitor"], "all")
        self.assertTrue(payload["searchOnAdd"])

    def test_explicit_profile_ids_override_defaults(self):
        app_cfg = {
            "name": "Readarr",
            "implementation": "Readarr",
            "root_folder": "/media/books",
            "capabilities": {"monitor_scope_all_value": "entireAuthor"},
        }
        schema = {
            "implementation": "GoodreadsListImportList",
            "implementationName": "Goodreads List",
            "configContract": "GoodreadsListImportListSettings",
            "fields": [{"name": "listId", "value": ""}],
            "qualityProfileId": 0,
            "metadataProfileId": 0,
        }
        list_cfg = {
            "name": "Goodreads Best Books Ever",
            "quality_profile_id": 11,
            "metadata_profile_id": 12,
            "field_overrides": {"listId": "1"},
        }

        payload = MODULE.build_arr_import_list_payload(
            app_cfg,
            schema,
            list_cfg,
            default_quality_profile_id=1,
            default_metadata_profile_id=2,
        )

        self.assertEqual(payload["qualityProfileId"], 11)
        self.assertEqual(payload["metadataProfileId"], 12)

    def test_unknown_field_overrides_are_ignored_by_default(self):
        app_cfg = {
            "name": "Readarr",
            "implementation": "Readarr",
            "root_folder": "/media/books",
        }
        schema = {
            "implementation": "GoodreadsListImportList",
            "implementationName": "Goodreads List",
            "configContract": "GoodreadsListImportListSettings",
            "fields": [{"name": "listId", "value": ""}],
        }
        list_cfg = {
            "name": "Readarr Unknown Override Test",
            "field_overrides": {
                "listId": "1",
                "limit": 100,
            },
        }

        payload = MODULE.build_arr_import_list_payload(
            app_cfg,
            schema,
            list_cfg,
            default_quality_profile_id=1,
            default_metadata_profile_id=2,
        )
        fields = {item["name"]: item.get("value") for item in payload["fields"]}
        self.assertIn("listId", fields)
        self.assertNotIn("limit", fields)

    def test_unknown_field_overrides_can_be_opted_in(self):
        app_cfg = {
            "name": "Readarr",
            "implementation": "Readarr",
            "root_folder": "/media/books",
        }
        schema = {
            "implementation": "GoodreadsListImportList",
            "implementationName": "Goodreads List",
            "configContract": "GoodreadsListImportListSettings",
            "fields": [{"name": "listId", "value": ""}],
        }
        list_cfg = {
            "name": "Readarr Unknown Override Opt-In",
            "allow_unknown_field_overrides": True,
            "field_overrides": {
                "listId": "1",
                "limit": 100,
            },
        }

        payload = MODULE.build_arr_import_list_payload(
            app_cfg,
            schema,
            list_cfg,
            default_quality_profile_id=1,
            default_metadata_profile_id=2,
        )
        fields = {item["name"]: item.get("value") for item in payload["fields"]}
        self.assertIn("listId", fields)
        self.assertIn("limit", fields)

    def test_legacy_keys_map_to_lidarr_readarr_schema(self):
        app_cfg = {
            "name": "Lidarr",
            "implementation": "Lidarr",
            "root_folder": "/media/music",
            "capabilities": {"monitor_scope_all_value": "entireArtist"},
        }
        schema = {
            "implementation": "LastFmTag",
            "implementationName": "Last.fm Tag",
            "configContract": "LastFmTagSettings",
            "fields": [{"name": "tagId", "value": ""}, {"name": "count", "value": 0}],
            "enableAutomaticAdd": False,
            "shouldMonitor": "none",
            "monitorNewItems": "all",
            "shouldSearch": False,
            "qualityProfileId": 0,
            "metadataProfileId": 0,
        }
        list_cfg = {
            "name": "Last.fm Top Rock Artists (Top 10)",
            "enable_auto": True,
            "monitor": "all",
            "search_on_add": True,
            "field_overrides": {"tagId": "rock", "count": 10},
        }

        payload = MODULE.build_arr_import_list_payload(
            app_cfg,
            schema,
            list_cfg,
            default_quality_profile_id=4,
            default_metadata_profile_id=7,
        )

        self.assertTrue(payload["enableAutomaticAdd"])
        self.assertEqual(payload["shouldMonitor"], "entireArtist")
        self.assertTrue(payload["shouldSearch"])

    def test_readarr_monitor_all_maps_to_entire_author(self):
        app_cfg = {
            "name": "Readarr",
            "implementation": "Readarr",
            "root_folder": "/media/books",
            "capabilities": {"monitor_scope_all_value": "entireAuthor"},
        }
        schema = {
            "implementation": "GoodreadsListImportList",
            "implementationName": "Goodreads List",
            "configContract": "GoodreadsListImportListSettings",
            "fields": [{"name": "listId", "value": ""}],
            "enableAutomaticAdd": False,
            "shouldMonitor": "none",
            "monitorNewItems": "all",
            "shouldSearch": False,
            "qualityProfileId": 0,
            "metadataProfileId": 0,
        }
        list_cfg = {
            "name": "Goodreads Best Books Ever",
            "enable_automatic_add": True,
            "should_monitor": "all",
            "monitor_new_items": "all",
            "should_search": True,
            "field_overrides": {"listId": "1"},
        }

        payload = MODULE.build_arr_import_list_payload(
            app_cfg,
            schema,
            list_cfg,
            default_quality_profile_id=1,
            default_metadata_profile_id=1,
        )

        self.assertEqual(payload["shouldMonitor"], "entireAuthor")
        self.assertTrue(payload["shouldSearch"])


if __name__ == "__main__":
    unittest.main()
