import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from bootstrap_services.config_models import AppCapabilities, ServarrAppConfig  # noqa: E402


class ServarrConfigModelTests(unittest.TestCase):
    def test_capability_defaults_and_per_app_override(self):
        apps = ServarrAppConfig.from_list(
            [
                {
                    "name": "Readarr",
                    "implementation": "Readarr",
                    "url": "http://readarr:8787",
                    "root_folder": "/media/books",
                    "capabilities": {
                        "supports_download_clients": False,
                    },
                }
            ],
            capability_defaults={
                "readarr": {
                    "supports_discovery_lists": False,
                    "supports_health_check": False,
                }
            },
        )
        self.assertEqual(len(apps), 1)
        caps = apps[0].capabilities
        self.assertFalse(caps.supports_discovery_lists)
        self.assertFalse(caps.supports_health_check)
        self.assertFalse(caps.supports_download_clients)
        self.assertTrue(caps.supports_auth)

    def test_capabilities_default_to_all_true(self):
        caps = AppCapabilities.from_dict(None)
        self.assertTrue(caps.supports_auth)
        self.assertTrue(caps.supports_media_management)
        self.assertTrue(caps.supports_root_folder)
        self.assertTrue(caps.supports_download_handling)
        self.assertTrue(caps.supports_quality_upgrade)
        self.assertTrue(caps.supports_prowlarr_application)
        self.assertTrue(caps.supports_download_clients)
        self.assertTrue(caps.supports_remote_path_mappings)
        self.assertTrue(caps.supports_discovery_lists)
        self.assertTrue(caps.supports_health_check)


if __name__ == "__main__":
    unittest.main()
