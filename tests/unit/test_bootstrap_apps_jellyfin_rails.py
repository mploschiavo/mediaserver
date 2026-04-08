import sys
import unittest
from pathlib import Path
from unittest import mock
from urllib import parse

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

import media_stack.services.apps.jellyfin.runtime_ops as MODULE
import media_stack.services.apps.jellyfin.runtime_ops as MEDIA_OPS


class JellyfinHomeRailsTests(unittest.TestCase):
    def test_default_rails_loaded(self):
        # The JSON defaults file was removed; rails are now in the
        # contracts/services/jellyfin.yaml contract.  Verify the YAML
        # contract has the expected rails.
        import yaml
        yaml_path = ROOT / "contracts" / "services" / "jellyfin.yaml"
        data = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
        rails = (data.get("defaults") or {}).get("home_rails", {}).get("rails", [])
        names = {str(item.get("name")) for item in rails if isinstance(item, dict)}
        self.assertIn("Trending", names)
        self.assertIn("Top Rated", names)

    def test_run_jellyfin_rail_query_infers_allowed_types_from_include_item_types(self):
        rail_cfg = {
            "name": "Trending",
            "path": "/Items",
            "query": {"includeItemTypes": "Movie", "recursive": "true"},
            "limit": 10,
        }

        fake_payload = {
            "Items": [
                {"Id": "movie-1", "Type": "Movie"},
                {"Id": "boxset-1", "Type": "BoxSet"},
            ]
        }

        with mock.patch.object(MEDIA_OPS, "jellyfin_request", return_value=(200, fake_payload, "")):
            ids = MODULE.run_jellyfin_rail_query(
                "http://jellyfin:8096", "api-key", "user-id", rail_cfg, 40
            )

        self.assertEqual(ids, ["movie-1"])

    def test_run_jellyfin_rail_query_infers_allowed_types_from_type_query(self):
        rail_cfg = {
            "name": "Because You Watched",
            "path": "/Items/Suggestions",
            "query": {"mediaType": "Video", "type": "Movie"},
            "limit": 10,
        }

        fake_payload = {
            "Items": [
                {"Id": "movie-1", "Type": "Movie"},
                {"Id": "episode-1", "Type": "Episode"},
            ]
        }

        with mock.patch.object(MEDIA_OPS, "jellyfin_request", return_value=(200, fake_payload, "")):
            ids = MODULE.run_jellyfin_rail_query(
                "http://jellyfin:8096", "api-key", "user-id", rail_cfg, 40
            )

        self.assertEqual(ids, ["movie-1"])

    def test_collection_item_ids_uses_non_recursive_membership_listing(self):
        captured = {"path": ""}

        def fake_request(_url, path, _key, method="GET", payload=None, timeout=20):
            del method, payload, timeout
            captured["path"] = path
            return 200, {"Items": []}, ""

        with mock.patch.object(MEDIA_OPS, "jellyfin_request", side_effect=fake_request):
            MODULE.collection_item_ids("http://jellyfin:8096", "api-key", "user-id", "col-1")

        query = parse.parse_qs(parse.urlsplit(captured["path"]).query)
        self.assertEqual(query.get("recursive"), ["false"])

    def test_ensure_collection_membership_excludes_self_reference(self):
        captured = {"to_add": []}

        def fake_update(_url, _key, _collection_id, to_add, to_remove):
            del to_remove
            captured["to_add"] = list(to_add)
            return len(to_add), 0

        with (
            mock.patch.object(
                MEDIA_OPS.JellyfinHomeRailsService, "find_collection_by_name", return_value="ABCD"
            ),
            mock.patch.object(
                MEDIA_OPS.JellyfinHomeRailsService, "collection_item_ids", return_value=[]
            ),
            mock.patch.object(
                MEDIA_OPS.JellyfinHomeRailsService,
                "update_collection_items",
                side_effect=fake_update,
            ),
        ):
            MODULE.ensure_jellyfin_collection_membership(
                "http://jellyfin:8096",
                "api-key",
                "user-id",
                "Trending",
                ["abcd", "movie-1"],
            )

        self.assertEqual(captured["to_add"], ["movie-1"])


if __name__ == "__main__":
    unittest.main()
