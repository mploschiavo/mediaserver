import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from bootstrap_services.apps.stack.bootstrap_config_policy import (  # noqa: E402
    apply_content_download_policy,
    apply_edge_url_policy,
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

    def test_edge_url_policy_rewrites_local_hybrid_homepage_and_onboarding_hosts(self):
        cfg = {
            "jellyseerr": {"jellyfin": {}},
            "homepage": {
                "hosts": ["homepage.local", "jellyfin.local", "jellyseerr.local"],
                "device_onboarding": {},
            },
        }

        apply_edge_url_policy(
            cfg,
            internet_exposed=False,
            route_strategy="hybrid",
            ingress_domain="media-dev.local",
            app_gateway_host="apps.media-dev.local",
            app_path_prefix="/app",
            media_server_direct_host="jellyfin.media-dev.local",
        )

        homepage_hosts = (cfg.get("homepage") or {}).get("hosts") or []
        self.assertEqual(
            homepage_hosts,
            [
                "apps.media-dev.local/app/homepage",
                "jellyfin.media-dev.local",
                "apps.media-dev.local/app/jellyseerr",
            ],
        )
        onboarding = (cfg.get("homepage") or {}).get("device_onboarding") or {}
        self.assertEqual(
            onboarding.get("jellyfin_url"),
            "http://jellyfin.media-dev.local",
        )
        self.assertEqual(
            onboarding.get("jellyseerr_url"),
            "http://apps.media-dev.local/app/jellyseerr",
        )
        jellyfin_cfg = ((cfg.get("jellyseerr") or {}).get("jellyfin")) or {}
        self.assertEqual(
            jellyfin_cfg.get("external_url"),
            "http://jellyfin.media-dev.local",
        )

    def test_edge_url_policy_uses_https_when_internet_exposed(self):
        cfg = {
            "jellyseerr": {"jellyfin": {}},
            "homepage": {
                "hosts": ["homepage.local", "jellyfin.local"],
                "device_onboarding": {},
            },
        }

        apply_edge_url_policy(
            cfg,
            internet_exposed=True,
            route_strategy="hybrid",
            ingress_domain="media-dev.example.com",
            app_gateway_host="apps.media-dev.example.com",
            app_path_prefix="/app",
            media_server_direct_host="jellyfin.media-dev.example.com",
        )

        onboarding = (cfg.get("homepage") or {}).get("device_onboarding") or {}
        self.assertEqual(
            onboarding.get("jellyfin_url"),
            "https://jellyfin.media-dev.example.com",
        )
        self.assertEqual(
            onboarding.get("jellyseerr_url"),
            "https://apps.media-dev.example.com/app/jellyseerr",
        )


if __name__ == "__main__":
    unittest.main()
