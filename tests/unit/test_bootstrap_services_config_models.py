import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.services.apps.download_clients.config_models import (  # noqa: E402
    DiskGuardrailsConfig,
    QbitQueueGuardrailsConfig,
)
from media_stack.services.apps.servarr.config_models import (  # noqa: E402
    AppCapabilities,
    ServarrAppConfig,
)


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

    def test_queue_guardrails_model_parses_budget_maps(self):
        model = QbitQueueGuardrailsConfig.from_dict(
            {
                "enabled": True,
                "dry_run": False,
                "default_max_queued": 30,
                "max_queued_by_category": {"tv": 50, "movies": "35"},
                "max_total_size_gib_by_category": {"tv": 300, "music": "75.5"},
                "max_weight_percent_by_category": {"tv": 40, "books": "7"},
                "over_limit_max_delete_per_category": 12,
                "over_budget_max_delete_per_category": 15,
            }
        )
        self.assertTrue(model.enabled)
        self.assertEqual(model.default_max_queued, 30)
        self.assertEqual(model.max_queued_by_category.get("movies"), 35)
        self.assertEqual(model.max_total_size_gib_by_category.get("music"), 75.5)
        self.assertEqual(model.max_weight_percent_by_category.get("books"), 7.0)
        self.assertEqual(model.over_limit_max_delete_per_category, 12)
        self.assertEqual(model.over_budget_max_delete_per_category, 15)

    def test_disk_guardrails_model_parses_thresholds(self):
        model = DiskGuardrailsConfig.from_dict(
            {
                "enabled": True,
                "required": False,
                "monitor_path": "/srv-stack",
                "max_used_percent": "70",
                "target_used_percent": 60,
                "qbit_cleanup": {"enabled": True},
            }
        )
        self.assertTrue(model.enabled)
        self.assertEqual(model.monitor_path, "/srv-stack")
        self.assertEqual(model.max_used_percent, 70.0)
        self.assertEqual(model.target_used_percent, 60.0)
        self.assertTrue(model.qbit_cleanup.get("enabled"))


if __name__ == "__main__":
    unittest.main()
