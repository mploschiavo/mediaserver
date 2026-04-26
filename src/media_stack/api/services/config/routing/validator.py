"""Routing v2 validator.

Enforces the rules in the design doc against a ``RoutingConfigV2``.
Returns a list of ``ValidationError`` so the UI can mark each offending
field. Empty list means the config is safe to apply.

Validation rules (numbered to match the design doc + §3 + §1
extensions):

    VR-1   No two hosts share the same ``canonical``.
    VR-2   A ``canonical`` cannot appear in another host's ``aliases``.
    VR-3   Every ``service_id`` must exist in the service registry
           (caller passes the allowed set; validator stays pure).
    VR-4   Every ``path_aliases[*].from`` and ``.to`` must start with
           ``/``.
    VR-5   ``apex.target`` resolves to a real path or service_id when
           ``apex.action == redirect`` or ``service``.
    VR-6   ``catch_all.action: redirect|service`` requires ``target``.
    VR-7   ``tls.cert_id`` must reference a cert in ``certs[]``.
    VR-8   When ``hosts[].auth.gate == required`` the controller would
           need a working ext_authz cluster. We validate the structure
           (provider non-empty); the live probe lives in PR-3.
    VR-9   ``path_aliases[*].from`` must not shadow an existing
           ``hosts[*].path_prefix``.
    VR-10  ``cert.cert_manager.solver.provider == 'cloudflare'`` (or
           any non-manual DNS provider) requires a non-empty
           ``solver.secret_ref``. (Live secret existence is checked at
           apply-time by the K8s adapter.)
    VR-11  ``cert.source == 'acme_direct'`` is only valid when the
           runtime ``deploy_mode`` is ``compose``. Pass
           ``deploy_mode='k8s'`` to enforce.

Each rule emits at most one ``ValidationError`` per offending field.
The error carries a structured ``field`` path (e.g.
``hosts[2].canonical``) plus a human-readable ``message`` and a
``hint`` for the UI to render.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Literal

from .schema_v2 import (
    ApexAction,
    AuthGate,
    CatchAllAction,
    CertSource,
    RoutingConfigV2,
)


DeployMode = Literal["k8s", "compose", "auto"]


@dataclass(frozen=True)
class ValidationError:
    code: str
    field: str
    message: str
    hint: str = ""


def _is_path(value: str) -> bool:
    return isinstance(value, str) and value.startswith("/")


def validate_routing_config(
    cfg: RoutingConfigV2,
    *,
    known_service_ids: Iterable[str] = (),
    deploy_mode: DeployMode = "auto",
) -> list[ValidationError]:
    """Run all validation rules against ``cfg``. Returns a sorted list
    by ``(field, code)`` so the UI can group errors per-field
    deterministically.

    Args:
        cfg: the parsed v2 config.
        known_service_ids: set/iter of valid service registry ids.
            Empty disables VR-3 (handy in unit tests where you want to
            validate other rules without wiring the registry).
        deploy_mode: ``k8s`` enables VR-11 enforcement; ``compose``
            disables it; ``auto`` skips (defer to apply-time).
    """
    errors: list[ValidationError] = []
    known_set = set(known_service_ids)
    cert_ids = {c.id for c in cfg.certs if c.id}

    # ---- VR-1 / VR-2: canonical uniqueness + alias non-overlap ----
    canonical_counts: dict[str, list[int]] = {}
    for i, h in enumerate(cfg.hosts):
        if h.canonical:
            canonical_counts.setdefault(h.canonical, []).append(i)
    for canonical, indices in canonical_counts.items():
        if len(indices) > 1:
            for idx in indices[1:]:
                errors.append(ValidationError(
                    code="VR-1",
                    field=f"hosts[{idx}].canonical",
                    message=f"Hostname '{canonical}' is already used by hosts[{indices[0]}].",
                    hint="Each hostname must point to one service. Did you mean to add it as an alias instead?",
                ))

    canonical_set = {h.canonical for h in cfg.hosts if h.canonical}
    for i, h in enumerate(cfg.hosts):
        for j, alias in enumerate(h.aliases):
            if alias and alias in canonical_set and alias != h.canonical:
                errors.append(ValidationError(
                    code="VR-2",
                    field=f"hosts[{i}].aliases[{j}]",
                    message=f"Alias '{alias}' is already the canonical name of another host.",
                    hint="A hostname can be canonical OR an alias, never both.",
                ))

    # ---- VR-3: service_id exists in registry ----
    if known_set:
        for i, h in enumerate(cfg.hosts):
            if h.service_id and h.service_id not in known_set:
                errors.append(ValidationError(
                    code="VR-3",
                    field=f"hosts[{i}].service_id",
                    message=f"Unknown service '{h.service_id}'.",
                    hint="Pick a service from the registry — typos here mean the route lands at no upstream.",
                ))

    # ---- VR-4: path_aliases entries are paths ----
    for i, p in enumerate(cfg.path_aliases):
        if not _is_path(p.from_path):
            errors.append(ValidationError(
                code="VR-4",
                field=f"path_aliases[{i}].from",
                message=f"'{p.from_path}' must start with '/'.",
                hint="Path aliases are HTTP path-prefix matches; they always begin with a slash.",
            ))
        if not _is_path(p.to_path):
            errors.append(ValidationError(
                code="VR-4",
                field=f"path_aliases[{i}].to",
                message=f"'{p.to_path}' must start with '/'.",
                hint="The redirect target is a path on this hostname.",
            ))

    # ---- VR-5: apex target shape ----
    apex = cfg.apex
    if apex.action == ApexAction.REDIRECT:
        if not apex.target:
            errors.append(ValidationError(
                code="VR-5",
                field="apex.target",
                message="Apex redirect requires a target path.",
                hint="e.g. '/apps' to send the bare hostname to your dashboard.",
            ))
        elif not _is_path(apex.target):
            errors.append(ValidationError(
                code="VR-5",
                field="apex.target",
                message=f"Apex redirect target '{apex.target}' must start with '/'.",
                hint="The target is a path on the same hostname.",
            ))
    elif apex.action == ApexAction.SERVICE:
        if not apex.target:
            errors.append(ValidationError(
                code="VR-5",
                field="apex.target",
                message="Apex 'service' action requires a target service_id.",
                hint="Pick the service the bare hostname should route to.",
            ))
        elif known_set and apex.target not in known_set:
            errors.append(ValidationError(
                code="VR-5",
                field="apex.target",
                message=f"Apex target service '{apex.target}' is not in the registry.",
                hint="Pick a service from the registry.",
            ))

    # ---- VR-6: catch_all target shape ----
    catch_all = cfg.catch_all
    if catch_all.action == CatchAllAction.REDIRECT:
        if not catch_all.target:
            errors.append(ValidationError(
                code="VR-6",
                field="catch_all.target",
                message="Catch-all redirect requires a target path.",
                hint="Where should requests to unknown URLs go? e.g. '/apps'.",
            ))
        elif not _is_path(catch_all.target):
            errors.append(ValidationError(
                code="VR-6",
                field="catch_all.target",
                message=f"Catch-all redirect target '{catch_all.target}' must start with '/'.",
                hint="The target is a path on the same hostname.",
            ))
    elif catch_all.action == CatchAllAction.SERVICE:
        if not catch_all.target:
            errors.append(ValidationError(
                code="VR-6",
                field="catch_all.target",
                message="Catch-all 'service' action requires a target service_id.",
                hint="Pick the service unknown URLs should route to.",
            ))
        elif known_set and catch_all.target not in known_set:
            errors.append(ValidationError(
                code="VR-6",
                field="catch_all.target",
                message=f"Catch-all target service '{catch_all.target}' is not in the registry.",
                hint="Pick a service from the registry.",
            ))

    # ---- VR-7: tls.cert_id reference ----
    for i, h in enumerate(cfg.hosts):
        if h.tls and h.tls.cert_id and h.tls.cert_id not in cert_ids:
            errors.append(ValidationError(
                code="VR-7",
                field=f"hosts[{i}].tls.cert_id",
                message=f"Cert id '{h.tls.cert_id}' is not defined in certs[].",
                hint="Add the cert under 'TLS certs' or pick an existing one.",
            ))

    # ---- VR-8: auth.required needs a provider ----
    for i, h in enumerate(cfg.hosts):
        if h.auth and h.auth.gate == AuthGate.REQUIRED and not h.auth.provider:
            errors.append(ValidationError(
                code="VR-8",
                field=f"hosts[{i}].auth.provider",
                message="Required auth gate must name a provider.",
                hint="e.g. 'authelia'. Without a provider Envoy can't ext_authz.",
            ))

    # ---- VR-9: path_aliases.from must not shadow hosts[].path_prefix ----
    host_path_prefixes: set[str] = {
        h.path_prefix.rstrip("/")
        for h in cfg.hosts
        if h.path_prefix
    }
    for i, p in enumerate(cfg.path_aliases):
        if not p.from_path:
            continue
        if p.from_path.rstrip("/") in host_path_prefixes:
            errors.append(ValidationError(
                code="VR-9",
                field=f"path_aliases[{i}].from",
                message=f"Alias '{p.from_path}' shadows a host path_prefix.",
                hint="The alias would prevent the host from ever receiving traffic. Rename the alias or remove the host's path_prefix.",
            ))

    # ---- VR-10: dns01 cert-manager solver needs secret_ref ----
    for i, c in enumerate(cfg.certs):
        if c.source != CertSource.CERT_MANAGER or c.cert_manager is None:
            continue
        cm = c.cert_manager
        if cm.challenge.value == "dns01" and cm.solver.provider != "manual":
            if not cm.solver.secret_ref:
                errors.append(ValidationError(
                    code="VR-10",
                    field=f"certs[{i}].cert_manager.solver.secret_ref",
                    message=(
                        f"DNS-01 with provider '{cm.solver.provider}' needs "
                        "a secret_ref for the API token."
                    ),
                    hint="Create a K8s Secret with the provider API token and reference it here.",
                ))

    # ---- VR-11: acme_direct only on compose ----
    if deploy_mode == "k8s":
        for i, c in enumerate(cfg.certs):
            if c.source == CertSource.ACME_DIRECT:
                errors.append(ValidationError(
                    code="VR-11",
                    field=f"certs[{i}].source",
                    message="acme_direct certs are not supported on Kubernetes.",
                    hint="Use 'cert_manager' with a ClusterIssuer instead — cert-manager handles renewals and storage natively.",
                ))

    errors.sort(key=lambda e: (e.field, e.code))
    return errors
