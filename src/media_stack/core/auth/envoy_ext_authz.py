"""Envoy ext_authz filter + cluster generation for gateway auth.

Generates the Envoy configuration fragments needed to wire ext_authz:
1. HTTP filter (inserted before envoy.filters.http.router)
2. Cluster pointing at the auth provider (Authelia/Authentik)
3. Per-route config to disable ext_authz for native/public services
"""

from __future__ import annotations

from typing import Any

from media_stack.core.auth.gateway_policy import ExtAuthzConfig, GatewayAuthPolicy


# Envoy ext_authz filter name — used in per_filter_config references
EXT_AUTHZ_FILTER_NAME = "envoy.filters.http.ext_authz"


def build_ext_authz_filter(
    ext_authz: ExtAuthzConfig,
    auth_portal_url: str = "",
) -> dict[str, Any]:
    """Build the Envoy ext_authz HTTP filter configuration.

    This filter is inserted into the HTTP filter chain BEFORE the router filter.
    It sends each request to the auth provider for verification before forwarding.
    """
    path_prefix = ext_authz.path_prefix
    # For Authelia /api/verify, add rd= parameter so unauthenticated
    # requests get 302 redirect instead of bare 401.
    if auth_portal_url and "/api/verify" in path_prefix:
        from urllib.parse import quote
        separator = "&" if "?" in path_prefix else "?"
        path_prefix = f"{path_prefix}{separator}rd={quote(auth_portal_url, safe=':/')}"

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

    # Merge auth header injection into the EXISTING base Lua filter.
    # Multiple Lua filters break envoy_on_response callbacks, so all
    # Lua code must live in a single filter. The base template Lua
    # (filter[0]) already has envoy_on_request and envoy_on_response.
    # We prepend auth header code to envoy_on_request.
    base_lua = http_filters[0] if http_filters and http_filters[0].get("name") == "envoy.filters.http.lua" else None
    if base_lua and "inline_code" in base_lua.get("typed_config", {}):
        old_code = base_lua["typed_config"]["inline_code"]
        # Inject auth header setup at the START of envoy_on_request
        auth_request_code = (
            '  -- [AUTH] Set forwarded headers for ext_authz\n'
            '  handle:headers():replace("x-forwarded-host", handle:headers():get(":authority") or "")\n'
            '  handle:headers():replace("x-forwarded-uri", handle:headers():get(":path") or "/")\n'
            '  handle:headers():replace("x-forwarded-proto", "https")\n'
            '  handle:headers():replace("x-forwarded-method", handle:headers():get(":method") or "GET")\n'
        )
        # Insert after "function envoy_on_request(handle)" line
        old_code = old_code.replace(
            'function envoy_on_request(handle)\n',
            'function envoy_on_request(handle)\n' + auth_request_code,
            1,
        )
        base_lua["typed_config"]["inline_code"] = old_code

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
