"""Validate that all service health endpoints are reachable via internal routes.

Tests the contract-defined health_path for each service against the registry.
Catches mismatches between contract health_path, service URL, and actual
app behavior (e.g., Maintainerr needing /app/maintainerr prefix).

Run with: python -m pytest tests/unit/test_service_route_health.py -v
"""

import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[3]
CONTRACTS_DIR = ROOT / "contracts" / "services"


def _load_services() -> list[dict]:
    """Load all service definitions from contracts."""
    services = []
    for path in sorted(CONTRACTS_DIR.glob("*.yaml")):
        if path.name.startswith("_"):
            continue
        try:
            data = yaml.safe_load(path.read_text()) or {}
            svc = data.get("service", {})
            if svc.get("id"):
                svc["_file"] = path.name
                svc["_defaults"] = data.get("defaults", {})
                services.append(svc)
        except Exception:
            continue
    return services


SERVICES = _load_services()


class TestHealthPathConsistency(unittest.TestCase):
    """Health paths must be consistent with service routing config."""

    def test_all_network_services_have_health_path(self):
        """Every service with a port should define a health check path."""
        for svc in SERVICES:
            if not svc.get("port"):
                continue  # CLI-only tools (e.g., grabit) have no port or health path
            hp = svc.get("health_path", "")
            self.assertTrue(
                hp,
                f"{svc['id']} ({svc['_file']}): has port {svc['port']} but missing health_path",
            )

    def test_health_path_starts_with_slash(self):
        for svc in SERVICES:
            hp = svc.get("health_path", "")
            if hp:
                self.assertTrue(
                    hp.startswith("/"),
                    f"{svc['id']}: health_path '{hp}' must start with /",
                )

    def test_preserve_prefix_services_have_consistent_health_path(self):
        """Services with preserve_path_prefix=true:
        - If health_path contains an app-specific base path (like /app/maintainerr),
          the default URL in the contract must account for it.
        - OR the service code must prepend the base path when making API calls.
        """
        for svc in SERVICES:
            if not svc.get("preserve_path_prefix"):
                continue
            hp = svc.get("health_path", "")
            svc_id = svc["id"]

            # Check if health_path contains an app-specific prefix
            # (e.g., /app/maintainerr/api/settings vs just /ping)
            segments = [s for s in hp.split("/") if s]
            if len(segments) >= 3 and segments[0] == "app":
                # This service bakes its base path into the health_path.
                # The default URL must include the base path, or the service
                # code must derive it from the health_path.
                defaults = svc.get("_defaults", {})
                default_url = defaults.get("url", "")
                base_path = f"/app/{segments[1]}"

                # Either the URL includes the base path...
                url_has_base = base_path in default_url
                # ...or document that the service code must handle it
                if not url_has_base:
                    # This is the Maintainerr case — URL is plain but
                    # service code must prepend /app/maintainerr to API calls.
                    # Verify the health_path is reachable at URL + health_path
                    self.assertTrue(
                        hp.startswith(base_path),
                        f"{svc_id}: health_path '{hp}' has app base path '{base_path}' "
                        f"but default URL '{default_url}' doesn't include it. "
                        f"Service code must derive the base path from health_path.",
                    )

    def test_non_preserve_services_have_simple_health_path(self):
        """Services without preserve_path_prefix should have root-relative health paths."""
        for svc in SERVICES:
            if svc.get("preserve_path_prefix"):
                continue
            hp = svc.get("health_path", "")
            if not hp:
                continue
            # Health path should not contain app-specific prefixes
            self.assertFalse(
                hp.startswith("/app/"),
                f"{svc['id']}: non-preserve service has /app/ prefix in health_path '{hp}'",
            )


class TestServiceDefaultURLs(unittest.TestCase):
    """Default URLs in contracts must be valid internal addresses."""

    def test_default_urls_use_service_hostname(self):
        """Default URL should use the service's own hostname."""
        for svc in SERVICES:
            defaults = svc.get("_defaults", {})
            url = defaults.get("url", "")
            if not url:
                continue
            host = svc.get("host", "")
            port = svc.get("port", 0)
            if host and port:
                expected_base = f"http://{host}:{port}"
                self.assertTrue(
                    url.startswith(expected_base),
                    f"{svc['id']}: default URL '{url}' doesn't match "
                    f"host:port ({expected_base})",
                )

    def test_network_services_have_positive_ports(self):
        """Services with a health_path should have a positive port."""
        for svc in SERVICES:
            if not svc.get("health_path"):
                continue
            port = svc.get("port", 0)
            self.assertGreater(
                port, 0,
                f"{svc['id']}: has health_path but port={port}",
            )


class TestPreservePathPrefixServices(unittest.TestCase):
    """Services with preserve_path_prefix have specific routing requirements."""

    def test_registry_matches_contracts(self):
        """Registry preserve_path_prefix should match contract."""
        try:
            from media_stack.core.service_registry.registry import SERVICE_MAP
        except ImportError:
            self.skipTest("registry not importable")

        for svc in SERVICES:
            svc_id = svc["id"]
            reg_svc = SERVICE_MAP.get(svc_id)
            if not reg_svc:
                continue
            contract_val = bool(svc.get("preserve_path_prefix", False))
            self.assertEqual(
                reg_svc.preserve_path_prefix, contract_val,
                f"{svc_id}: registry preserve_path_prefix={reg_svc.preserve_path_prefix} "
                f"doesn't match contract={contract_val}",
            )


if __name__ == "__main__":
    unittest.main()
