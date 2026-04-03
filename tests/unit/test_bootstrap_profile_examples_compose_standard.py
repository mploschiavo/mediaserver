import sys
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))


class ComposeStandardProfileExampleTests(unittest.TestCase):
    def test_compose_standard_profile_uses_single_gateway_path_prefix_envoy(self):
        profile_path = ROOT / "examples" / "bootstrap-profiles" / "media-compose-standard.yaml"
        payload = yaml.safe_load(profile_path.read_text(encoding="utf-8")) or {}
        routing = payload.get("routing") or {}
        self.assertEqual(str(routing.get("provider") or "").strip().lower(), "envoy")
        self.assertEqual(str(routing.get("strategy") or "").strip().lower(), "path-prefix")
        self.assertEqual(str(routing.get("gateway_host") or "").strip(), "apps.media-dev.local")
        self.assertEqual(str(routing.get("app_path_prefix") or "").strip(), "/app")
        direct_hosts = routing.get("direct_hosts")
        self.assertFalse(bool(direct_hosts))


if __name__ == "__main__":
    unittest.main()
