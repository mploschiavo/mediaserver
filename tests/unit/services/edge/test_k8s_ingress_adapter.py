"""Tests for ``K8sIngressAdapter`` — pure-function checks on the
ApplyPlan it derives from a RoutingConfigV2.

No kubectl, no live K8s API. The adapter ships only dicts; the caller
applies them.
"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.api.services.config.routing.schema_v2 import (  # noqa: E402
    AcmeChallenge,
    Binding,
    CertEntry,
    CertManagerConfig,
    CertManagerSolver,
    CertSource,
    ExposureConfig,
    HostEntry,
    HostTls,
    IssuerKind,
    RoutingConfigV2,
)
from media_stack.services.edge.binding_adapter import (  # noqa: E402
    detect_active_adapter,
)
from media_stack.services.edge.k8s_ingress_adapter import (  # noqa: E402
    K8sIngressAdapter,
)


def _baseline() -> RoutingConfigV2:
    cfg = RoutingConfigV2(
        gateway_host="m.iomio.io",
        exposure=ExposureConfig(
            enabled=True,
            binding=Binding.K8S_INGRESS,
        ),
    )
    cfg.hosts.append(HostEntry(role="media_server", service_id="jellyfin",
                                canonical="jf.iomio.io",
                                aliases=["jellyfin.iomio.io"],
                                tls=HostTls(cert_id="wildcard")))
    cfg.hosts.append(HostEntry(role="auth", service_id="authelia",
                                canonical="auth.iomio.io",
                                tls=HostTls(cert_id="wildcard")))
    cfg.certs.append(CertEntry(
        id="wildcard",
        source=CertSource.CERT_MANAGER,
        common_name="*.iomio.io",
        sans=["iomio.io", "*.iomio.io"],
        cert_manager=CertManagerConfig(
            issuer_kind=IssuerKind.CLUSTER_ISSUER,
            issuer_name="letsencrypt-prod",
            challenge=AcmeChallenge.HTTP01,
        ),
    ))
    return cfg


class TestDetect(unittest.TestCase):
    def test_detect_true_when_kubernetes_env(self) -> None:
        os.environ["KUBERNETES_SERVICE_HOST"] = "10.0.0.1"
        try:
            self.assertTrue(K8sIngressAdapter().detect())
        finally:
            del os.environ["KUBERNETES_SERVICE_HOST"]

    def test_detect_false_outside_k8s(self) -> None:
        os.environ.pop("KUBERNETES_SERVICE_HOST", None)
        self.assertFalse(K8sIngressAdapter().detect())


class TestServicePatch(unittest.TestCase):
    def test_clusterip_when_exposure_disabled(self) -> None:
        cfg = _baseline()
        cfg.exposure.enabled = False
        plan = K8sIngressAdapter().compute_apply_plan(cfg)
        svc = next(s for s in plan.steps if s.kind == "service.patch")
        self.assertEqual(svc.payload["spec"]["type"], "ClusterIP")

    def test_loadbalancer_when_binding_loadbalancer(self) -> None:
        cfg = _baseline()
        cfg.exposure.binding = Binding.K8S_LOADBALANCER
        plan = K8sIngressAdapter().compute_apply_plan(cfg)
        svc = next(s for s in plan.steps if s.kind == "service.patch")
        self.assertEqual(svc.payload["spec"]["type"], "LoadBalancer")

    def test_clusterip_when_binding_ingress(self) -> None:
        # Ingress fronts the Service, so it stays internal.
        cfg = _baseline()
        cfg.exposure.binding = Binding.K8S_INGRESS
        plan = K8sIngressAdapter().compute_apply_plan(cfg)
        svc = next(s for s in plan.steps if s.kind == "service.patch")
        self.assertEqual(svc.payload["spec"]["type"], "ClusterIP")


class TestIngressEmission(unittest.TestCase):
    def test_ingress_lists_every_hostname(self) -> None:
        cfg = _baseline()
        plan = K8sIngressAdapter().compute_apply_plan(cfg)
        ingress = next(s for s in plan.steps if s.kind == "ingress.apply").payload
        rule_hosts = {r["host"] for r in ingress["spec"]["rules"]}
        self.assertIn("jf.iomio.io", rule_hosts)
        self.assertIn("jellyfin.iomio.io", rule_hosts)  # alias
        self.assertIn("auth.iomio.io", rule_hosts)
        self.assertIn("m.iomio.io", rule_hosts)         # gateway

    def test_ingress_class_default_nginx(self) -> None:
        plan = K8sIngressAdapter().compute_apply_plan(_baseline())
        ingress = next(s for s in plan.steps if s.kind == "ingress.apply").payload
        self.assertEqual(ingress["spec"]["ingressClassName"], "nginx")

    def test_ingress_class_overridable_via_env(self) -> None:
        os.environ["EDGE_INGRESS_CLASS"] = "traefik"
        try:
            plan = K8sIngressAdapter().compute_apply_plan(_baseline())
            ingress = next(s for s in plan.steps if s.kind == "ingress.apply").payload
            self.assertEqual(ingress["spec"]["ingressClassName"], "traefik")
        finally:
            del os.environ["EDGE_INGRESS_CLASS"]

    def test_no_ingress_when_exposure_disabled(self) -> None:
        cfg = _baseline()
        cfg.exposure.enabled = False
        plan = K8sIngressAdapter().compute_apply_plan(cfg)
        kinds = [s.kind for s in plan.steps]
        self.assertNotIn("ingress.apply", kinds)
        self.assertIn("ingress.delete", kinds)


class TestCertManagerAnnotations(unittest.TestCase):
    """R-8 territory: cert-manager annotations match the issuer kind."""

    def test_clusterissuer_annotation(self) -> None:
        cfg = _baseline()
        plan = K8sIngressAdapter().compute_apply_plan(cfg)
        ingress = next(s for s in plan.steps if s.kind == "ingress.apply").payload
        annotations = ingress["metadata"]["annotations"]
        self.assertEqual(
            annotations["cert-manager.io/cluster-issuer"], "letsencrypt-prod",
        )
        self.assertNotIn("cert-manager.io/issuer", annotations)

    def test_namespaced_issuer_annotation(self) -> None:
        cfg = _baseline()
        cfg.certs[0].cert_manager.issuer_kind = IssuerKind.ISSUER
        cfg.certs[0].cert_manager.issuer_name = "letsencrypt-staging"
        plan = K8sIngressAdapter().compute_apply_plan(cfg)
        ingress = next(s for s in plan.steps if s.kind == "ingress.apply").payload
        annotations = ingress["metadata"]["annotations"]
        self.assertEqual(annotations["cert-manager.io/issuer"], "letsencrypt-staging")
        self.assertNotIn("cert-manager.io/cluster-issuer", annotations)

    def test_tls_block_lists_every_hostname(self) -> None:
        cfg = _baseline()
        plan = K8sIngressAdapter().compute_apply_plan(cfg)
        ingress = next(s for s in plan.steps if s.kind == "ingress.apply").payload
        tls = ingress["spec"]["tls"]
        self.assertEqual(len(tls), 1)
        self.assertEqual(tls[0]["secretName"], "wildcard-tls")
        self.assertIn("jf.iomio.io", tls[0]["hosts"])
        self.assertIn("auth.iomio.io", tls[0]["hosts"])

    def test_no_certs_no_tls_block(self) -> None:
        cfg = _baseline()
        cfg.certs = []
        for h in cfg.hosts:
            h.tls = None
        plan = K8sIngressAdapter().compute_apply_plan(cfg)
        ingress = next(s for s in plan.steps if s.kind == "ingress.apply").payload
        self.assertNotIn("tls", ingress["spec"])


class TestDns01CertificateEmission(unittest.TestCase):
    """For DNS-01 with a non-manual provider, the adapter emits a
    standalone Certificate resource so the operator can wire the
    secret_ref. HTTP-01 flows let the annotation drive everything."""

    def test_dns01_cert_emitted_with_secret_ref(self) -> None:
        cfg = _baseline()
        cfg.certs[0].cert_manager.challenge = AcmeChallenge.DNS01
        cfg.certs[0].cert_manager.solver = CertManagerSolver(
            provider="cloudflare", secret_ref="cf-token",
        )
        plan = K8sIngressAdapter().compute_apply_plan(cfg)
        cert_step = next(s for s in plan.steps if s.kind == "cert.apply")
        self.assertEqual(cert_step.payload["kind"], "Certificate")
        self.assertEqual(cert_step.payload["spec"]["secretName"], "wildcard-tls")
        self.assertEqual(
            cert_step.payload["spec"]["issuerRef"]["name"], "letsencrypt-prod",
        )
        self.assertIn("*.iomio.io", cert_step.payload["spec"]["dnsNames"])

    def test_http01_does_not_emit_explicit_certificate(self) -> None:
        # HTTP-01 challenge: cert-manager handles everything via the
        # Ingress annotation; no explicit Certificate resource needed.
        cfg = _baseline()
        plan = K8sIngressAdapter().compute_apply_plan(cfg)
        kinds = [s.kind for s in plan.steps]
        self.assertNotIn("cert.apply", kinds)


class TestApplyPlanWarnings(unittest.TestCase):
    def test_exposed_but_no_hostnames_warns(self) -> None:
        cfg = RoutingConfigV2(
            gateway_host="",
            exposure=ExposureConfig(enabled=True, binding=Binding.K8S_INGRESS),
        )
        plan = K8sIngressAdapter().compute_apply_plan(cfg)
        self.assertTrue(plan.warnings)
        self.assertIn("hostnames", plan.warnings[0])


class TestAdapterDetection(unittest.TestCase):
    def test_detect_active_picks_first_matching(self) -> None:
        os.environ["KUBERNETES_SERVICE_HOST"] = "10.0.0.1"
        try:
            active = detect_active_adapter([K8sIngressAdapter()])
            self.assertIsInstance(active, K8sIngressAdapter)
        finally:
            del os.environ["KUBERNETES_SERVICE_HOST"]

    def test_detect_active_returns_none_if_no_match(self) -> None:
        os.environ.pop("KUBERNETES_SERVICE_HOST", None)
        active = detect_active_adapter([K8sIngressAdapter()])
        self.assertIsNone(active)


if __name__ == "__main__":
    unittest.main()
