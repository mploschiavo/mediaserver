import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from bootstrap_lib.homepage import DEFAULT_HOSTS, render_services_yaml  # noqa: E402


class HomepageRenderTests(unittest.TestCase):
    def test_render_uses_preferred_order_and_dedupes(self):
        yaml_text = render_services_yaml(
            ["sonarr.local", "jellyfin.local", "sonarr.local", "qbittorrent.local"]
        )
        jellyfin_pos = yaml_text.find("http://jellyfin.local")
        sonarr_pos = yaml_text.find("http://sonarr.local")
        qbit_pos = yaml_text.find("http://qbittorrent.local")
        self.assertTrue(jellyfin_pos != -1 and sonarr_pos != -1 and qbit_pos != -1)
        self.assertLess(jellyfin_pos, sonarr_pos)
        self.assertLess(sonarr_pos, qbit_pos)
        self.assertEqual(yaml_text.count("http://sonarr.local"), 1)

    def test_render_falls_back_to_default_hosts(self):
        yaml_text = render_services_yaml([])
        for host in DEFAULT_HOSTS:
            self.assertIn(f"http://{host}", yaml_text)

    def test_render_adds_device_onboarding_cards_when_enabled(self):
        yaml_text = render_services_yaml(
            ["homepage.local", "jellyfin.local", "jellyseerr.local"],
            onboarding={"enabled": True, "jellyfin_url": "http://jellyfin.local"},
        )
        self.assertIn("Device Onboarding", yaml_text)
        self.assertIn("Jellyfin Setup QR", yaml_text)
        self.assertIn("short link: jellyfin.local", yaml_text)
        self.assertIn("Samsung TV Quick Start", yaml_text)
        self.assertIn("Vizio Quick Start", yaml_text)
        self.assertIn("TCL Quick Start", yaml_text)


if __name__ == "__main__":
    unittest.main()
