import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.services.apps.stack.controller_config_policy import (  # noqa: E402
    apply_content_download_policy,
    apply_edge_url_policy,
    apply_selected_apps_policy,
)

# Stub the policy catalog to avoid filesystem/registry dependency
_STUB_POLICY = {
    "selected_apps_policy": {
        "app_toggle_sections": {
            "jellyfin": "jellyfin",
            "jellyseerr": "jellyseerr",
            "maintainerr": "maintainerr",
            "flaresolverr": "flaresolverr",
            "tautulli": "tautulli",
            "bazarr": "bazarr",
        },
        "arr_app_keys": ["sonarr", "radarr", "lidarr", "readarr", "prowlarr"],
        "selected_app_expansions": {
            "unpackerr": ["sonarr", "radarr"],
        },
        "arr_disable_sections_when_unselected": [
            "arr_media_management",
            "arr_download_handling",
            "arr_quality_upgrade",
            "arr_discovery_lists",
            "disk_guardrails",
            "media_hygiene",
        ],
        "arr_discovery_reserved_keys": [
            "enabled",
            "required",
            "trigger_initial_sync",
            "prune_unmanaged",
        ],
        "homepage_host_reserved_tokens": [],
        "jellyfin_disable_sections_when_unselected": ["jellyfin_home_rails"],
        "maintainerr_integrations_section": "maintainerr.integrations",
        "jellyfin_home_rails_cleanup_path": "jellyfin_home_rails.cleanup_collections_when_disabled",
    }
}


def _patch_policy():
    return patch(
        "media_stack.services.apps.stack.controller_config_policy._load_policy_catalog",
        return_value=_STUB_POLICY,
    )


class BootstrapConfigPolicyTests(unittest.TestCase):
    @_patch_policy()
    def test_selected_apps_policy_clears_prowlarr_runtime_inputs_when_unselected(self, _mock):
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

    @_patch_policy()
    def test_selected_apps_policy_keeps_prowlarr_runtime_inputs_when_selected(self, _mock):
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

    @_patch_policy()
    def test_selected_apps_policy_expands_unpackerr_to_seed_primary_arr_apps_only(self, _mock):
        cfg = {
            "arr_apps": [
                {"implementation": "sonarr", "name": "Sonarr"},
                {"implementation": "radarr", "name": "Radarr"},
                {"implementation": "lidarr", "name": "Lidarr"},
                {"implementation": "readarr", "name": "Readarr"},
            ],
            "arr_media_management": {"enabled": True},
            "app_auth": {
                "include": ["sonarr", "radarr", "lidarr", "readarr", "prowlarr"],
            },
        }

        apply_selected_apps_policy(cfg, selected_apps_csv="unpackerr,prowlarr")

        implementations = {
            str((item or {}).get("implementation") or "").strip().lower()
            for item in (cfg.get("arr_apps") or [])
            if isinstance(item, dict)
        }
        self.assertEqual(implementations, {"sonarr", "radarr"})
        self.assertTrue(bool((cfg.get("arr_media_management") or {}).get("enabled")))
        include = ((cfg.get("app_auth") or {}).get("include")) or []
        self.assertEqual(
            {str(value).strip().lower() for value in include},
            {"sonarr", "radarr", "prowlarr"},
        )

    @_patch_policy()
    def test_selected_apps_policy_prunes_homepage_hosts_to_selected_apps(self, _mock):
        cfg = {
            "homepage": {
                "enabled": True,
                "hosts": [
                    "homepage.local",
                    "sonarr.local",
                    "radarr.local",
                    "tautulli.local",
                    "flaresolverr.local",
                ],
            }
        }

        apply_selected_apps_policy(cfg, selected_apps_csv="homepage,sonarr,radarr")

        self.assertEqual(
            (cfg.get("homepage") or {}).get("hosts"),
            ["homepage.local", "sonarr.local", "radarr.local"],
        )

    @_patch_policy()
    def test_content_download_policy_disables_auto_indexers_when_downloads_disabled(self, _mock):
        cfg = {
            "prowlarr_auto_add_tested_indexers": True,
            "arr_discovery_lists": {"trigger_initial_sync": True},
        }
        apply_content_download_policy(cfg, auto_download_content=False)
        self.assertFalse(bool(cfg.get("prowlarr_auto_add_tested_indexers")))
        self.assertFalse(bool((cfg.get("arr_discovery_lists") or {}).get("trigger_initial_sync")))

    @_patch_policy()
    def test_content_download_policy_enables_auto_indexers_when_downloads_enabled(self, _mock):
        cfg = {
            "prowlarr_auto_add_tested_indexers": False,
            "arr_discovery_lists": {"trigger_initial_sync": False},
        }
        apply_content_download_policy(cfg, auto_download_content=True)
        self.assertTrue(bool(cfg.get("prowlarr_auto_add_tested_indexers")))
        self.assertTrue(bool((cfg.get("arr_discovery_lists") or {}).get("trigger_initial_sync")))

    @_patch_policy()
    def test_edge_url_policy_rewrites_local_hybrid_homepage_and_onboarding_hosts(self, _mock):
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
                "homepage.local",
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

    @_patch_policy()
    def test_edge_url_policy_uses_https_when_internet_exposed(self, _mock):
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

    @_patch_policy()
    def test_edge_url_policy_rewrites_compose_hosts_with_explicit_gateway_port(self, _mock):
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
            app_gateway_port="18080",
            app_path_prefix="/app",
            media_server_direct_host="jellyfin.media-dev.local",
        )

        homepage_hosts = (cfg.get("homepage") or {}).get("hosts") or []
        self.assertEqual(
            homepage_hosts,
            [
                "homepage.local:18080",
                "jellyfin.media-dev.local:18080",
                "apps.media-dev.local:18080/app/jellyseerr",
            ],
        )
        onboarding = (cfg.get("homepage") or {}).get("device_onboarding") or {}
        self.assertEqual(
            onboarding.get("jellyfin_url"),
            "http://jellyfin.media-dev.local:18080",
        )
        self.assertEqual(
            onboarding.get("jellyseerr_url"),
            "http://apps.media-dev.local:18080/app/jellyseerr",
        )
        jellyfin_cfg = ((cfg.get("jellyseerr") or {}).get("jellyfin")) or {}
        self.assertEqual(
            jellyfin_cfg.get("external_url"),
            "http://jellyfin.media-dev.local:18080",
        )

    @_patch_policy()
    def test_edge_url_policy_path_prefix_uses_single_gateway_for_browser_apps(self, _mock):
        cfg = {
            "homepage": {
                "hosts": ["homepage.local", "jellyfin.local", "bazarr.local", "jellyseerr.local"],
                "device_onboarding": {},
            },
            "jellyseerr": {"jellyfin": {}},
            "app_auth": {
                "include": ["Sonarr", "Radarr", "Lidarr", "Readarr", "Prowlarr"],
            },
        }

        apply_edge_url_policy(
            cfg,
            internet_exposed=False,
            route_strategy="path-prefix",
            ingress_domain="media-dev.local",
            app_gateway_host="apps.media-dev.local",
            app_gateway_port="18080",
            app_path_prefix="/app",
            media_server_direct_host="",
        )

        homepage_hosts = (cfg.get("homepage") or {}).get("hosts") or []
        self.assertIn("apps.media-dev.local:18080/app/homepage", homepage_hosts)
        self.assertIn("apps.media-dev.local:18080/app/jellyfin", homepage_hosts)
        self.assertIn("apps.media-dev.local:18080/app/bazarr", homepage_hosts)
        self.assertIn("apps.media-dev.local:18080/app/jellyseerr", homepage_hosts)
        self.assertNotIn("homepage.local:18080", homepage_hosts)
        self.assertNotIn("media.media-dev.local:18080", homepage_hosts)
        path_map = ((cfg.get("app_auth") or {}).get("path_prefix_url_base_by_app")) or {}
        self.assertEqual(
            path_map,
            {
                "sonarr": "/app/sonarr",
                "radarr": "/app/radarr",
                "lidarr": "/app/lidarr",
                "readarr": "/app/readarr",
                "prowlarr": "/app/prowlarr",
            },
        )

    @_patch_policy()
    def test_edge_url_policy_adds_jellyseerr_path_base_when_enabled(self, _mock):
        cfg = {
            "app_auth": {
                "include": ["Sonarr"],
            },
            "jellyseerr": {
                "enabled": True,
            },
        }

        apply_edge_url_policy(
            cfg,
            internet_exposed=False,
            route_strategy="path-prefix",
            ingress_domain="media-dev.local",
            app_gateway_host="apps.media-dev.local",
            app_gateway_port="18080",
            app_path_prefix="/app",
            media_server_direct_host="",
        )

        path_map = ((cfg.get("app_auth") or {}).get("path_prefix_url_base_by_app")) or {}
        self.assertEqual(path_map.get("sonarr"), "/app/sonarr")
        self.assertEqual(path_map.get("jellyseerr"), "/app/jellyseerr")

    @_patch_policy()
    def test_edge_url_policy_adds_maintainerr_path_base_when_enabled(self, _mock):
        cfg = {
            "app_auth": {
                "include": ["Sonarr"],
            },
            "maintainerr": {
                "enabled": True,
            },
        }

        apply_edge_url_policy(
            cfg,
            internet_exposed=False,
            route_strategy="path-prefix",
            ingress_domain="media-dev.local",
            app_gateway_host="apps.media-dev.local",
            app_gateway_port="18080",
            app_path_prefix="/app",
            media_server_direct_host="",
        )

        path_map = ((cfg.get("app_auth") or {}).get("path_prefix_url_base_by_app")) or {}
        self.assertEqual(path_map.get("sonarr"), "/app/sonarr")
        self.assertEqual(path_map.get("maintainerr"), "/app/maintainerr")


if __name__ == "__main__":
    unittest.main()
