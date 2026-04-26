"""Tests for the routing v2 schema dataclasses.

These cover three things:

1. **Round-trip integrity** — every shape we accept via ``from_dict``
   must serialise back via ``to_dict`` and parse back to an equal
   object. This is the structural guarantee operators rely on when
   editing the YAML by hand.
2. **Defensive defaults** — bad/None/missing inputs land on the
   schema's defaults rather than raising. Validation lives elsewhere
   (``validator.py``); the schema's job is to load whatever it sees.
3. **Wire-name remapping** — ``PathAlias.from_path`` must serialise
   as ``from`` (and vice versa) so YAML stays readable while Python
   stays keyword-safe.

These tests target the pure-data layer; no I/O, no network.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[5]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.api.services.config.routing.schema_v2 import (  # noqa: E402
    AcmeChallenge,
    AcmeDirectConfig,
    ApexAction,
    ApexConfig,
    AuthGate,
    Binding,
    CatchAllAction,
    CatchAllConfig,
    CertEntry,
    CertManagerConfig,
    CertManagerSolver,
    CertSource,
    CertStatus,
    ExposureConfig,
    HostAuth,
    HostEntry,
    HostGeoAcl,
    HostHeaders,
    HostRateLimit,
    HostTls,
    IssuerKind,
    PathAlias,
    RoutingConfigV2,
    RoutingDefaults,
    Strategy,
)


class TestEnumCoercion(unittest.TestCase):
    """Enum string-values must round-trip cleanly."""

    def test_strategy_accepts_string(self) -> None:
        cfg = RoutingConfigV2.from_dict({"strategy": "path"})
        self.assertEqual(cfg.strategy, Strategy.PATH)

    def test_strategy_unknown_falls_back_to_hybrid(self) -> None:
        cfg = RoutingConfigV2.from_dict({"strategy": "magical"})
        self.assertEqual(cfg.strategy, Strategy.HYBRID)

    def test_binding_default_is_auto(self) -> None:
        e = ExposureConfig.from_dict({})
        self.assertEqual(e.binding, Binding.AUTO)

    def test_apex_action_defaults_none(self) -> None:
        # NONE = "no apex rule, fall through". This is the
        # migration-safe default; v1 had no explicit apex handling.
        a = ApexConfig.from_dict({})
        self.assertEqual(a.action, ApexAction.NONE)

    def test_catch_all_defaults_404(self) -> None:
        c = CatchAllConfig.from_dict({})
        self.assertEqual(c.action, CatchAllAction.NOT_FOUND)


class TestExposureConfig(unittest.TestCase):
    def test_round_trip(self) -> None:
        e = ExposureConfig(
            enabled=True,
            binding=Binding.K8S_INGRESS,
            public_hostnames=["a.example.com", "b.example.com"],
            bind_addresses=["0.0.0.0:80"],
        )
        self.assertEqual(ExposureConfig.from_dict(e.to_dict()), e)

    def test_from_non_dict_returns_default(self) -> None:
        self.assertEqual(ExposureConfig.from_dict(None), ExposureConfig())
        self.assertEqual(ExposureConfig.from_dict("garbage"), ExposureConfig())

    def test_string_truthy_for_enabled(self) -> None:
        e = ExposureConfig.from_dict({"enabled": "yes"})
        self.assertTrue(e.enabled)


class TestHostEntry(unittest.TestCase):
    def _full_host(self) -> HostEntry:
        return HostEntry(
            role="media_server",
            service_id="jellyfin",
            canonical="jf.iomio.io",
            aliases=["jellyfin.iomio.io"],
            path_prefix="",
            tls=HostTls(cert_id="wildcard", force_https=True),
            auth=HostAuth(gate=AuthGate.REQUIRED, provider="authelia"),
            websocket=True,
            timeout_seconds=600,
            body_limit_mb=50000,
            headers=HostHeaders(
                response_set={"X-Frame-Options": "SAMEORIGIN"},
                response_remove=["Server"],
            ),
            rate_limit=HostRateLimit(per_second=100, burst=200),
            geo_acl=HostGeoAcl(allow=["US", "CA"], deny=[]),
            maintenance=False,
        )

    def test_full_round_trip(self) -> None:
        h = self._full_host()
        self.assertEqual(HostEntry.from_dict(h.to_dict()), h)

    def test_optional_subdataclasses_are_none_when_omitted(self) -> None:
        h = HostEntry.from_dict({"role": "auth", "service_id": "authelia",
                                 "canonical": "auth.example"})
        self.assertIsNone(h.tls)
        self.assertIsNone(h.auth)
        self.assertIsNone(h.headers)
        self.assertIsNone(h.rate_limit)
        self.assertIsNone(h.geo_acl)

    def test_aliases_coerce_to_strings(self) -> None:
        h = HostEntry.from_dict({"aliases": [1, 2, "three", None]})
        # None drops, others stringify.
        self.assertEqual(h.aliases, ["1", "2", "three"])

    def test_to_dict_omits_none_subfields(self) -> None:
        h = HostEntry(role="r", service_id="s", canonical="c.x")
        d = h.to_dict()
        self.assertNotIn("tls", d)
        self.assertNotIn("auth", d)


class TestPathAliasWireRemap(unittest.TestCase):
    """``from`` is a Python keyword — the wire form uses it; the
    dataclass uses ``from_path`` and remaps on serialisation."""

    def test_to_dict_uses_wire_keys(self) -> None:
        p = PathAlias(from_path="/a", to_path="/b", code=301)
        d = p.to_dict()
        self.assertEqual(d, {"from": "/a", "to": "/b", "code": 301})

    def test_from_dict_accepts_wire_keys(self) -> None:
        p = PathAlias.from_dict({"from": "/x", "to": "/y", "code": 308})
        self.assertEqual(p.from_path, "/x")
        self.assertEqual(p.to_path, "/y")
        self.assertEqual(p.code, 308)

    def test_from_dict_accepts_python_keys(self) -> None:
        p = PathAlias.from_dict({"from_path": "/x", "to_path": "/y"})
        self.assertEqual(p.from_path, "/x")
        self.assertEqual(p.to_path, "/y")

    def test_round_trip_via_wire(self) -> None:
        p1 = PathAlias(from_path="/old", to_path="/new")
        p2 = PathAlias.from_dict(p1.to_dict())
        self.assertEqual(p1, p2)


class TestApexConfig(unittest.TestCase):
    def test_round_trip(self) -> None:
        a = ApexConfig(action=ApexAction.REDIRECT, target="/apps", code=302)
        self.assertEqual(ApexConfig.from_dict(a.to_dict()), a)

    def test_static_action_round_trip(self) -> None:
        a = ApexConfig(action=ApexAction.STATIC, target="<html>hi</html>", code=200)
        self.assertEqual(ApexConfig.from_dict(a.to_dict()), a)


class TestCatchAllConfig(unittest.TestCase):
    def test_round_trip_redirect(self) -> None:
        c = CatchAllConfig(action=CatchAllAction.REDIRECT, target="/apps", code=302)
        self.assertEqual(CatchAllConfig.from_dict(c.to_dict()), c)

    def test_round_trip_with_custom_404_body(self) -> None:
        c = CatchAllConfig(action=CatchAllAction.NOT_FOUND,
                            custom_404_body="<h1>not found</h1>")
        self.assertEqual(CatchAllConfig.from_dict(c.to_dict()), c)

    def test_404_action_value_is_string(self) -> None:
        c = CatchAllConfig(action=CatchAllAction.NOT_FOUND)
        self.assertEqual(c.to_dict()["action"], "404")


class TestCertEntry(unittest.TestCase):
    def test_cert_manager_full_round_trip(self) -> None:
        c = CertEntry(
            id="wildcard-iomio-io",
            source=CertSource.CERT_MANAGER,
            common_name="*.iomio.io",
            sans=["iomio.io", "*.iomio.io"],
            cert_manager=CertManagerConfig(
                issuer_kind=IssuerKind.CLUSTER_ISSUER,
                issuer_name="letsencrypt-prod",
                challenge=AcmeChallenge.DNS01,
                solver=CertManagerSolver(
                    provider="cloudflare",
                    secret_ref="cf-api-token",
                ),
                secret_name="wildcard-iomio-io-tls",
            ),
            auto_renew=True,
            status=CertStatus.READY,
            expires_at="2026-08-01T00:00:00Z",
        )
        c2 = CertEntry.from_dict(c.to_dict())
        self.assertEqual(c2, c)

    def test_acme_direct_round_trip(self) -> None:
        c = CertEntry(
            id="api-iomio-io",
            source=CertSource.ACME_DIRECT,
            common_name="api.iomio.io",
            acme_direct=AcmeDirectConfig(
                directory_url="https://acme-staging-v02.api.letsencrypt.org/directory",
                email="ops@iomio.io",
                challenge=AcmeChallenge.HTTP01,
            ),
        )
        c2 = CertEntry.from_dict(c.to_dict())
        self.assertEqual(c2, c)

    def test_uploaded_cert_no_subblocks(self) -> None:
        c = CertEntry(id="manual", source=CertSource.UPLOADED, common_name="x.example")
        d = c.to_dict()
        self.assertNotIn("cert_manager", d)
        self.assertNotIn("acme_direct", d)


class TestRoutingDefaults(unittest.TestCase):
    def test_round_trip(self) -> None:
        d = RoutingDefaults(
            websocket=False,
            auth=HostAuth(gate=AuthGate.REQUIRED, provider="authelia"),
            timeout_seconds=60,
            body_limit_mb=100,
            headers=HostHeaders(response_set={"HSTS": "max-age=1"}),
        )
        self.assertEqual(RoutingDefaults.from_dict(d.to_dict()), d)


class TestRoutingConfigV2RoundTrip(unittest.TestCase):
    """Top-level config — assemble a realistic shape, round-trip."""

    def test_full_shape_round_trip(self) -> None:
        cfg = RoutingConfigV2(
            base_domain="iomio.io",
            stack_subdomain="m",
            gateway_host="m.iomio.io",
            gateway_port=443,
            strategy=Strategy.HYBRID,
            scheme="https",
            app_path_prefix="/app",
            exposure=ExposureConfig(
                enabled=True,
                binding=Binding.K8S_INGRESS,
                public_hostnames=["m.iomio.io", "jf.iomio.io"],
            ),
            hosts=[
                HostEntry(role="media_server", service_id="jellyfin",
                          canonical="jf.iomio.io",
                          aliases=["jellyfin.iomio.io"],
                          tls=HostTls(cert_id="wildcard"),
                          auth=HostAuth(gate=AuthGate.REQUIRED, provider="authelia")),
                HostEntry(role="auth", service_id="authelia",
                          canonical="auth.iomio.io",
                          auth=HostAuth(gate=AuthGate.NONE)),
            ],
            path_aliases=[
                PathAlias(from_path="/app/jellyfin", to_path="/app/jf", code=301),
                PathAlias(from_path="/app/media-stack-ui", to_path="/app/ui", code=301),
            ],
            apex=ApexConfig(action=ApexAction.REDIRECT, target="/apps", code=302),
            catch_all=CatchAllConfig(action=CatchAllAction.REDIRECT, target="/apps"),
            certs=[
                CertEntry(id="wildcard", source=CertSource.CERT_MANAGER,
                          common_name="*.iomio.io",
                          cert_manager=CertManagerConfig(
                              issuer_kind=IssuerKind.CLUSTER_ISSUER,
                              issuer_name="letsencrypt-prod",
                              challenge=AcmeChallenge.DNS01,
                              solver=CertManagerSolver(provider="cloudflare",
                                                       secret_ref="cf-api-token"),
                          )),
            ],
            defaults=RoutingDefaults(
                websocket=False,
                timeout_seconds=60,
                auth=HostAuth(gate=AuthGate.REQUIRED, provider="authelia"),
            ),
        )
        cfg2 = RoutingConfigV2.from_dict(cfg.to_dict())
        self.assertEqual(cfg2, cfg)

    def test_empty_dict_is_valid_default(self) -> None:
        cfg = RoutingConfigV2.from_dict({})
        self.assertEqual(cfg.version, 2)
        self.assertEqual(cfg.strategy, Strategy.HYBRID)
        self.assertEqual(cfg.exposure.enabled, False)
        self.assertEqual(cfg.hosts, [])

    def test_unknown_keys_are_dropped(self) -> None:
        cfg = RoutingConfigV2.from_dict({
            "base_domain": "x",
            "garbage_key": "ignored",
            "another": [1, 2, 3],
        })
        self.assertEqual(cfg.base_domain, "x")
        # Unknown keys must not appear in the round-tripped dict.
        d = cfg.to_dict()
        self.assertNotIn("garbage_key", d)
        self.assertNotIn("another", d)

    def test_to_dict_keys_are_stable_order(self) -> None:
        # Operators diff routing.yaml across releases; key order
        # changes turn into noisy git diffs. The test pins the order.
        cfg = RoutingConfigV2()
        keys = list(cfg.to_dict().keys())
        self.assertEqual(keys[0], "version")
        self.assertEqual(keys[1], "base_domain")
        self.assertIn("hosts", keys)
        self.assertIn("path_aliases", keys)
        self.assertIn("apex", keys)
        self.assertIn("catch_all", keys)


if __name__ == "__main__":
    unittest.main()
