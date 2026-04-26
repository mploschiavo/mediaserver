"""Regression tests for ``ConfigureAuthJob._resolve_domain_pair``.

The 2026-04-20 SSO outage: Authelia 4.38 crashlooped because its
``session.cookies[0].domain`` was the bare ``"local"`` (no period).
Authelia 4.38 enforces "must have at least a single period or be
an IP address". 4.37 did not, so the latent bug went unnoticed
across multiple deploys.

Root cause: ``_resolve_domain_pair`` only read ``ingress.subdomain``
when picking the stack subdomain. The compose profile keeps the
subdomain under ``routing.stack_subdomain`` (alongside
``base_domain``), so the resolver returned ``("local", "")`` —
which the Authelia generator interpreted as "flat topology, use
the bare base as cookie domain" → ``cookie_domain="local"``.

These tests pin every input shape we've shipped, including the
exact one that broke production, so an Authelia upgrade can't
silently re-expose this class of bug."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.core.auth.configure_auth_job import (  # noqa: E402
    ConfigureAuthJob,
)


class DomainResolverTests(unittest.TestCase):

    def setUp(self) -> None:
        self.job = ConfigureAuthJob()

    # ------------------------------------------------------------------
    # The 2026-04-20 regression
    # ------------------------------------------------------------------

    def test_routing_stack_subdomain_is_honored(self) -> None:
        """The compose-shaped profile that broke production: only
        ``routing.base_domain`` and ``routing.stack_subdomain`` are
        set; ``ingress`` is empty. Resolver must return the
        subdomain — otherwise the cookie domain ends up as bare
        ``local``, which Authelia 4.38 rejects."""
        ingress = {}
        routing = {
            "base_domain": "local",
            "stack_subdomain": "media-stack",
            "gateway_host": "apps.media-stack.local",
        }
        base, sub = self.job._resolve_domain_pair(ingress, routing)
        self.assertEqual(base, "local")
        self.assertEqual(
            sub, "media-stack",
            "Resolver dropped the subdomain. The Authelia generator "
            "will then build cookie_domain='local' — Authelia 4.38 "
            "rejects single-label domains and crashloops.",
        )

    # ------------------------------------------------------------------
    # The shapes the docs / install path support
    # ------------------------------------------------------------------

    def test_explicit_ingress_section_wins(self) -> None:
        """When both ``ingress`` and ``routing`` declare a domain,
        ``ingress`` wins (it's the canonical place per the install
        wizard)."""
        ingress = {"domain": "example.com", "subdomain": "stack"}
        routing = {"base_domain": "local", "stack_subdomain": "wrong"}
        base, sub = self.job._resolve_domain_pair(ingress, routing)
        self.assertEqual(base, "example.com")
        self.assertEqual(sub, "stack")

    def test_flat_k8s_layout_no_subdomain(self) -> None:
        """K8s flat layout: only ``routing.base_domain`` set.
        Resolver must return sub="" so the generator picks the
        flat-topology branch."""
        ingress = {}
        routing = {"base_domain": "iomio.io"}
        base, sub = self.job._resolve_domain_pair(ingress, routing)
        self.assertEqual(base, "iomio.io")
        self.assertEqual(sub, "")

    def test_gateway_host_fallback(self) -> None:
        """No explicit base anywhere — derive from gateway_host."""
        ingress = {}
        routing = {"gateway_host": "apps.media-stack.local"}
        base, sub = self.job._resolve_domain_pair(ingress, routing)
        self.assertEqual(base, "media-stack.local")
        self.assertEqual(sub, "apps")

    def test_default_when_nothing_set(self) -> None:
        """Empty profile must still produce a sensible default."""
        base, sub = self.job._resolve_domain_pair({}, {})
        self.assertEqual(base, "local")
        self.assertEqual(sub, "media-stack")

    def test_ingress_subdomain_only_no_base(self) -> None:
        """Having a subdomain but no base is nonsense; resolver
        falls through to the gateway_host or default path."""
        ingress = {"subdomain": "stack"}
        routing = {"base_domain": "example.com"}
        base, sub = self.job._resolve_domain_pair(ingress, routing)
        # routing.base_domain takes precedence; ingress.subdomain
        # is honored as a fallback.
        self.assertEqual(base, "example.com")
        self.assertEqual(sub, "stack")


class CookieDomainProducesValidAutheliaShapeTests(unittest.TestCase):
    """End-to-end pin: every (base, sub) the resolver returns must
    survive Authelia 4.38's domain validation when fed through the
    full generator. Authelia rejects:

    - bare single-label domains ("local")
    - leading/trailing dots
    - empty strings"""

    def _config(self, **kw):
        from media_stack.core.auth.authelia_config_generator import (
            AutheliaConfigGenerator,
            AutheliaConfigOptions,
        )
        opts = AutheliaConfigOptions(
            gateway_port=443, admin_username="admin",
            admin_email="admin@local", **kw,
        )
        return AutheliaConfigGenerator(opts).generate_configuration()

    def _assert_valid_cookie_domain(self, domain: str) -> None:
        self.assertNotEqual(
            domain, "",
            "Authelia rejects empty cookie domains.",
        )
        self.assertFalse(
            domain.startswith(".") or domain.endswith("."),
            f"Authelia rejects leading/trailing dots: {domain!r}",
        )
        # The 4.38 rule: must have at least one period OR be an IP.
        is_ip = all(part.isdigit() for part in domain.split("."))
        self.assertTrue(
            "." in domain or is_ip,
            f"Authelia 4.38 rejects single-label domains: {domain!r}",
        )

    def test_compose_profile_yields_valid_cookie(self) -> None:
        cfg = self._config(
            base_domain="local", stack_subdomain="media-stack",
            gateway_host="apps.media-stack.local",
        )
        domain = cfg["session"]["cookies"][0]["domain"]
        self._assert_valid_cookie_domain(domain)
        self.assertEqual(domain, "media-stack.local")

    def test_k8s_flat_profile_yields_valid_cookie(self) -> None:
        cfg = self._config(
            base_domain="iomio.io", stack_subdomain="",
            gateway_host="m.iomio.io",
        )
        domain = cfg["session"]["cookies"][0]["domain"]
        self._assert_valid_cookie_domain(domain)
        self.assertEqual(domain, "iomio.io")


if __name__ == "__main__":
    unittest.main()
