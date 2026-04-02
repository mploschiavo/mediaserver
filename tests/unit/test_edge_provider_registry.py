import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from core.edge.provider_registry import (  # noqa: E402
    compose_label_specs_by_provider,
    router_service_names_by_provider,
)


class EdgeProviderRegistryTests(unittest.TestCase):
    def test_compose_label_specs_include_builtin_traefik_none_and_envoy_stub(self):
        specs = compose_label_specs_by_provider()
        self.assertIn("traefik", specs)
        self.assertIn("none", specs)
        self.assertIn("envoy", specs)
        self.assertEqual(specs["traefik"].get("enable_label_key"), "traefik.enable")
        self.assertEqual(specs["none"], {})
        self.assertEqual(specs["envoy"], {})

    def test_router_service_names_include_builtin_defaults(self):
        names = router_service_names_by_provider()
        self.assertEqual(names.get("traefik"), ("traefik",))
        self.assertEqual(names.get("none"), ())
        self.assertEqual(names.get("envoy"), ("envoy",))


if __name__ == "__main__":
    unittest.main()
