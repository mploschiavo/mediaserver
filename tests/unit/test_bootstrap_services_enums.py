import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.services.enums import BootstrapMode  # noqa: E402


class BootstrapModeTests(unittest.TestCase):
    def test_choices_include_canonical_modes_only(self):
        choices = BootstrapMode.choices()
        self.assertIn("media-server-prewarm", choices)
        self.assertIn("media-server-home-rails", choices)
        self.assertNotIn("jellyfin-prewarm", choices)
        self.assertNotIn("jellyfin-home-rails", choices)

    def test_from_cli_rejects_legacy_jellyfin_aliases(self):
        with self.assertRaises(ValueError):
            BootstrapMode.from_cli("jellyfin-prewarm")
        with self.assertRaises(ValueError):
            BootstrapMode.from_cli("jellyfin-home-rails")

    def test_from_cli_accepts_generic_modes(self):
        self.assertEqual(
            BootstrapMode.from_cli("media-server-prewarm"),
            BootstrapMode.MEDIA_SERVER_PREWARM,
        )
        self.assertEqual(
            BootstrapMode.from_cli("media-server-home-rails"),
            BootstrapMode.MEDIA_SERVER_HOME_RAILS,
        )


if __name__ == "__main__":
    unittest.main()
