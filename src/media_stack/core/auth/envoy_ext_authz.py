"""Envoy ext_authz filter + cluster generation for gateway auth.

Generates the Envoy configuration fragments needed to wire ext_authz:
1. HTTP filter (inserted before envoy.filters.http.router)
2. Cluster pointing at the auth provider (Authelia/Authentik)
3. Per-route config to disable ext_authz for native/public services
"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote as _quote

from media_stack.core.auth.gateway_policy import ExtAuthzConfig, GatewayAuthPolicy


# Envoy ext_authz filter name — used in per_filter_config references
EXT_AUTHZ_FILTER_NAME = "envoy.filters.http.ext_authz"


def _prefix_with_rd(raw_prefix: str, auth_portal_url: str) -> str:
    """If the contract's path_prefix is the authz-path form AND a portal
    URL is known, inject ``rd=<encoded portal>&`` right after the ``?``.

    For non-authz-path prefixes (e.g. Authentik's /outpost.goauthentik.io
    path), return unchanged — they don't use an `rd` param."""
    if not raw_prefix or "authz_path=" not in raw_prefix or not auth_portal_url:
        return raw_prefix
    if "rd=" in raw_prefix:
        return raw_prefix  # already parameterized (profile override)
    head, sep, tail = raw_prefix.partition("?")
    if not sep:
        return raw_prefix
    rd_param = "rd=" + _quote(auth_portal_url, safe="")
    return f"{head}?{rd_param}&{tail}"


def build_ext_authz_filter(
    ext_authz: ExtAuthzConfig,
    auth_portal_url: str = "",
) -> dict[str, Any]:
    """Build the Envoy ext_authz HTTP filter configuration.

    Inserted into the HTTP filter chain BEFORE the router filter. Each
    request is sent to the auth provider for verification.

    Path prefix assembly (for Authelia's /api/verify):
      The contract provides ``path_prefix`` ending in ``authz_path=``.
      Envoy appends the user's request path verbatim after the prefix.
      To get Authelia to emit a 302 with Location:<portal>?rd=<original>
      on unauthenticated requests, we inject ``rd=<encoded portal>&``
      BEFORE ``authz_path=``. Order matters: ``authz_path`` must stay
      last so the appended path lands in a harmless query parameter
      and doesn't corrupt ``rd``.
    """
    path_prefix = _prefix_with_rd(ext_authz.path_prefix, auth_portal_url)

    return {
        "name": EXT_AUTHZ_FILTER_NAME,
        "typed_config": {
            "@type": "type.googleapis.com/envoy.extensions.filters.http.ext_authz.v3.ExtAuthz",
            "transport_api_version": "V3",
            "http_service": {
                "server_uri": {
                    "uri": f"http://{ext_authz.host}:{ext_authz.port}",
                    "cluster": ext_authz.cluster_name,
                    "timeout": "5s",
                },
                "path_prefix": path_prefix,
                "authorization_request": {
                    "allowed_headers": {
                        "patterns": [
                            {"exact": "accept", "ignore_case": True},
                            {"exact": "cookie", "ignore_case": True},
                            {"exact": "authorization", "ignore_case": True},
                            {"exact": "proxy-authorization", "ignore_case": True},
                            {"prefix": "x-", "ignore_case": True},
                        ],
                    },
                    # Authelia /api/verify computes the post-login
                    # `rd` redirect from the URL it sees on the auth
                    # check. It does NOT read X-Forwarded-Uri on this
                    # legacy endpoint — only X-Original-URL overrides
                    # the request path. Without X-Original-URL it
                    # falls back to the request URI Envoy sent it
                    # (the path_prefix `/api/verify?rd=...&authz_path=
                    # /app/...`), producing a malformed rd that loops
                    # back through verify forever. Confirmed in
                    # production: Authelia logs show
                    #   "Access to https://m.iomio.io/api/verify?rd=
                    #    ...&authz_path=/api/health"
                    # even when X-Forwarded-Uri=/api/health is set.
                    #
                    # X-Forwarded-{Method,Proto,Host} are still set —
                    # Authelia uses Host for cookie-scope checks and
                    # Method for the verify-vs-redirect decision, and
                    # they're cheap. X-Original-URL is the load-bearing
                    # one for the rd path.
                    # NOTE: this field takes envoy.config.core.v3.HeaderValue
                    # (bare {key, value}), NOT HeaderValueOption (the
                    # {header: {...}, append_action: ...} shape used
                    # by route-level request_headers_to_add). Mixing
                    # them up was the cause of v1.0.188's load-time
                    # crash: "no such field: 'header' has unknown
                    # fields" — fail-closed Envoy refuses to start.
                    "headers_to_add": [
                        {"key": "X-Original-URL",
                         "value": "%REQ(X-FORWARDED-PROTO)%://%REQ(:AUTHORITY)%%REQ(:PATH)%"},
                        {"key": "X-Forwarded-Method",
                         "value": "%REQ(:METHOD)%"},
                        {"key": "X-Forwarded-Proto",
                         "value": "%REQ(X-FORWARDED-PROTO)%"},
                        {"key": "X-Forwarded-Host",
                         "value": "%REQ(:AUTHORITY)%"},
                        {"key": "X-Forwarded-Uri",
                         "value": "%REQ(:PATH)%"},
                    ],
                },
                "authorization_response": {
                    "allowed_upstream_headers": {
                        "patterns": [
                            {"exact": h, "ignore_case": True}
                            for h in ext_authz.response_headers_to_add
                        ],
                    },
                    # Headers sent to the client on auth denial (302/401).
                    # Location is critical for the login redirect to work.
                    "allowed_client_headers": {
                        "patterns": [
                            {"exact": "location", "ignore_case": True},
                            {"exact": "set-cookie", "ignore_case": True},
                            {"exact": "www-authenticate", "ignore_case": True},
                        ],
                    },
                },
            },
            # Fail closed: deny requests when auth provider returns non-200.
            # Authelia returns 302 redirect for unauthenticated users, which
            # ext_authz treats as denial and forwards the 302 to the browser.
            "failure_mode_allow": False,
        },
    }


def build_ext_authz_cluster(ext_authz: ExtAuthzConfig) -> dict[str, Any]:
    """Build the Envoy cluster definition for the auth provider."""
    return {
        "name": ext_authz.cluster_name,
        "connect_timeout": "5s",
        "type": "STRICT_DNS",
        "lb_policy": "ROUND_ROBIN",
        "load_assignment": {
            "cluster_name": ext_authz.cluster_name,
            "endpoints": [
                {
                    "lb_endpoints": [
                        {
                            "endpoint": {
                                "address": {
                                    "socket_address": {
                                        "address": ext_authz.host,
                                        "port_value": ext_authz.port,
                                    },
                                },
                            },
                        },
                    ],
                },
            ],
        },
    }


def route_ext_authz_disabled_config() -> dict[str, Any]:
    """Per-route config that disables ext_authz for native/public services.

    Add this to a route's `typed_per_filter_config` to bypass auth.
    """
    return {
        EXT_AUTHZ_FILTER_NAME: {
            "@type": "type.googleapis.com/envoy.extensions.filters.http.ext_authz.v3.ExtAuthzPerRoute",
            "disabled": True,
        },
    }


def inject_ext_authz_into_payload(
    payload: dict[str, Any],
    policy: GatewayAuthPolicy,
    auth_portal_url: str = "",
) -> None:
    """Inject ext_authz filter and cluster into an Envoy config payload.

    Modifies the payload in-place:
    1. Adds ext_authz HTTP filter before the router filter
    2. Adds the auth provider cluster to static_resources.clusters
    """
    if not policy.ext_authz:
        return

    # 1. Inject filter before router
    static_resources = payload.get("static_resources", {})
    listeners = static_resources.get("listeners", [])
    if not listeners:
        return

    filter_chains = listeners[0].get("filter_chains", [])
    if not filter_chains:
        return

    filters = filter_chains[0].get("filters", [])
    if not filters:
        return

    hcm = filters[0].get("typed_config", {})
    http_filters = hcm.get("http_filters", [])

    # Find the router filter position and insert ext_authz before it
    router_idx = None
    for i, f in enumerate(http_filters):
        if f.get("name") == "envoy.filters.http.router":
            router_idx = i
            break

    # The X-Forwarded-* headers Authelia /api/verify needs are added
    # via Envoy's native authorization_request.headers_to_add (see
    # build_ext_authz_filter). Earlier revisions tried to splice
    # those replace() calls into the base template's
    # envoy_on_request, but the template defines only
    # envoy_on_response — str.replace silently no-op'd, Authelia got
    # no X-Forwarded-Uri, and the post-login rd looped back through
    # /api/verify instead of landing on the user's target.
    ext_authz_filter = build_ext_authz_filter(policy.ext_authz, auth_portal_url)
    if router_idx is not None:
        http_filters.insert(router_idx, ext_authz_filter)
    else:
        http_filters.append(ext_authz_filter)

    # 2. Add auth cluster
    clusters = static_resources.get("clusters", [])
    # Don't duplicate
    existing_names = {c.get("name") for c in clusters if isinstance(c, dict)}
    auth_cluster = build_ext_authz_cluster(policy.ext_authz)
    if auth_cluster["name"] not in existing_names:
        clusters.append(auth_cluster)
    static_resources["clusters"] = clusters


def apply_per_route_auth_policy(
    route: dict[str, Any],
    service_name: str,
    policy: GatewayAuthPolicy,
) -> None:
    """Apply per-route ext_authz bypass for native/public services.

    Modifies the route dict in-place by adding typed_per_filter_config
    if the service should bypass ext_authz.
    """
    if not policy.ext_authz:
        return

    svc_policy = policy.service_policies.get(service_name, "protected")
    if svc_policy in ("native", "public"):
        route_cfg = route.get("route") or route.get("redirect")
        if route_cfg is None:
            return
        per_filter = route.setdefault("typed_per_filter_config", {})
        per_filter.update(route_ext_authz_disabled_config())
