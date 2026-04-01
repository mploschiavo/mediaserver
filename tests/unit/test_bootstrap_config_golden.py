import json
import unittest
from pathlib import Path

from scripts.bootstrap_services.config_models import (
    ArrDiscoveryListsConfig,
    DownloadClientConfig,
    JellyfinLiveTvConfig,
)
from scripts.bootstrap_services.plugin_manifest_loader import (
    build_adapter_hook_defaults,
    load_plugin_manifests,
)

ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = ROOT / "bootstrap" / "media-stack.bootstrap.json"
GOLDEN_DIR = ROOT / "tests" / "unit" / "golden"


def _canonical_json(value):
    return json.dumps(value, indent=2, sort_keys=True) + "\n"


class BootstrapConfigGoldenTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))

    def _assert_matches_golden(self, section_name, golden_filename):
        actual = self.cfg.get(section_name) or {}
        expected = json.loads((GOLDEN_DIR / golden_filename).read_text(encoding="utf-8"))
        self.assertEqual(
            _canonical_json(actual),
            _canonical_json(expected),
            msg=(
                f"Config section '{section_name}' drifted from golden file "
                f"{golden_filename}. If intentional, regenerate golden fixtures."
            ),
        )

    def test_download_clients_matches_golden(self):
        self._assert_matches_golden("download_clients", "download_clients.json")
        qbit = DownloadClientConfig.from_dict(
            (self.cfg.get("download_clients") or {}).get("qbittorrent")
        )
        self.assertTrue(bool(qbit.url))
        self.assertGreaterEqual(int(qbit.priority), 1)

    def test_jellyfin_livetv_matches_golden(self):
        self._assert_matches_golden("jellyfin_livetv", "jellyfin_livetv.json")
        live_tv = JellyfinLiveTvConfig.from_dict(self.cfg.get("jellyfin_livetv") or {})
        self.assertTrue(live_tv.enabled)
        self.assertGreaterEqual(len(live_tv.tuners), 1)
        self.assertGreaterEqual(len(live_tv.guides), 1)

    def test_arr_discovery_lists_matches_golden(self):
        self._assert_matches_golden("arr_discovery_lists", "arr_discovery_lists.json")
        discovery = ArrDiscoveryListsConfig.from_dict(self.cfg.get("arr_discovery_lists") or {})
        self.assertTrue(discovery.enabled)
        self.assertTrue(discovery.trigger_initial_sync)
        self.assertGreaterEqual(len(discovery.by_app), 1)

    def test_adapter_hooks_matches_golden(self):
        self._assert_matches_golden("adapter_hooks", "adapter_hooks.json")
        manifests = load_plugin_manifests()
        defaults = build_adapter_hook_defaults(manifests)
        self.assertEqual(
            {"sonarr", "radarr", "lidarr", "readarr"},
            set(defaults.adapter_classes.keys()),
        )
        self.assertEqual(
            {"qbittorrent", "sabnzbd", "transmission"},
            set(defaults.download_client_adapter_classes.keys()),
        )


if __name__ == "__main__":
    unittest.main()
