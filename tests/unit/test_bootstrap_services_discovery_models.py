import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from bootstrap_services.apps.servarr.config_models import ArrDiscoveryListEntry  # noqa: E402
from bootstrap_services.apps.servarr.config_models_discovery import (  # noqa: E402
    LastFmTagOptions,
    TmdbPopularImportOptions,
)


class DiscoveryConfigModelTests(unittest.TestCase):
    def test_tmdb_contract_and_provider_options_parse(self):
        entry = ArrDiscoveryListEntry.from_dict(
            {
                "name": "TMDb Trending Movies",
                "implementation": "TMDbPopularImport",
                "field_overrides": {"tMDbListType": 1},
            }
        )
        self.assertEqual(entry.contract.provider, "tmdb")
        self.assertIsInstance(entry.provider_options, TmdbPopularImportOptions)
        self.assertEqual(entry.contract_missing_override_fields, ())

    def test_lastfm_contract_and_provider_options_parse(self):
        entry = ArrDiscoveryListEntry.from_dict(
            {
                "name": "Last.fm Top Rock Artists (Top 10)",
                "implementation": "LastFmTag",
                "field_overrides": {"tagId": "rock", "count": 10},
            }
        )
        self.assertEqual(entry.contract.provider, "lastfm")
        self.assertIsInstance(entry.provider_options, LastFmTagOptions)
        self.assertEqual(entry.contract_missing_override_fields, ())

    def test_contract_missing_required_fields_are_captured(self):
        entry = ArrDiscoveryListEntry.from_dict(
            {
                "name": "Broken Last.fm list",
                "implementation": "LastFmTag",
                "field_overrides": {"count": 10},
            }
        )
        self.assertEqual(entry.contract.provider, "lastfm")
        self.assertIn("tagId", entry.contract_missing_override_fields)


if __name__ == "__main__":
    unittest.main()
