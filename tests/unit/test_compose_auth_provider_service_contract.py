import sys
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from core.auth.provider_registry import compose_service_names_by_provider  # noqa: E402


class ComposeAuthProviderServiceContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compose_path = ROOT / "docker" / "docker-compose.yml"
        payload = yaml.safe_load(compose_path.read_text(encoding="utf-8")) or {}
        cls.services = dict(payload.get("services") or {})

    def test_compose_declares_auth_provider_services(self):
        self.assertIn("authelia", self.services)
        self.assertIn("authentik", self.services)
        self.assertIn("authentik-worker", self.services)

    def test_registry_compose_services_exist_in_compose_spec(self):
        service_map = compose_service_names_by_provider()
        for provider in ("authelia", "authentik"):
            for service_name in tuple(service_map.get(provider) or ()):  # pragma: no branch
                self.assertIn(service_name, self.services)

    def test_traefik_forwardauth_middlewares_are_declared(self):
        authelia_labels = self.services.get("authelia", {}).get("labels") or []
        authentik_labels = self.services.get("authentik", {}).get("labels") or []
        authelia_text = "\n".join(str(item) for item in authelia_labels)
        authentik_text = "\n".join(str(item) for item in authentik_labels)

        self.assertIn("traefik.http.middlewares.authelia.forwardauth.address", authelia_text)
        self.assertIn("traefik.http.middlewares.authentik.forwardauth.address", authentik_text)


if __name__ == "__main__":
    unittest.main()
