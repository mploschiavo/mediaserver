import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from bootstrap_lib.bazarr import apply_scalar_updates  # noqa: E402

SAMPLE = """---
general:
  use_sonarr: false
  use_radarr: false
sonarr:
  apikey: ''
  ip: 127.0.0.1
  port: 8989
radarr:
  apikey: ''
  ip: 127.0.0.1
  port: 7878
"""


class BazarrConfigTests(unittest.TestCase):
    def test_apply_updates_existing_values(self):
        rendered, changed = apply_scalar_updates(
            SAMPLE,
            {
                "general": {"use_sonarr": True, "use_radarr": True},
                "sonarr": {"apikey": "abc123", "ip": "sonarr"},
                "radarr": {"apikey": "def456", "ip": "radarr"},
            },
        )
        self.assertTrue(changed)
        self.assertIn("  use_sonarr: true", rendered)
        self.assertIn("  use_radarr: true", rendered)
        self.assertIn("  apikey: abc123", rendered)
        self.assertIn("  apikey: def456", rendered)
        self.assertIn("  ip: sonarr", rendered)
        self.assertIn("  ip: radarr", rendered)

    def test_apply_inserts_missing_keys(self):
        minimal = "general:\n  use_sonarr: false\n"
        rendered, changed = apply_scalar_updates(
            minimal,
            {
                "general": {"use_radarr": True},
                "sonarr": {"apikey": "xyz"},
            },
        )
        self.assertTrue(changed)
        self.assertIn("  use_radarr: true", rendered)
        self.assertIn("sonarr:", rendered)
        self.assertIn("  apikey: xyz", rendered)

    def test_apply_updates_list_values(self):
        current = """general:
  enabled_providers:
    - oldprovider
"""
        rendered, changed = apply_scalar_updates(
            current,
            {
                "general": {
                    "enabled_providers": ["opensubtitlescom", "podnapisi"],
                }
            },
        )
        self.assertTrue(changed)
        self.assertIn("  enabled_providers:", rendered)
        self.assertIn("    - opensubtitlescom", rendered)
        self.assertIn("    - podnapisi", rendered)
        self.assertNotIn("oldprovider", rendered)

    def test_apply_replaces_list_with_empty_list(self):
        current = """general:
  enabled_providers:
    - opensubtitlescom
"""
        rendered, changed = apply_scalar_updates(
            current,
            {
                "general": {
                    "enabled_providers": [],
                }
            },
        )
        self.assertTrue(changed)
        self.assertIn("  enabled_providers: []", rendered)


if __name__ == "__main__":
    unittest.main()
