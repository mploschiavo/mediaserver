import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from bootstrap_services.enums import BootstrapMode  # noqa: E402


class BootstrapModeTests(unittest.TestCase):
    def test_choices_include_generic_and_legacy_modes(self):
        choices = BootstrapMode.choices()
        self.assertIn("media-server-prewarm", choices)
        self.assertIn("media-server-home-rails", choices)
        self.assertIn("jellyfin-prewarm", choices)
        self.assertIn("jellyfin-home-rails", choices)

    def test_from_cli_maps_legacy_jellyfin_aliases(self):
        self.assertEqual(
            BootstrapMode.from_cli("jellyfin-prewarm"),
            BootstrapMode.MEDIA_SERVER_PREWARM,
        )
        self.assertEqual(
            BootstrapMode.from_cli("jellyfin-home-rails"),
            BootstrapMode.MEDIA_SERVER_HOME_RAILS,
        )

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
