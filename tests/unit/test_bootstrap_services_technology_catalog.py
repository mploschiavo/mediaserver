import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.services.technology_catalog import default_servarr_catalog  # noqa: E402


class TechnologyCatalogTests(unittest.TestCase):
    def test_canonicalize_alias(self):
        catalog = default_servarr_catalog()
        self.assertEqual(catalog.canonicalize("Sonarr"), "sonarr")
        self.assertEqual(catalog.canonicalize("readarr"), "readarr")
        self.assertEqual(catalog.canonicalize("UnknownApp"), "unknownapp")

    def test_expand_capability_defaults_maps_aliases(self):
        catalog = default_servarr_catalog()
        expanded = catalog.expand_capability_defaults(
            {
                "Sonarr": {"supports_discovery_lists": False},
                "readarr": {"supports_quality_upgrade": False},
            }
        )
        self.assertIn("sonarr", expanded)
        self.assertIn("Sonarr", expanded)
        self.assertIn("readarr", expanded)
        self.assertIn("Readarr", expanded)
        self.assertFalse(expanded["sonarr"]["supports_discovery_lists"])
        self.assertFalse(expanded["Readarr"]["supports_quality_upgrade"])


if __name__ == "__main__":
    unittest.main()
