"""Tests for the routing v2 validator (VR-1 through VR-11).

Each validation rule has at least one rejecting test case + the
baseline-accepting test asserts that every VR can pass. The "every VR
has a test" guarantee is enforced by ``test_routing_validation_rules_ratchet.py``
under ``tests/unit/ratchets/``.

The validator is pure-data: no I/O, no network, no global state. Tests
exercise the rules with hand-crafted ``RoutingConfigV2`` instances.
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
    CatchAllAction,
    CatchAllConfig,
    CertEntry,
    CertManagerConfig,
    CertManagerSolver,
    CertSource,
    HostAuth,
    HostEntry,
    HostTls,
    IssuerKind,
    PathAlias,
    RoutingConfigV2,
)
from media_stack.api.services.config.routing.validator import (  # noqa: E402
    ValidationError,
    validate_routing_config,
)


KNOWN_SERVICES = {"jellyfin", "authelia", "sonarr", "radarr", "homepage"}


def _baseline() -> RoutingConfigV2:
    """A minimal valid config — used as the starting point for each
    rejecting test (mutate one field, expect one error)."""
    return RoutingConfigV2(
        gateway_host="m.iomio.io",
        hosts=[
            HostEntry(role="media_server", service_id="jellyfin",
                      canonical="jf.iomio.io",
                      tls=HostTls(cert_id="wildcard"),
                      auth=HostAuth(gate=AuthGate.REQUIRED, provider="authelia")),
            HostEntry(role="auth", service_id="authelia",
                      canonical="auth.iomio.io",
                      auth=HostAuth(gate=AuthGate.NONE)),
        ],
        certs=[
            CertEntry(id="wildcard", source=CertSource.UPLOADED,
                      common_name="*.iomio.io"),
        ],
    )


class TestBaselineValidates(unittest.TestCase):
    def test_baseline_passes(self) -> None:
        errs = validate_routing_config(_baseline(),
                                       known_service_ids=KNOWN_SERVICES)
        self.assertEqual(errs, [], f"unexpected errors: {errs}")


class TestVR1CanonicalUniqueness(unittest.TestCase):
    """VR-1: two hosts cannot share the same canonical."""

    def test_duplicate_canonical_rejected(self) -> None:
        cfg = _baseline()
        cfg.hosts.append(HostEntry(role="copy", service_id="sonarr",
                                    canonical="jf.iomio.io"))
        errs = validate_routing_config(cfg, known_service_ids=KNOWN_SERVICES)
        codes = {e.code for e in errs}
        self.assertIn("VR-1", codes)
        # The error points at the duplicate, not the original.
        offending = [e for e in errs if e.code == "VR-1"][0]
        self.assertIn("hosts[2]", offending.field)


class TestVR2AliasNonOverlap(unittest.TestCase):
    """VR-2: a canonical can't appear in another host's aliases."""

    def test_alias_collision_rejected(self) -> None:
        cfg = _baseline()
        cfg.hosts[1].aliases = ["jf.iomio.io"]   # already canonical
        errs = validate_routing_config(cfg, known_service_ids=KNOWN_SERVICES)
        self.assertIn("VR-2", {e.code for e in errs})


class TestVR3UnknownServiceId(unittest.TestCase):
    """VR-3: every service_id must exist in the registry."""

    def test_unknown_service_rejected(self) -> None:
        cfg = _baseline()
        cfg.hosts.append(HostEntry(role="extra", service_id="not_a_service",
                                    canonical="x.iomio.io"))
        errs = validate_routing_config(cfg, known_service_ids=KNOWN_SERVICES)
        self.assertIn("VR-3", {e.code for e in errs})

    def test_empty_known_set_disables_vr3(self) -> None:
        cfg = _baseline()
        cfg.hosts.append(HostEntry(role="extra", service_id="anything",
                                    canonical="x.iomio.io"))
        errs = validate_routing_config(cfg, known_service_ids=set())
        self.assertNotIn("VR-3", {e.code for e in errs})


class TestVR4PathAliasShape(unittest.TestCase):
    """VR-4: path_aliases entries must start with '/'."""

    def test_missing_leading_slash_rejected(self) -> None:
        cfg = _baseline()
        cfg.path_aliases.append(PathAlias(from_path="app/jellyfin",
                                          to_path="/app/jf"))
        errs = validate_routing_config(cfg, known_service_ids=KNOWN_SERVICES)
        codes = {e.code for e in errs}
        self.assertIn("VR-4", codes)


class TestVR5ApexTarget(unittest.TestCase):
    """VR-5: apex.target must be present and well-shaped when action
    is REDIRECT or SERVICE."""

    def test_redirect_without_target_rejected(self) -> None:
        cfg = _baseline()
        cfg.apex = ApexConfig(action=ApexAction.REDIRECT, target="")
        errs = validate_routing_config(cfg, known_service_ids=KNOWN_SERVICES)
        self.assertIn("VR-5", {e.code for e in errs})

    def test_redirect_target_must_be_path(self) -> None:
        cfg = _baseline()
        cfg.apex = ApexConfig(action=ApexAction.REDIRECT, target="apps")
        errs = validate_routing_config(cfg, known_service_ids=KNOWN_SERVICES)
        self.assertIn("VR-5", {e.code for e in errs})

    def test_service_apex_unknown_service(self) -> None:
        cfg = _baseline()
        cfg.apex = ApexConfig(action=ApexAction.SERVICE, target="not_real")
        errs = validate_routing_config(cfg, known_service_ids=KNOWN_SERVICES)
        self.assertIn("VR-5", {e.code for e in errs})

    def test_apex_none_is_always_valid(self) -> None:
        cfg = _baseline()
        cfg.apex = ApexConfig(action=ApexAction.NONE)
        errs = validate_routing_config(cfg, known_service_ids=KNOWN_SERVICES)
        self.assertEqual(errs, [])


class TestVR6CatchAllTarget(unittest.TestCase):
    def test_redirect_without_target_rejected(self) -> None:
        cfg = _baseline()
        cfg.catch_all = CatchAllConfig(action=CatchAllAction.REDIRECT, target="")
        errs = validate_routing_config(cfg, known_service_ids=KNOWN_SERVICES)
        self.assertIn("VR-6", {e.code for e in errs})

    def test_redirect_target_must_be_path(self) -> None:
        cfg = _baseline()
        cfg.catch_all = CatchAllConfig(action=CatchAllAction.REDIRECT, target="apps")
        errs = validate_routing_config(cfg, known_service_ids=KNOWN_SERVICES)
        self.assertIn("VR-6", {e.code for e in errs})

    def test_404_action_needs_no_target(self) -> None:
        cfg = _baseline()
        cfg.catch_all = CatchAllConfig(action=CatchAllAction.NOT_FOUND)
        errs = validate_routing_config(cfg, known_service_ids=KNOWN_SERVICES)
        self.assertEqual(errs, [])

    def test_service_action_without_target_rejected(self) -> None:
        cfg = _baseline()
        cfg.catch_all = CatchAllConfig(action=CatchAllAction.SERVICE, target="")
        errs = validate_routing_config(cfg, known_service_ids=KNOWN_SERVICES)
        self.assertIn("VR-6", {e.code for e in errs})

    def test_service_action_unknown_service_rejected(self) -> None:
        cfg = _baseline()
        cfg.catch_all = CatchAllConfig(action=CatchAllAction.SERVICE,
                                        target="not_a_service")
        errs = validate_routing_config(cfg, known_service_ids=KNOWN_SERVICES)
        self.assertIn("VR-6", {e.code for e in errs})


class TestVR7TlsCertReference(unittest.TestCase):
    def test_unknown_cert_id_rejected(self) -> None:
        cfg = _baseline()
        cfg.hosts[0].tls = HostTls(cert_id="not_present")
        errs = validate_routing_config(cfg, known_service_ids=KNOWN_SERVICES)
        self.assertIn("VR-7", {e.code for e in errs})

    def test_empty_cert_id_skips_check(self) -> None:
        # An empty cert_id means "use defaults"; not a missing
        # reference, just no TLS preference.
        cfg = _baseline()
        cfg.hosts[0].tls = HostTls(cert_id="")
        errs = validate_routing_config(cfg, known_service_ids=KNOWN_SERVICES)
        self.assertNotIn("VR-7", {e.code for e in errs})


class TestVR8AuthRequiredNeedsProvider(unittest.TestCase):
    def test_required_gate_without_provider_rejected(self) -> None:
        cfg = _baseline()
        cfg.hosts[0].auth = HostAuth(gate=AuthGate.REQUIRED, provider="")
        errs = validate_routing_config(cfg, known_service_ids=KNOWN_SERVICES)
        self.assertIn("VR-8", {e.code for e in errs})

    def test_optional_gate_without_provider_accepted(self) -> None:
        # Optional gate doesn't need a provider.
        cfg = _baseline()
        cfg.hosts[0].auth = HostAuth(gate=AuthGate.OPTIONAL, provider="")
        errs = validate_routing_config(cfg, known_service_ids=KNOWN_SERVICES)
        self.assertNotIn("VR-8", {e.code for e in errs})


class TestVR9PathAliasShadowsHostPrefix(unittest.TestCase):
    def test_alias_shadows_host_path_prefix(self) -> None:
        cfg = _baseline()
        cfg.hosts.append(HostEntry(role="dashboard", service_id="homepage",
                                    canonical="m.iomio.io",
                                    path_prefix="/apps"))
        cfg.path_aliases.append(PathAlias(from_path="/apps", to_path="/elsewhere"))
        errs = validate_routing_config(cfg, known_service_ids=KNOWN_SERVICES)
        self.assertIn("VR-9", {e.code for e in errs})


class TestVR10DnsSolverNeedsSecretRef(unittest.TestCase):
    def test_cloudflare_dns01_without_secret_ref_rejected(self) -> None:
        cfg = _baseline()
        cfg.certs.append(CertEntry(
            id="wildcard-iomio-io-cm",
            source=CertSource.CERT_MANAGER,
            common_name="*.iomio.io",
            cert_manager=CertManagerConfig(
                issuer_kind=IssuerKind.CLUSTER_ISSUER,
                issuer_name="letsencrypt-prod",
                challenge=AcmeChallenge.DNS01,
                solver=CertManagerSolver(provider="cloudflare", secret_ref=""),
            ),
        ))
        errs = validate_routing_config(cfg, known_service_ids=KNOWN_SERVICES)
        self.assertIn("VR-10", {e.code for e in errs})

    def test_http01_doesnt_need_secret_ref(self) -> None:
        cfg = _baseline()
        cfg.certs.append(CertEntry(
            id="wildcard-iomio-io-http",
            source=CertSource.CERT_MANAGER,
            common_name="api.iomio.io",
            cert_manager=CertManagerConfig(
                issuer_kind=IssuerKind.CLUSTER_ISSUER,
                issuer_name="letsencrypt-prod",
                challenge=AcmeChallenge.HTTP01,
            ),
        ))
        errs = validate_routing_config(cfg, known_service_ids=KNOWN_SERVICES)
        self.assertNotIn("VR-10", {e.code for e in errs})


class TestVR11AcmeDirectOnlyOnCompose(unittest.TestCase):
    def test_acme_direct_rejected_on_k8s(self) -> None:
        cfg = _baseline()
        cfg.certs.append(CertEntry(
            id="acme",
            source=CertSource.ACME_DIRECT,
            common_name="x.example",
            acme_direct=AcmeDirectConfig(email="ops@example"),
        ))
        errs = validate_routing_config(cfg,
                                       known_service_ids=KNOWN_SERVICES,
                                       deploy_mode="k8s")
        self.assertIn("VR-11", {e.code for e in errs})

    def test_acme_direct_accepted_on_compose(self) -> None:
        cfg = _baseline()
        cfg.certs.append(CertEntry(
            id="acme",
            source=CertSource.ACME_DIRECT,
            common_name="x.example",
            acme_direct=AcmeDirectConfig(email="ops@example"),
        ))
        errs = validate_routing_config(cfg,
                                       known_service_ids=KNOWN_SERVICES,
                                       deploy_mode="compose")
        self.assertNotIn("VR-11", {e.code for e in errs})

    def test_acme_direct_skipped_when_deploy_mode_auto(self) -> None:
        cfg = _baseline()
        cfg.certs.append(CertEntry(
            id="acme",
            source=CertSource.ACME_DIRECT,
            common_name="x.example",
            acme_direct=AcmeDirectConfig(email="ops@example"),
        ))
        errs = validate_routing_config(cfg,
                                       known_service_ids=KNOWN_SERVICES,
                                       deploy_mode="auto")
        self.assertNotIn("VR-11", {e.code for e in errs})


class TestErrorOrdering(unittest.TestCase):
    """Errors are sorted by (field, code) so the UI groups them
    deterministically per-field."""

    def test_errors_sorted_by_field(self) -> None:
        cfg = _baseline()
        # Trigger two errors at different fields.
        cfg.hosts[0].tls = HostTls(cert_id="missing")
        cfg.apex = ApexConfig(action=ApexAction.REDIRECT, target="")
        errs = validate_routing_config(cfg, known_service_ids=KNOWN_SERVICES)
        fields = [e.field for e in errs]
        self.assertEqual(fields, sorted(fields))


class TestValidationErrorShape(unittest.TestCase):
    """The ``ValidationError`` is the contract with the UI — every
    error must carry a code, field, message, and (optionally) hint."""

    def test_error_has_all_fields(self) -> None:
        cfg = _baseline()
        cfg.apex = ApexConfig(action=ApexAction.REDIRECT, target="")
        errs = validate_routing_config(cfg, known_service_ids=KNOWN_SERVICES)
        e = errs[0]
        self.assertIsInstance(e, ValidationError)
        self.assertTrue(e.code)
        self.assertTrue(e.field)
        self.assertTrue(e.message)
        # Hints are encouraged but not strictly required; assert it's a string.
        self.assertIsInstance(e.hint, str)


if __name__ == "__main__":
    unittest.main()
