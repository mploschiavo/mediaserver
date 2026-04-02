import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from bootstrap_services.apps.stack.bootstrap_config_policy import (  # noqa: E402
    apply_content_download_policy,
    apply_selected_apps_policy,
)


class BootstrapConfigPolicyTests(unittest.TestCase):
    def test_selected_apps_policy_clears_prowlarr_runtime_inputs_when_unselected(self):
        cfg = {
            "prowlarr_url": "http://prowlarr:9696",
            "prowlarr_indexers": [{"name": "Indexer A"}],
            "trigger_indexer_sync": True,
            "prowlarr_auto_add_tested_indexers": True,
            "flaresolverr": {"enabled": True},
            "arr_media_management": {"enabled": True},
            "arr_download_handling": {"enabled": True},
            "arr_quality_upgrade": {"enabled": True},
            "arr_discovery_lists": {"enabled": True},
            "disk_guardrails": {"enabled": True},
            "media_hygiene": {"enabled": True},
            "jellyfin_home_rails": {"enabled": True, "cleanup_collections_when_disabled": True},
            "maintainerr": {"enabled": True, "integrations": {"enabled": True}},
        }

        apply_selected_apps_policy(cfg, selected_apps_csv="homepage")

        self.assertEqual(cfg.get("prowlarr_url"), "")
        self.assertEqual(cfg.get("prowlarr_indexers"), [])
        self.assertFalse(bool(cfg.get("trigger_indexer_sync")))
        self.assertFalse(bool(cfg.get("prowlarr_auto_add_tested_indexers")))
        self.assertFalse(bool((cfg.get("flaresolverr") or {}).get("enabled")))
        self.assertFalse(bool((cfg.get("arr_media_management") or {}).get("enabled")))
        self.assertFalse(bool((cfg.get("arr_download_handling") or {}).get("enabled")))
        self.assertFalse(bool((cfg.get("arr_quality_upgrade") or {}).get("enabled")))
        self.assertFalse(bool((cfg.get("arr_discovery_lists") or {}).get("enabled")))
        self.assertFalse(bool((cfg.get("disk_guardrails") or {}).get("enabled")))
        self.assertFalse(bool((cfg.get("media_hygiene") or {}).get("enabled")))
        self.assertFalse(bool((cfg.get("jellyfin_home_rails") or {}).get("enabled")))
        self.assertFalse(
            bool((cfg.get("jellyfin_home_rails") or {}).get("cleanup_collections_when_disabled"))
        )
        self.assertFalse(
            bool(((cfg.get("maintainerr") or {}).get("integrations") or {}).get("enabled"))
        )

    def test_selected_apps_policy_keeps_prowlarr_runtime_inputs_when_selected(self):
        cfg = {
            "prowlarr_url": "http://prowlarr:9696",
            "prowlarr_indexers": [{"name": "Indexer A"}],
            "trigger_indexer_sync": True,
            "prowlarr_auto_add_tested_indexers": True,
            "flaresolverr": {"enabled": True},
        }

        apply_selected_apps_policy(cfg, selected_apps_csv="prowlarr,homepage,flaresolverr")

        self.assertEqual(cfg.get("prowlarr_url"), "http://prowlarr:9696")
        self.assertEqual(cfg.get("prowlarr_indexers"), [{"name": "Indexer A"}])
        self.assertTrue(bool(cfg.get("trigger_indexer_sync")))
        self.assertTrue(bool(cfg.get("prowlarr_auto_add_tested_indexers")))
        self.assertTrue(bool((cfg.get("flaresolverr") or {}).get("enabled")))

    def test_content_download_policy_disables_auto_indexers_when_downloads_disabled(self):
        cfg = {
            "prowlarr_auto_add_tested_indexers": True,
            "arr_discovery_lists": {"trigger_initial_sync": True},
        }
        apply_content_download_policy(cfg, auto_download_content=False)
        self.assertFalse(bool(cfg.get("prowlarr_auto_add_tested_indexers")))
        self.assertFalse(bool((cfg.get("arr_discovery_lists") or {}).get("trigger_initial_sync")))

    def test_content_download_policy_enables_auto_indexers_when_downloads_enabled(self):
        cfg = {
            "prowlarr_auto_add_tested_indexers": False,
            "arr_discovery_lists": {"trigger_initial_sync": False},
        }
        apply_content_download_policy(cfg, auto_download_content=True)
        self.assertTrue(bool(cfg.get("prowlarr_auto_add_tested_indexers")))
        self.assertTrue(bool((cfg.get("arr_discovery_lists") or {}).get("trigger_initial_sync")))


if __name__ == "__main__":
    unittest.main()
