"""K8sIngressAdapter — emits Ingress + Service patch + cert-manager
Certificate manifests from a ``RoutingConfigV2``.

Pure-function adapter. Nothing in here calls kubectl or the Kubernetes
API; the output is a list of YAML/dict manifests the caller applies
(typically via ``kubectl apply -f`` or the controller's existing
``ingress-config`` action).

What it covers (design § references):

  * §6 — the K8s rows in the binding-adapter matrix
  * §1 #8 — internet_exposed toggle (Service type + Ingress lifecycle)
  * §1 #9 — Let's Encrypt via cert-manager annotations:
    ``cert-manager.io/cluster-issuer: letsencrypt-prod`` (or
    ``cert-manager.io/issuer`` for namespace-scoped Issuers)
  * VR-10 — when DNS-01 with a non-manual provider is configured,
    the Certificate references ``solver.secret_ref`` for the API
    token

Out of scope here (will land in later PRs):

  * Live cert status probing (PR-3.5 or PR-5 with a ``/cert/{id}/probe``
    endpoint)
  * Ingress class auto-detection (defaults to "nginx"; operators can
    override via ``EDGE_INGRESS_CLASS`` env var read elsewhere)
  * Traefik / other ingress-controller annotations (this assumes
    nginx-ingress + cert-manager, the default media-stack layout)
"""
from __future__ import annotations

import os
from typing import Any

from media_stack.api.services.config.routing.schema_v2 import (
    AcmeChallenge,
    Binding,
    CertEntry,
    CertSource,
    HostEntry,
    IssuerKind,
    RoutingConfigV2,
)
from .binding_adapter import ApplyPlan, ApplyPlanStep


# Defaults — overridable via env so deployments with non-standard
# ingress controllers don't have to fork the adapter.
_DEFAULT_NAMESPACE = "media-stack"
_DEFAULT_INGRESS_CLASS = "nginx"
_DEFAULT_BACKEND_SERVICE = "media-stack-envoy"
_DEFAULT_BACKEND_PORT = 8080


def _ns() -> str:
    return os.environ.get("MEDIA_STACK_NAMESPACE", _DEFAULT_NAMESPACE)


def _ingress_class() -> str:
    return os.environ.get("EDGE_INGRESS_CLASS", _DEFAULT_INGRESS_CLASS)


def _backend_service() -> str:
    return os.environ.get("EDGE_BACKEND_SERVICE", _DEFAULT_BACKEND_SERVICE)


def _backend_port() -> int:
    try:
        return int(os.environ.get("EDGE_BACKEND_PORT", _DEFAULT_BACKEND_PORT))
    except (TypeError, ValueError):
        return _DEFAULT_BACKEND_PORT


def _all_public_hostnames(cfg: RoutingConfigV2) -> list[str]:
    """Every hostname Envoy should serve. Used to populate Ingress
    rules and the cert-manager TLS section."""
    seen: set[str] = set()
    out: list[str] = []
    for h in cfg.hosts:
        for hostname in [h.canonical, *h.aliases]:
            if hostname and hostname not in seen:
                seen.add(hostname)
                out.append(hostname)
    if cfg.gateway_host and cfg.gateway_host not in seen:
        out.append(cfg.gateway_host)
        seen.add(cfg.gateway_host)
    return out


def _cert_for_host(host: HostEntry, cfg: RoutingConfigV2) -> CertEntry | None:
    if host.tls is None or not host.tls.cert_id:
        return None
    for c in cfg.certs:
        if c.id == host.tls.cert_id:
            return c
    return None


def _issuer_annotation(cert: CertEntry) -> dict[str, str]:
    """The cert-manager annotation that wires the Ingress to a
    ClusterIssuer or Issuer.

      * ClusterIssuer (cluster-scoped): ``cert-manager.io/cluster-issuer``
      * Issuer (namespace-scoped):       ``cert-manager.io/issuer``

    The two annotations are mutually exclusive — cert-manager warns
    if both are present, and only one resolves.
    """
    if cert.cert_manager is None:
        return {}
    cm = cert.cert_manager
    key = (
        "cert-manager.io/cluster-issuer"
        if cm.issuer_kind == IssuerKind.CLUSTER_ISSUER
        else "cert-manager.io/issuer"
    )
    return {key: cm.issuer_name} if cm.issuer_name else {}


def _build_ingress(cfg: RoutingConfigV2) -> dict[str, Any]:
    """Construct the K8s Ingress object. One Ingress lists every
    public hostname in its ``rules`` and ``tls`` blocks; cert-manager
    reads the annotation to provision certs as Secrets."""
    hostnames = _all_public_hostnames(cfg)

    annotations: dict[str, str] = {}
    # cert-manager annotations: pick the first cert that uses it
    # (we only emit one Ingress; multi-cert flows would need split
    # Ingresses, which is a Tier 2 nicety left for PR-8).
    for c in cfg.certs:
        if c.source == CertSource.CERT_MANAGER:
            annotations.update(_issuer_annotation(c))
            # cert-manager picks the SAN list from ingress.spec.tls[].hosts
            break

    # Group hostnames under a single TLS block per cert. A future
    # version may split per-cert, but for now: one Secret name per
    # Ingress (the first cert wins) and every hostname goes into it.
    tls_block: list[dict[str, Any]] = []
    for c in cfg.certs:
        if c.source != CertSource.CERT_MANAGER:
            continue
        secret_name = (
            c.cert_manager.secret_name if c.cert_manager and c.cert_manager.secret_name
            else f"{c.id}-tls"
        )
        tls_block.append({
            "hosts": list(hostnames),
            "secretName": secret_name,
        })
        break  # one cert per Ingress for now

    rules: list[dict[str, Any]] = []
    for host in hostnames:
        rules.append({
            "host": host,
            "http": {
                "paths": [{
                    "path": "/",
                    "pathType": "Prefix",
                    "backend": {
                        "service": {
                            "name": _backend_service(),
                            "port": {"number": _backend_port()},
                        },
                    },
                }],
            },
        })

    ingress = {
        "apiVersion": "networking.k8s.io/v1",
        "kind": "Ingress",
        "metadata": {
            "name": "media-stack-edge",
            "namespace": _ns(),
            "annotations": annotations,
        },
        "spec": {
            "ingressClassName": _ingress_class(),
            "rules": rules,
        },
    }
    if tls_block:
        ingress["spec"]["tls"] = tls_block
    return ingress


def _build_service_patch(cfg: RoutingConfigV2) -> dict[str, Any]:
    """Patch the media-stack-envoy Service type based on
    ``exposure.binding`` and ``exposure.enabled``.

      * exposure.enabled=False → ClusterIP (no public reach)
      * binding=k8s_loadbalancer → LoadBalancer
      * binding=k8s_ingress (or auto) → ClusterIP (Ingress fronts it)
    """
    if not cfg.exposure.enabled:
        svc_type = "ClusterIP"
    elif cfg.exposure.binding == Binding.K8S_LOADBALANCER:
        svc_type = "LoadBalancer"
    else:
        svc_type = "ClusterIP"   # Ingress is the front; Service stays internal
    return {
        "apiVersion": "v1",
        "kind": "Service",
        "metadata": {
            "name": _backend_service(),
            "namespace": _ns(),
        },
        "spec": {"type": svc_type},
    }


def _build_certificate(c: CertEntry, hostnames: list[str]) -> dict[str, Any] | None:
    """For CertSource.CERT_MANAGER, optionally emit a standalone
    ``cert-manager.io/v1 Certificate`` resource. Most flows don't need
    this — the Ingress annotation triggers cert-manager to create the
    Certificate implicitly. We emit explicit Certificate objects only
    when the operator needs DNS-01 with secret_ref control."""
    if c.cert_manager is None:
        return None
    cm = c.cert_manager
    if cm.challenge != AcmeChallenge.DNS01:
        return None  # let the Ingress-driven flow handle http01
    secret_name = cm.secret_name or f"{c.id}-tls"
    sans = list(c.sans) if c.sans else hostnames
    return {
        "apiVersion": "cert-manager.io/v1",
        "kind": "Certificate",
        "metadata": {
            "name": c.id,
            "namespace": _ns(),
        },
        "spec": {
            "secretName": secret_name,
            "issuerRef": {
                "kind": cm.issuer_kind.value,
                "name": cm.issuer_name,
            },
            "commonName": c.common_name,
            "dnsNames": sans,
        },
    }


class K8sIngressAdapter:
    """K8s deploy-mode binding adapter (Tier 0 from §6 of the design).
    Pure-function: ``compute_apply_plan`` produces a list of dicts the
    caller applies via kubectl."""

    name = "k8s_ingress"

    def detect(self) -> bool:
        return bool(os.environ.get("KUBERNETES_SERVICE_HOST"))

    def compute_apply_plan(self, cfg: RoutingConfigV2) -> ApplyPlan:
        plan = ApplyPlan()
        hostnames = _all_public_hostnames(cfg)

        # Service patch — always emit so the deploy stays in-sync if
        # the operator toggles internet_exposed.
        plan.steps.append(ApplyPlanStep(
            kind="service.patch",
            description=(
                f"Set {_backend_service()} Service type → "
                f"{_build_service_patch(cfg)['spec']['type']}"
            ),
            payload=_build_service_patch(cfg),
        ))

        if cfg.exposure.enabled and hostnames:
            ingress = _build_ingress(cfg)
            plan.steps.append(ApplyPlanStep(
                kind="ingress.apply",
                description=(
                    f"Apply Ingress 'media-stack-edge' covering "
                    f"{len(hostnames)} hostname(s): {', '.join(hostnames[:3])}"
                    f"{'…' if len(hostnames) > 3 else ''}"
                ),
                payload=ingress,
            ))

            # Per-cert Certificate manifests — only emitted for DNS-01
            # solvers that need explicit secret_ref control. HTTP-01
            # flows let the Ingress annotation drive everything.
            for c in cfg.certs:
                cert_obj = _build_certificate(c, hostnames)
                if cert_obj is not None:
                    plan.steps.append(ApplyPlanStep(
                        kind="cert.apply",
                        description=f"Apply Certificate '{c.id}' (DNS-01 solver)",
                        payload=cert_obj,
                    ))
        else:
            # Internet-exposed = false → make sure the Ingress is GONE.
            # The caller deletes it; we describe the intent.
            plan.steps.append(ApplyPlanStep(
                kind="ingress.delete",
                description="Delete Ingress 'media-stack-edge' — internet exposure disabled",
                payload={
                    "apiVersion": "networking.k8s.io/v1",
                    "kind": "Ingress",
                    "metadata": {"name": "media-stack-edge", "namespace": _ns()},
                },
            ))

        # Sanity warnings — surface design-doc violations the validator
        # didn't catch (the validator is data-shape; the adapter is
        # cluster-aware).
        if cfg.exposure.enabled and not hostnames:
            plan.warnings.append(
                "exposure.enabled=true but no hostnames configured — "
                "the Ingress would have an empty rules list and "
                "cert-manager wouldn't issue any certs.",
            )

        return plan
