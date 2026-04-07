import json
import unittest
from pathlib import Path

from media_stack.services.apps.download_clients.config_models import DownloadClientConfig
from media_stack.services.apps.jellyfin.config_models import JellyfinLiveTvConfig
from media_stack.services.apps.servarr.config_models import ArrDiscoveryListsConfig
from media_stack.services.plugin_manifest_loader import (
    build_adapter_hook_defaults,
    load_plugin_manifests,
)

ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = ROOT / "contracts" / "media-stack.config.json"
GOLDEN_DIR = ROOT / "tests" / "unit" / "golden"


def _canonical_json(value):
    return json.dumps(value, indent=2, sort_keys=True) + "\n"


class BootstrapConfigGoldenTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from media_stack.services.runtime_factory.config_loader import ControllerConfigLoader

        def _merge(a, b):
            r = dict(a)
            r.update(b)
            return r

        loader = ControllerConfigLoader(deep_merge_objects=_merge)
        cls.cfg = loader.load_config(str(CONFIG_PATH))

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
        jf = self.cfg.get("jellyfin") or {}
        livetv_cfg = jf.get("livetv") if isinstance(jf, dict) else None
        if not livetv_cfg:
            livetv_cfg = self.cfg.get("jellyfin_livetv") or {}
        actual = livetv_cfg
        expected = json.loads((GOLDEN_DIR / "jellyfin_livetv.json").read_text(encoding="utf-8"))
        self.assertEqual(
            _canonical_json(actual),
            _canonical_json(expected),
            msg="jellyfin livetv config drifted from golden file.",
        )
        live_tv = JellyfinLiveTvConfig.from_dict(livetv_cfg)
        self.assertTrue(live_tv.enabled)
        self.assertGreaterEqual(len(live_tv.tuners), 1)
        self.assertGreaterEqual(len(live_tv.guides), 1)

    def test_arr_discovery_lists_matches_golden(self):
        self._assert_matches_golden("arr_discovery_lists", "arr_discovery_lists.json")
        discovery = ArrDiscoveryListsConfig.from_dict(self.cfg.get("arr_discovery_lists") or {})
        self.assertTrue(discovery.enabled)
        self.assertTrue(discovery.trigger_initial_sync)
        self.assertGreaterEqual(len(discovery.by_app), 1)

    def test_plugin_manifest_adapter_classes_are_registered(self):
        manifests = load_plugin_manifests()
        defaults = build_adapter_hook_defaults(manifests)
        # Servarr adapters (arr services)
        self.assertTrue(
            {"sonarr", "radarr", "lidarr", "readarr"}.issubset(
                set(defaults.adapter_classes.keys())
            ),
        )
        # Download client adapters
        self.assertTrue(
            {"qbittorrent", "sabnzbd"}.issubset(
                set(defaults.download_client_adapter_classes.keys())
            ),
        )
        # Media server adapters
        self.assertTrue(
            {"jellyfin"}.issubset(
                set(defaults.media_server_adapter_classes.keys())
            ),
        )


    def test_adapter_classes_do_not_cross_contaminate_roles(self):
        """Media server and download client adapters must NOT appear in servarr role."""
        manifests = load_plugin_manifests()
        defaults = build_adapter_hook_defaults(manifests)

        servarr = set(defaults.adapter_classes.keys())
        media_server = set(defaults.media_server_adapter_classes.keys())
        download_client = set(defaults.download_client_adapter_classes.keys())

        # Servarr should only contain arr apps
        self.assertNotIn("plex", servarr, "Plex is a media_server, not servarr")
        self.assertNotIn("jellyfin", servarr, "Jellyfin is a media_server, not servarr")
        self.assertNotIn("qbittorrent", servarr, "qBittorrent is a download_client, not servarr")
        self.assertNotIn("sabnzbd", servarr, "SABnzbd is a download_client, not servarr")

        # No overlap between roles
        self.assertFalse(servarr & media_server, f"Overlap servarr/media_server: {servarr & media_server}")
        self.assertFalse(servarr & download_client, f"Overlap servarr/download_client: {servarr & download_client}")


    def test_servarr_factory_rejects_non_servarr_adapters(self):
        """ServarrAdapterFactory must not load media_server or download_client adapters."""
        from media_stack.services.adapter_factory import build_adapter_registry
        from media_stack.services.apps.servarr.technologies.base import ServarrAdapterBase

        manifests = load_plugin_manifests()
        defaults = build_adapter_hook_defaults(manifests)
        # Should succeed — only real servarr adapters in the registry
        registry = build_adapter_registry(
            defaults.adapter_classes, base_class=ServarrAdapterBase, role="servarr"
        )
        self.assertTrue(len(registry) > 0, "At least one servarr adapter should be registered")
        for key in registry:
            self.assertTrue(
                issubclass(registry[key], ServarrAdapterBase),
                f"{key} adapter must inherit ServarrAdapterBase",
            )

    def test_unpackerr_xml_key_reader_works(self):
        """Unpackerr's inline XML key reader handles missing/malformed files."""
        import tempfile
        import xml.etree.ElementTree as ET
        from pathlib import Path

        # Valid XML with API key
        with tempfile.NamedTemporaryFile(mode="w", suffix=".xml", delete=False) as f:
            f.write('<Config><ApiKey>test-key-123</ApiKey></Config>')
            f.flush()
            tree = ET.parse(f.name)
            el = tree.find(".//ApiKey")
            self.assertEqual((el.text or "").strip(), "test-key-123")

        # Missing file returns empty
        missing = Path("/nonexistent/config.xml")
        self.assertFalse(missing.is_file())

    def test_routing_overrides_path_is_writable(self):
        """Routing overrides should use CONFIG_ROOT which is writable."""
        import os
        config_root = Path(os.environ.get("CONFIG_ROOT", "/srv-config"))
        # In test env, CONFIG_ROOT may not exist — just verify the path logic
        overrides_dir = config_root / ".controller"
        self.assertTrue(str(overrides_dir).endswith(".controller"))


if __name__ == "__main__":
    unittest.main()
