"""Ratchet R-8: cert-manager annotations on emitted Ingress objects
match the configured ``issuer_kind`` (Issuer vs ClusterIssuer).

cert-manager has two annotations and they are mutually exclusive:

  * ``cert-manager.io/cluster-issuer``  → references a ClusterIssuer
  * ``cert-manager.io/issuer``          → references a (namespaced) Issuer

Both present = cert-manager warns and only one resolves; missing
either when source=cert_manager = no certs ever issue. The ratchet
locks the K8sIngressAdapter to emit exactly one annotation matching
the ``IssuerKind`` enum.
"""
from __future__ import annotations

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
    CertSource,
    ExposureConfig,
    HostEntry,
    HostTls,
    IssuerKind,
    RoutingConfigV2,
)
from media_stack.services.edge.k8s_ingress_adapter import (  # noqa: E402
    K8sIngressAdapter,
)


def _cfg_with_issuer_kind(kind: IssuerKind, name: str = "letsencrypt-prod") -> RoutingConfigV2:
    cfg = RoutingConfigV2(
        gateway_host="m.iomio.io",
        exposure=ExposureConfig(enabled=True, binding=Binding.K8S_INGRESS),
    )
    cfg.hosts.append(HostEntry(role="r", service_id="s", canonical="x.iomio.io",
                                tls=HostTls(cert_id="c1")))
    cfg.certs.append(CertEntry(
        id="c1", source=CertSource.CERT_MANAGER,
        common_name="x.iomio.io",
        cert_manager=CertManagerConfig(
            issuer_kind=kind, issuer_name=name,
            challenge=AcmeChallenge.HTTP01,
        ),
    ))
    return cfg


class CertManagerAnnotationRatchet(unittest.TestCase):
    def test_clusterissuer_emits_only_cluster_issuer_annotation(self) -> None:
        plan = K8sIngressAdapter().compute_apply_plan(
            _cfg_with_issuer_kind(IssuerKind.CLUSTER_ISSUER),
        )
        ingress = next(s for s in plan.steps if s.kind == "ingress.apply").payload
        annotations = ingress["metadata"]["annotations"]
        self.assertIn("cert-manager.io/cluster-issuer", annotations)
        self.assertNotIn("cert-manager.io/issuer", annotations,
                          "Both annotations present — cert-manager would warn.")

    def test_issuer_emits_only_namespaced_issuer_annotation(self) -> None:
        plan = K8sIngressAdapter().compute_apply_plan(
            _cfg_with_issuer_kind(IssuerKind.ISSUER),
        )
        ingress = next(s for s in plan.steps if s.kind == "ingress.apply").payload
        annotations = ingress["metadata"]["annotations"]
        self.assertIn("cert-manager.io/issuer", annotations)
        self.assertNotIn("cert-manager.io/cluster-issuer", annotations,
                          "Both annotations present — cert-manager would warn.")

    def test_uploaded_cert_emits_no_cert_manager_annotation(self) -> None:
        cfg = RoutingConfigV2(
            gateway_host="m.iomio.io",
            exposure=ExposureConfig(enabled=True, binding=Binding.K8S_INGRESS),
        )
        cfg.hosts.append(HostEntry(role="r", service_id="s", canonical="x.iomio.io"))
        cfg.certs.append(CertEntry(
            id="manual", source=CertSource.UPLOADED, common_name="x.iomio.io",
        ))
        plan = K8sIngressAdapter().compute_apply_plan(cfg)
        ingress = next(s for s in plan.steps if s.kind == "ingress.apply").payload
        annotations = ingress["metadata"].get("annotations", {})
        for k in annotations:
            self.assertFalse(
                k.startswith("cert-manager.io/"),
                f"UPLOADED cert source should not produce a cert-manager "
                f"annotation, but found {k!r}.",
            )


if __name__ == "__main__":
    unittest.main()
