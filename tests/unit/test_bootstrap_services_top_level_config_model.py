import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from bootstrap_services.top_level_config_model import TopLevelBootstrapConfig  # noqa: E402


class TopLevelBootstrapConfigModelTests(unittest.TestCase):
    def test_from_dict_accepts_known_sections_and_unknown_passthrough(self):
        model = TopLevelBootstrapConfig.from_dict(
            {
                "prowlarr_url": "http://prowlarr:9696",
                "arr_apps": [],
                "download_clients": {},
                "prowlarr_indexer_reputation": {"enabled": True},
                "arr_indexer_sync": {"prune_stale_indexers": True},
                "unknown_key": {"value": 1},
            }
        )
        self.assertEqual(model.prowlarr_url, "http://prowlarr:9696")
        self.assertTrue(model.prowlarr_indexer_reputation.get("enabled"))
        self.assertTrue(model.arr_indexer_sync.get("prune_stale_indexers"))
        self.assertIn("unknown_key", model.unknown)
        self.assertEqual(model.to_dict()["unknown_key"], {"value": 1})

    def test_from_dict_rejects_wrong_top_level_types(self):
        with self.assertRaises(ValueError):
            TopLevelBootstrapConfig.from_dict(
                {
                    "prowlarr_url": 123,
                    "arr_apps": [],
                }
            )


if __name__ == "__main__":
    unittest.main()
