import sys
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))


class ComposeStandardProfileExampleTests(unittest.TestCase):
    def test_compose_standard_profile_uses_hybrid_envoy_with_direct_media_server(self):
        profile_path = ROOT / "deploy" / "examples" / "bootstrap-profiles" / "media-compose-standard.yaml"
        payload = yaml.safe_load(profile_path.read_text(encoding="utf-8")) or {}
        routing = payload.get("routing") or {}
        self.assertEqual(str(routing.get("provider") or "").strip().lower(), "envoy")
        # hybrid routes most apps through the single gateway and serves Jellyfin directly
        # so that native device clients (apps, TVs) can connect without path-prefix complexity.
        self.assertEqual(str(routing.get("strategy") or "").strip().lower(), "hybrid")
        self.assertEqual(str(routing.get("gateway_host") or "").strip(), "apps.media-stack.local")
        self.assertEqual(str(routing.get("gateway_port") or "").strip(), "443")
        self.assertEqual(str(routing.get("app_path_prefix") or "").strip(), "/app")
        direct_hosts = routing.get("direct_hosts") or {}
        self.assertTrue(
            bool(direct_hosts.get("media_server")),
            "hybrid profile must declare a direct media-server host for native device clients",
        )

    def test_compose_standard_profile_bootstrap_flags_enable_otb_experience(self):
        profile_path = ROOT / "deploy" / "examples" / "bootstrap-profiles" / "media-compose-standard.yaml"
        payload = yaml.safe_load(profile_path.read_text(encoding="utf-8")) or {}
        bootstrap_cfg = payload.get("bootstrap") or {}
        self.assertTrue(bool(bootstrap_cfg.get("preconfigure_apps")))
        self.assertTrue(bool(bootstrap_cfg.get("preconfigure_api_keys")))
        self.assertTrue(bool(bootstrap_cfg.get("apply_initial_preferences")))
        # Since v1.0.141 the standard profile defaults
        # ``auto_download_content`` to true so a fresh bootstrap matches
        # the OTB experience users expect; dry-run / test-only deploys
        # flip the flag.
        self.assertTrue(bool(bootstrap_cfg.get("auto_download_content")))


if __name__ == "__main__":
    unittest.main()
