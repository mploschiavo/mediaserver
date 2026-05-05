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
        # ``direct_hosts.media_server`` MUST be present in the profile —
        # the field is the operator's surface for tuning. Either an
        # explicit FQDN ("jellyfin.media.example.com") OR the empty
        # string is acceptable: empty triggers the envoy generator's
        # auto-derive path, which yields
        # ``<media_server>.<stack_subdomain>.<base_domain>``
        # (e.g. ``jellyfin.media-stack.local``). The boolean ``true``
        # form was retired (commit e121b40e) because ``str(True)`` was
        # leaking the literal "True" into Envoy routes as a hostname.
        self.assertIn(
            "media_server", direct_hosts,
            "hybrid profile must declare media_server under direct_hosts "
            "(empty string OK; triggers auto-derive in envoy generator)",
        )
        media_server = direct_hosts.get("media_server")
        self.assertNotIsInstance(
            media_server, bool,
            "media_server boolean form retired in v1.0.320 — use "
            "an explicit FQDN string or empty string for auto-derive",
        )

    def test_compose_standard_profile_bootstrap_flags(self):
        profile_path = ROOT / "deploy" / "examples" / "bootstrap-profiles" / "media-compose-standard.yaml"
        payload = yaml.safe_load(profile_path.read_text(encoding="utf-8")) or {}
        bootstrap_cfg = payload.get("bootstrap") or {}
        self.assertTrue(bool(bootstrap_cfg.get("preconfigure_apps")))
        self.assertTrue(bool(bootstrap_cfg.get("preconfigure_api_keys")))
        self.assertTrue(bool(bootstrap_cfg.get("apply_initial_preferences")))
        # ``auto_download_content`` was true on compose-standard from
        # v1.0.141 through v1.0.319 (OTB experience). v1.0.320 aligned
        # the compose-standard default with k8s-standard
        # (``auto_download_content: false``) so a fresh standard
        # install doesn't grab content before the operator has reviewed
        # the dashboard. Operators wanting OTB downloads use
        # ``media-compose-full`` (still true there) or flip the
        # dashboard toggle. See commit e121b40e.
        self.assertFalse(
            bool(bootstrap_cfg.get("auto_download_content")),
            "compose-standard now matches k8s-standard "
            "(auto_download_content: false). Use media-compose-full "
            "for the OTB-downloading variant.",
        )
        # Indexer-sync + health-refresh toggles were missing on
        # compose-standard (v1.0.320 added them to match every other
        # profile). Both should be true.
        self.assertTrue(bool(bootstrap_cfg.get("trigger_indexer_sync")))
        self.assertTrue(bool(bootstrap_cfg.get("refresh_health_after_setup")))


if __name__ == "__main__":
    unittest.main()
