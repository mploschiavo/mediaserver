import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.core.auth.provider_registry import (  # noqa: E402
    compose_service_names_by_provider,
    load_builtin_auth_provider_specs,
    merge_auth_provider_defaults,
)


class AuthProviderRegistryTests(unittest.TestCase):
    def test_builtin_specs_include_authelia_and_authentik(self):
        specs = {spec.key: spec for spec in load_builtin_auth_provider_specs()}
        self.assertIn("none", specs)
        self.assertIn("authelia", specs)
        self.assertIn("authentik", specs)
        self.assertEqual(specs["authelia"].default_middleware, "authelia@docker")
        self.assertEqual(specs["authentik"].default_middleware, "authentik@docker")

    def test_compose_service_names_by_provider(self):
        service_map = compose_service_names_by_provider()
        self.assertEqual(service_map.get("none"), ())
        self.assertEqual(service_map.get("authelia"), ("authelia",))
        self.assertEqual(
            service_map.get("authentik"),
            ("authentik", "authentik-worker"),
        )

    def test_merge_auth_provider_defaults_preserves_catalog_overrides(self):
        merged = merge_auth_provider_defaults(
            provider_keys=("none", "authelia", "authentik"),
            catalog_defaults={"authelia": "authelia-custom@docker"},
            override_defaults={"authentik": "authentik-custom@docker"},
        )
        self.assertEqual(merged["none"], "")
        self.assertEqual(merged["authelia"], "authelia-custom@docker")
        self.assertEqual(merged["authentik"], "authentik-custom@docker")


if __name__ == "__main__":
    unittest.main()
