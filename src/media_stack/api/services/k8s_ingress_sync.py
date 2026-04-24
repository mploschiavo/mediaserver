"""Reconcile the K8s Ingress resource from runtime routing config.

Why this exists
---------------
Before this module the K8s Ingress (``media-stack-ingress``) was
operator-edited at install time and never updated by the controller.
A user changing routing in the dashboard saw the dashboard "save"
succeed, envoy.yaml regenerate (after v1.0.160 wired up the
routing-overrides merge), but the Ingress kept routing only the
hostnames the operator originally typed into ``k8s/ingress*.yaml``.

Result: a user typing ``apps.example.com`` into the Routing tab on
K8s would have:
  - dashboard shows the new gateway host                   ✓
  - envoy.yaml has a vhost for ``apps.example.com``        ✓
  - K8s Ingress still routes only the old host             ✗

So requests to ``https://apps.example.com/`` never reached envoy in
the first place — the Ingress controller 404'd before envoy ever
saw the request. Compose users sidestep this entirely because
compose has no Ingress layer; envoy IS the edge.

What the reconcile does
-----------------------
Builds the desired ``spec.rules`` + ``spec.tls.hosts`` of the
``media-stack-ingress`` Ingress from:

  - Runtime routing config (``gateway_host``, ``stack_subdomain``,
    ``base_domain``, ``direct_hosts``, ``strategy``).
  - Service registry (one subdomain per enabled service when
    strategy is ``subdomain`` or ``hybrid``).
  - The legacy ``.local`` fallback set, kept for LAN access without
    public DNS.

Then PATCHes the Ingress via the K8s API. No-op outside K8s
(``K8S_NAMESPACE`` env empty → returns ``{"applied": false}``).

This is the K8s-only counterpart to the compose envoy-config flow.
The dashboard's POST /api/routing now triggers BOTH:
  - envoy-config (already wired)
  - ingress-config (this module — new in v1.0.162)
"""

from __future__ import annotations

import logging
import os
from typing import Any

_log = logging.getLogger("media_stack.k8s_ingress_sync")

# The Ingress object name is fixed across all deploys — keep one
# Ingress per namespace because most Ingress controllers route on
# Host first, and merging from multiple objects is a frequent source
# of "rule was applied to the wrong backend" bugs.
_INGRESS_NAME = "media-stack-ingress"

# Hosts the Ingress controller needs to TLS-terminate. Using one
# secret keeps the cert-manager / manual-cert flow simple. Override
# with the ``INGRESS_TLS_SECRET`` env var when the operator has a
# different secret name from a prior install.
_DEFAULT_TLS_SECRET = "iomio-tls"

# LAN-only hostnames that should ALWAYS resolve through envoy. Useful
# when the operator hasn't set up public DNS yet — they can still
# reach apps via /etc/hosts entries for ``.local`` names.
_LOCAL_FALLBACK_HOSTS_PER_SVC = (
    "homepage", "jellyfin", "jellyseerr",
    "sonarr", "radarr", "lidarr", "readarr",
    "bazarr", "prowlarr", "qbittorrent", "sabnzbd",
    "maintainerr", "tautulli", "authelia", "authentik",
)


def _routing_from_runtime() -> dict[str, Any]:
    """Read the merged routing config (profile + overrides) the same
    way generate_envoy_config_main reads it. Single source of truth
    so envoy-config and ingress-config can't disagree on what the
    user asked for."""
    from media_stack.api.services.config import get_routing
    return dict(get_routing() or {})


def _enabled_web_ui_services() -> list[tuple[str, int]]:
    """List of (service_id, internal_port) for every service with a
    web UI. We only emit ingress rules for these — backend-only
    services (e.g. flaresolverr) don't need an external hostname."""
    from media_stack.api.services.registry import get_web_ui_services
    out: list[tuple[str, int]] = []
    for svc in get_web_ui_services():
        port = int(svc.published_port or svc.port or 0)
        if port:
            out.append((svc.id, port))
    return out


def _build_rules(routing: dict, services: list[tuple[str, int]]) -> tuple[list[dict], list[str]]:
    """Build the ingress rules + the deduped list of TLS hosts.

    Returns:
        (rules, tls_hosts) — both empty when routing config is unset.

    TLS-host scope rule (added v1.0.163 after Let's Encrypt rate-limit
    near-miss): only OPERATOR-DECLARED hostnames go on the TLS list,
    because cert-manager auto-creates a Certificate from
    ``spec.tls.hosts`` and tries to issue a Let's Encrypt cert for
    each. Putting per-service subdomains there means cert-manager
    asks LE to validate ``sonarr.example.com`` etc., which fails (no
    DNS), spawns one solver pod per host, and burns through the LE
    rate-limit. Operator-declared = ``gateway_host`` + every value in
    ``direct_hosts``. Per-service subdomains stay as INGRESS RULES
    (so envoy receives the request if DNS happens to point there)
    but skip TLS — works over HTTP, or the operator can add their
    own multi-SAN cert.
    """
    rules: list[dict] = []
    tls_hosts: list[str] = []
    seen: set[str] = set()

    def _add_rule(host: str, svc_name: str, port: int, request_tls: bool = True) -> None:
        host = host.strip().lower()
        if not host or host in seen:
            return
        seen.add(host)
        rules.append({
            "host": host,
            "http": {
                "paths": [{
                    "path": "/",
                    "pathType": "Prefix",
                    "backend": {
                        "service": {"name": svc_name, "port": {"number": port}},
                    },
                }],
            },
        })
        if request_tls:
            tls_hosts.append(host)

    strategy = str(routing.get("strategy") or "subdomain").lower().strip()
    gateway_host = str(routing.get("gateway_host") or "").strip().lower()
    stack_subdomain = str(routing.get("stack_subdomain") or "").strip().lower()
    base_domain = str(routing.get("base_domain") or "").strip().lower()
    direct_hosts = routing.get("direct_hosts") or {}

    # Gateway host — routes to envoy:80 (envoy then dispatches by
    # path-prefix or subdomain inside its own vhost config). Required
    # for path-prefix and hybrid strategies.
    if gateway_host and strategy in ("hybrid", "path-prefix", "subdomain"):
        _add_rule(gateway_host, "envoy", 80)

    # Per-service subdomains — only when strategy includes subdomain
    # routing AND the operator has provided enough info to build the
    # FQDN (need both stack_subdomain and base_domain, otherwise we'd
    # silently emit ``<svc>.local`` and stomp the LAN fallback set).
    if strategy in ("subdomain", "hybrid") and stack_subdomain and base_domain:
        for svc_id, _port in services:
            host = f"{svc_id}.{stack_subdomain}.{base_domain}"
            # Subdomain rules go to envoy too — envoy routes by Host
            # header to the right backend. Bypassing envoy here would
            # skip auth/middleware policies.
            #
            # request_tls=False: per-service subdomains are NOT
            # automatically added to the TLS hosts list. Most
            # operators don't have public DNS for every per-service
            # subdomain (they pick a small set, like
            # m./auth./jf.). Putting them in TLS triggers
            # cert-manager to ask Let's Encrypt to validate hosts
            # that don't resolve, hitting the LE rate limit and
            # spawning a solver pod per failed host. (v1.0.163.)
            _add_rule(host, "envoy", 80, request_tls=False)

    # Direct hosts — bypass envoy for specific services (typically the
    # media server, where TV/mobile clients prefer a simple host
    # without path-prefix gymnastics). Maps each direct_host to the
    # named service if the service is in the enabled set.
    svc_port_map = dict(services)
    for role, host in (direct_hosts or {}).items():
        host_str = str(host or "").strip().lower()
        if not host_str:
            continue
        # ``role`` keys typical today:
        #   media_server → resolves via technology_bindings to
        #                  jellyfin / plex / emby.
        #   auth         → resolves to authelia (we map manually
        #                  because there's no technology_bindings
        #                  entry for the auth provider — auth.provider
        #                  in profile YAML names it).
        #
        # Anything else is tried as a literal service id, so
        # ``direct_hosts: {sonarr: srn.example.com}`` works without
        # a binding indirection.
        # Sensible role → service-id defaults so direct_hosts works
        # even when no profile YAML is mounted (the K8s common case
        # before the user has saved any profile-level config).
        # technology_bindings overrides when present.
        _ROLE_DEFAULTS = {"media_server": "jellyfin", "auth": "authelia"}
        try:
            from media_stack.api.services.config._profile import ProfileService
            _profile_svc = ProfileService()
            bindings = _profile_svc.technology_bindings() or {}
            profile_data = _profile_svc.load()[0] or {}
        except Exception:
            bindings = {}
            profile_data = {}
        if role == "auth":
            svc_id = (profile_data.get("auth") or {}).get("provider") or _ROLE_DEFAULTS["auth"]
        else:
            svc_id = bindings.get(role) or _ROLE_DEFAULTS.get(role) or role
        port = svc_port_map.get(svc_id)
        if port:
            _add_rule(host_str, svc_id, port)

    # LAN fallback: <svc>.local → envoy. Always emitted so an operator
    # without DNS can use /etc/hosts to reach apps. These don't go on
    # the TLS hosts list because LAN access is plain HTTP via the
    # default ingress; the operator can opt in by adding them.
    for svc_id in _LOCAL_FALLBACK_HOSTS_PER_SVC:
        if any(s == svc_id for s, _ in services):
            host = f"{svc_id}.local"
            _add_rule(host, "envoy", 80, request_tls=False)

    return rules, tls_hosts


def _patch_ingress(namespace: str, rules: list[dict], tls_hosts: list[str]) -> dict[str, Any]:
    """Apply the desired rules/TLS to the live Ingress via the K8s API."""
    try:
        from kubernetes import client as k8s_client, config as k8s_config
    except ImportError:
        return {"applied": False, "error": "kubernetes python client not installed"}
    try:
        try:
            k8s_config.load_incluster_config()
        except Exception:
            k8s_config.load_kube_config()
    except Exception as exc:
        return {"applied": False, "error": f"k8s config: {exc}"}

    networking = k8s_client.NetworkingV1Api()

    # Collect the existing TLS secret name so we don't overwrite a
    # cert-manager-managed secret with our default just because the
    # operator never told us. Falls back to the env var or default.
    tls_secret = os.environ.get("INGRESS_TLS_SECRET", "").strip()
    try:
        existing = networking.read_namespaced_ingress(_INGRESS_NAME, namespace)
        existing_tls = (existing.spec.tls or [])
        if existing_tls and existing_tls[0].secret_name and not tls_secret:
            tls_secret = existing_tls[0].secret_name
    except Exception:
        existing = None
    if not tls_secret:
        tls_secret = _DEFAULT_TLS_SECRET

    body: dict[str, Any] = {
        "spec": {
            "rules": rules,
            "tls": [{"hosts": tls_hosts, "secretName": tls_secret}] if tls_hosts else [],
        }
    }
    try:
        networking.patch_namespaced_ingress(
            name=_INGRESS_NAME,
            namespace=namespace,
            body=body,
        )
    except Exception as exc:
        return {"applied": False, "error": f"patch failed: {exc}"}
    return {
        "applied": True,
        "rules_count": len(rules),
        "tls_hosts_count": len(tls_hosts),
        "tls_secret": tls_secret,
    }


def reconcile() -> dict[str, Any]:
    """Public entry point — read routing config, build rules,
    PATCH the Ingress. Safe to call repeatedly (idempotent: if the
    rules already match, the PATCH is a no-op at the K8s API level).

    On K8s, this is the FIRST-HOP control plane: without rules in the
    Ingress, external traffic never reaches Envoy in the first place.
    That means "reconcile produced nothing" is not a silent skip — it
    is a hard failure of the clean-deploy invariant. This function
    ``raise``s rather than returning ``skipped`` when routing looks
    incomplete on a K8s cluster; bootstrap surfaces the exception in
    the dashboard instead of quietly proceeding to a broken state.

    Exception: when the controller is NOT running on K8s
    (``K8S_NAMESPACE`` unset), this is a genuine no-op — compose has
    no Ingress layer to reconcile and the caller is just invoking the
    job unconditionally from the DAG. Returning a skip is correct
    there and matches ``ingress-config``'s contract phase 21 semantics.
    """
    namespace = os.environ.get("K8S_NAMESPACE", "").strip()
    if not namespace:
        return {
            "applied": False,
            "skipped": True,
            "reason": "not running on K8s (K8S_NAMESPACE unset)",
        }
    # From here on we ARE on K8s. Silent skip == broken cluster.
    routing = _routing_from_runtime()
    if not routing:
        raise RuntimeError(
            "ingress-config: routing config is empty on K8s. The "
            "controller couldn't read a gateway_host / base_domain "
            "from the merged profile + overrides view, so no Ingress "
            "rules can be built and every request will 404 at the "
            "Ingress layer. Check that the media-stack-controller-profile "
            "ConfigMap is present and the controller pod has mounted "
            "it at /profile/profile.yaml. (v1.0.169 failed-loud behaviour.)"
        )
    services = _enabled_web_ui_services()
    rules, tls_hosts = _build_rules(routing, services)
    if not rules:
        raise RuntimeError(
            f"ingress-config: routing config was non-empty (gateway_host="
            f"{routing.get('gateway_host')!r}, strategy="
            f"{routing.get('strategy')!r}) but produced zero Ingress "
            "rules. Likely causes: no enabled web-UI services in the "
            "registry, or a strategy/base_domain combination with no "
            "matching rule branch in _build_rules. External traffic "
            "won't reach Envoy; this is the clean-deploy invariant "
            "failing. (v1.0.169 failed-loud behaviour.)"
        )
    return _patch_ingress(namespace, rules, tls_hosts)
