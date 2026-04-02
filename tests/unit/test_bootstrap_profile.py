import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.core.bootstrap_profile import (
    BootstrapProfileConfig,
    normalize_selected_apps_csv,
)


class BootstrapProfileTests(unittest.TestCase):
    def test_from_dict_parses_canonical_profile(self):
        profile = BootstrapProfileConfig.from_dict(
            {
                "schema_version": 1,
                "kind": "media_stack_profile",
                "metadata": {
                    "name": "media-dev",
                    "platform": "compose",
                    "purpose": "dev",
                },
                "resources": {
                    "disk_space_gb": "1TB",
                    "network_cidr": "10.10.0.0/24",
                },
                "install_profile": "standard",
                "apps": {
                    "sabnzbd": False,
                },
                "bootstrap": {
                    "preconfigure_apps": True,
                    "preconfigure_api_keys": True,
                    "apply_initial_preferences": True,
                    "auto_download_content": False,
                },
                "routing": {
                    "internet_exposed": True,
                    "strategy": "hybrid",
                    "base_domain": "example.com",
                    "stack_subdomain": "media-dev",
                    "gateway_host": "apps.media-dev.example.com",
                    "app_path_prefix": "/app",
                    "direct_hosts": {
                        "media_server": "jellyfin.media-dev.example.com",
                    },
                },
                "auth": {
                    "enabled": True,
                    "provider": "authelia",
                },
            }
        )
        self.assertEqual(profile.deployment_target, "compose")
        self.assertEqual(profile.disk_allocation_gb, 1000)
        self.assertEqual(profile.network_cidr, "10.10.0.0/24")
        self.assertEqual(profile.install_profile, "standard")
        self.assertFalse(profile.install_apps["sabnzbd"])
        self.assertTrue(profile.install_apps["radarr"])
        self.assertTrue(profile.preconfigure_api_keys)
        self.assertTrue(profile.apply_initial_preferences)
        self.assertFalse(profile.auto_download_content)
        self.assertEqual(profile.exposure.auth_provider, "authelia")
        self.assertEqual(profile.exposure.auth_middleware, "authelia@docker")
        self.assertEqual(profile.exposure.gateway_host, "apps.media-dev.example.com")
        self.assertEqual(
            profile.exposure.media_server_direct_host, "jellyfin.media-dev.example.com"
        )

    def test_from_dict_rejects_missing_required_sections(self):
        with self.assertRaisesRegex(ValueError, "metadata must be an object"):
            BootstrapProfileConfig.from_dict({})

    def test_from_dict_rejects_small_disk_allocation(self):
        with self.assertRaisesRegex(ValueError, "at least 200GB"):
            BootstrapProfileConfig.from_dict(
                {
                    "schema_version": 1,
                    "kind": "media_stack_profile",
                    "metadata": {
                        "name": "media-dev",
                        "platform": "k8s",
                        "purpose": "dev",
                    },
                    "resources": {
                        "disk_space_gb": 100,
                        "network_cidr": "192.168.1.0/24",
                    },
                    "install_profile": "minimal",
                }
            )

    def test_from_yaml_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            profile_file = Path(tmp) / "bootstrap.yaml"
            profile_file.write_text(
                "\n".join(
                    [
                        "schema_version: 1",
                        "kind: media_stack_profile",
                        "metadata:",
                        "  name: media-test",
                        "  platform: k8s",
                        "  purpose: test",
                        "resources:",
                        "  disk_space_gb: 500GB",
                        "  network_cidr: 192.168.50.0/24",
                        "install_profile: minimal",
                        "routing:",
                        "  strategy: path-prefix",
                        "  base_domain: local",
                    ]
                ),
                encoding="utf-8",
            )
            profile = BootstrapProfileConfig.from_yaml_file(profile_file)
        self.assertEqual(profile.deployment_target, "k8s")
        self.assertEqual(profile.purpose, "test")
        self.assertEqual(profile.exposure.route_strategy, "path-prefix")
        self.assertEqual(profile.exposure.gateway_host, "apps.media-test.local")

    def test_normalize_selected_apps_csv(self):
        self.assertEqual(
            normalize_selected_apps_csv("  jellyfin, mainainerr ,sonarr "),
            "jellyfin,maintainerr,sonarr",
        )
        with self.assertRaisesRegex(ValueError, "Unsupported app"):
            normalize_selected_apps_csv("jellyfin,unknownapp")

    def test_from_dict_uses_code_live_tv_defaults_when_not_specified(self):
        profile = BootstrapProfileConfig.from_dict(
            {
                "schema_version": 1,
                "kind": "media_stack_profile",
                "metadata": {
                    "name": "media-dev",
                    "platform": "k8s",
                    "purpose": "dev",
                },
                "resources": {
                    "disk_space_gb": 500,
                    "network_cidr": "192.168.1.0/24",
                },
                "install_profile": "minimal",
            }
        )
        self.assertFalse(profile.auto_download_content)
        self.assertEqual(
            profile.live_tv_tuner_urls,
            ("https://iptv-org.github.io/iptv/countries/us.m3u",),
        )
        self.assertEqual(
            profile.live_tv_guide_urls,
            ("https://iptv-epg.org/files/epg-us.xml",),
        )
        self.assertEqual(
            profile.live_tv_default_program_icon_url,
            "https://raw.githubusercontent.com/iptv-org/logo/master/tv.png",
        )

    def test_full_install_profile_defaults_auto_download_to_true(self):
        profile = BootstrapProfileConfig.from_dict(
            {
                "schema_version": 1,
                "kind": "media_stack_profile",
                "metadata": {
                    "name": "media-prod",
                    "platform": "k8s",
                    "purpose": "prod",
                },
                "resources": {
                    "disk_space_gb": 1000,
                    "network_cidr": "10.30.0.0/24",
                },
                "install_profile": "full",
            }
        )
        self.assertTrue(profile.auto_download_content)

    def test_catalog_file_allows_extending_apps_without_code_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            catalog_file = Path(tmp) / "catalog.yaml"
            catalog_file.write_text(
                "\n".join(
                    [
                        "schema_version: 1",
                        "kind: bootstrap_profile_catalog",
                        "boolean_tokens:",
                        "  true: ['1','true']",
                        "  false: ['0','false']",
                        "deployment_aliases:",
                        "  k8s: k8s",
                        "  compose: compose",
                        "purpose_values: [dev, test, prod]",
                        "route_strategy_aliases:",
                        "  subdomain: subdomain",
                        "  path-prefix: path-prefix",
                        "  hybrid: hybrid",
                        "  local: subdomain",
                        "auth_providers: [none, authelia, authentik]",
                        "apps:",
                        "  keys: [jellyfin, customapp]",
                        "  aliases: {}",
                        "install_profiles:",
                        "  minimal: { enabled_apps: [jellyfin] }",
                        "  standard: { enabled_apps: [jellyfin] }",
                        "  full: { enabled_apps: '*' }",
                        "live_tv_defaults:",
                        "  tuner_urls: ['https://example.com/tv.m3u']",
                        "  guide_urls: ['https://example.com/guide.xml']",
                        "  default_program_icon_url: https://example.com/icon.png",
                    ]
                ),
                encoding="utf-8",
            )
            with patch.dict(
                os.environ,
                {"BOOTSTRAP_PROFILE_CATALOG_FILE": str(catalog_file)},
                clear=False,
            ):
                self.assertEqual(
                    normalize_selected_apps_csv("customapp,jellyfin"), "customapp,jellyfin"
                )
                profile = BootstrapProfileConfig.from_dict(
                    {
                        "schema_version": 1,
                        "kind": "media_stack_profile",
                        "metadata": {
                            "name": "media-ext",
                            "platform": "compose",
                            "purpose": "dev",
                        },
                        "resources": {
                            "disk_space_gb": 500,
                            "network_cidr": "10.90.0.0/24",
                        },
                        "install_profile": "full",
                        "apps": {
                            "customapp": False,
                        },
                    }
                )
                self.assertIn("customapp", profile.install_apps)
                self.assertFalse(profile.install_apps["customapp"])


if __name__ == "__main__":
    unittest.main()
