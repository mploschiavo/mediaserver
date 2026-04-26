"""Envoy route_config emitter for ``RoutingConfigV2``.

This is a *pure* function: ``RoutingConfigV2`` in, ``dict`` out (the
shape Envoy expects under
``static_resources.listeners[*].filter_chains[*].filters[*].typed_config.route_config``).
No I/O, no global state, no template loading.

It runs alongside the legacy v1 emitter in
``services/edge/envoy_config_generator.py``; PR-3 will wire one or the
other into the live envoy.yaml writer based on which schema the
controller persisted.

Design § references (the spec this implements):

* §1   — 17 scenarios this generator must handle
* §2   — RoutingConfigV2 schema (consumed here as input)
* §5   — example Envoy YAML this emitter produces

Routing precedence within a virtual_host (Envoy evaluates routes
top-down, first match wins). The generator emits routes in this order:

    1. path_aliases on the gateway host (most specific prefix match)
    2. host[].path_prefix service routes (specific paths)
    3. host[].canonical service routes ("/" prefix on subdomain hosts)
    4. apex route (exact path "/" match)
    5. catch_all route ("/" prefix, last resort)

Aliases for hostnames are emitted as **separate** virtual_hosts that
redirect to the canonical, so the operator's URL bar shows the
canonical name (the alias is a synonym, not a primary). Implements
§1 scenarios 1-7 + Let's Encrypt-friendly route_config (cert binding
is a separate concern for the listener's filter_chain, handled in
PR-3).
"""

from __future__ import annotations

from typing import Any, Iterable

from media_stack.api.services.config.routing.schema_v2 import (
    ApexAction,
    CatchAllAction,
    PathAlias,
    RoutingConfigV2,
)


# Cluster name format mirrors the legacy generator
# (``services/edge/envoy_config_generator.py``) — Envoy clusters are
# named ``service_<id>`` so ratchet tests can assert without coupling
# to the cluster builder.
_CLUSTER_PREFIX = "service_"


def _cluster_name(service_id: str) -> str:
    return f"{_CLUSTER_PREFIX}{service_id}"


def _redirect_route_for_path_alias(p: PathAlias) -> dict[str, Any]:
    """Emit an Envoy route that redirects ``from_path`` →
    ``to_path`` with the configured response code (301/302/308).

    Uses ``path_separated_prefix`` + ``prefix_rewrite`` so the suffix
    is preserved: ``/app/jellyfin/movies/123`` redirects to
    ``/app/jf/movies/123`` rather than bare ``/app/jf`` (which is what
    a naive ``path_redirect`` would emit).

    ``path_separated_prefix`` (Envoy ≥1.23) matches only at path
    component boundaries — so ``/app/jellyfin`` matches ``/app/jellyfin``
    and ``/app/jellyfin/...`` but NOT ``/app/jellyfinx``. This avoids
    a false-positive when two services share a prefix root.
    """
    return {
        "match": {"path_separated_prefix": p.from_path},
        "redirect": {
            "prefix_rewrite": p.to_path,
            "response_code": _envoy_redirect_code(p.code),
        },
    }


def _redirect_route_to_canonical(canonical: str) -> dict[str, Any]:
    """Emit a 'redirect everything to canonical host' route — used in
    alias virtual_hosts so visitors of an alias hostname end up on the
    canonical URL."""
    return {
        "match": {"prefix": "/"},
        "redirect": {
            "host_redirect": canonical,
            "response_code": "MOVED_PERMANENTLY",
        },
    }


def _service_route(prefix: str, service_id: str) -> dict[str, Any]:
    """Forward a path prefix to the service's upstream cluster."""
    route: dict[str, Any] = {
        "match": {"prefix": prefix},
        "route": {"cluster": _cluster_name(service_id)},
    }
    return route


def _apex_route(cfg: RoutingConfigV2) -> dict[str, Any] | None:
    """Build the apex route (exact "/" match) or None if action=NONE."""
    apex = cfg.apex
    if apex.action == ApexAction.NONE:
        return None
    if apex.action == ApexAction.REDIRECT:
        return {
            "match": {"path": "/"},
            "redirect": {
                "path_redirect": apex.target,
                "response_code": _envoy_redirect_code(apex.code),
            },
        }
    if apex.action == ApexAction.SERVICE:
        return {
            "match": {"path": "/"},
            "route": {"cluster": _cluster_name(apex.target)},
        }
    if apex.action == ApexAction.STATIC:
        # Envoy serves static responses via the
        # `direct_response` route action. Body lives in
        # `direct_response.body.inline_string`. The 200 default is
        # right for "static landing page"; operators can override
        # with apex.code if they want a different status.
        return {
            "match": {"path": "/"},
            "direct_response": {
                "status": apex.code if apex.code != 302 else 200,
                "body": {"inline_string": apex.target},
            },
        }
    return None  # unreachable; kept for future enum members


def _catch_all_route(cfg: RoutingConfigV2) -> dict[str, Any] | None:
    """Final fallthrough route, evaluated only if nothing earlier
    matched. ``NOT_FOUND`` is the default; explicit redirects/services/
    blocks override it."""
    catch = cfg.catch_all
    if catch.action == CatchAllAction.NOT_FOUND:
        # Envoy returns 404 by default when no route matches, so the
        # explicit catch-all is only useful when the operator wants a
        # custom body. Skip emission otherwise — keeps the route_config
        # tight.
        if not catch.custom_404_body:
            return None
        return {
            "match": {"prefix": "/"},
            "direct_response": {
                "status": 404,
                "body": {"inline_string": catch.custom_404_body},
            },
        }
    if catch.action == CatchAllAction.REDIRECT:
        return {
            "match": {"prefix": "/"},
            "redirect": {
                "path_redirect": catch.target,
                "response_code": _envoy_redirect_code(catch.code),
            },
        }
    if catch.action == CatchAllAction.SERVICE:
        return {
            "match": {"prefix": "/"},
            "route": {"cluster": _cluster_name(catch.target)},
        }
    if catch.action == CatchAllAction.BLOCK:
        # 444 is nginx's "no response — close connection" code; Envoy
        # doesn't have a dedicated equivalent, but `direct_response`
        # with status 444 is the closest signal. Operators using
        # block-mode are typically chaining a firewall rule on top.
        return {
            "match": {"prefix": "/"},
            "direct_response": {"status": 444},
        }
    return None  # unreachable


def _envoy_redirect_code(http_code: int) -> str:
    """Map an HTTP redirect code to the Envoy enum string."""
    return {
        301: "MOVED_PERMANENTLY",
        302: "FOUND",
        303: "SEE_OTHER",
        307: "TEMPORARY_REDIRECT",
        308: "PERMANENT_REDIRECT",
    }.get(int(http_code), "FOUND")


def _split_hosts_by_role(cfg: RoutingConfigV2) -> tuple[list, list]:
    """Bucket host entries by whether they own the gateway host or
    not. The gateway host vhost is special — it carries the apex,
    catch-all, and path_aliases routes. Subdomain hosts are simpler
    (one prefix → one cluster)."""
    gateway_hosts: list = []
    other_hosts: list = []
    gw = cfg.gateway_host
    for h in cfg.hosts:
        if h.canonical == gw or gw in h.aliases:
            gateway_hosts.append(h)
        else:
            other_hosts.append(h)
    return gateway_hosts, other_hosts


def _build_subdomain_vhost(host) -> dict[str, Any]:
    """A subdomain host gets a vhost matching just its canonical and
    a single forward route."""
    routes = [_service_route("/", host.service_id)]
    return {
        "name": f"vh_{host.role or host.service_id}_{host.canonical}",
        "domains": [host.canonical],
        "routes": routes,
    }


def _build_alias_redirect_vhost(host) -> dict[str, Any] | None:
    """If the host has aliases, emit a separate vhost matching them
    and redirecting to the canonical."""
    if not host.aliases:
        return None
    return {
        "name": f"vh_{host.role or host.service_id}_aliases",
        "domains": list(host.aliases),
        "routes": [_redirect_route_to_canonical(host.canonical)],
    }


def _build_gateway_vhost(
    cfg: RoutingConfigV2,
    gateway_hosts: Iterable,
    other_hosts: Iterable,
) -> dict[str, Any] | None:
    """Build the virtual_host for the gateway hostname.

    The gateway host carries:

      * path_aliases (highest priority)
      * one path_prefix route per gateway-host service (e.g.
        m.iomio.io/apps/ → homepage)
      * one path_prefix route per *other* host that has a
        ``path_prefix`` set (path-strategy surfacing)
      * apex
      * catch_all

    Returns None if there's no gateway hostname at all (purely
    subdomain-routed configs).
    """
    gw = cfg.gateway_host
    if not gw:
        return None

    routes: list[dict[str, Any]] = []

    # 1. path_aliases — most specific match first (alphabetical-by-from
    # for stable diffs).
    for p in sorted(cfg.path_aliases, key=lambda x: x.from_path):
        if p.from_path and p.to_path:
            routes.append(_redirect_route_for_path_alias(p))

    # 2. path_prefix routes — every host with a path_prefix gets
    # surfaced under the gateway. Subdomain-only hosts (no path_prefix)
    # are reached via their own vhosts; we don't double-route them.
    pp_hosts = [h for h in cfg.hosts if h.path_prefix]
    pp_hosts.sort(key=lambda h: h.path_prefix or "")
    for h in pp_hosts:
        prefix = h.path_prefix
        if not prefix.endswith("/"):
            prefix = prefix + "/"
        routes.append(_service_route(prefix, h.service_id))

    # 3. Service routes for hosts whose canonical IS the gateway host
    # but with no path_prefix (e.g. "m.iomio.io" → homepage at root).
    # Only emit when no other route has already claimed "/".
    for h in gateway_hosts:
        if not h.path_prefix:
            # Skip if apex already redirects "/", which evaluates first
            # via exact-path matching anyway.
            routes.append(_service_route("/", h.service_id))

    # 4. Apex route (exact "/" match — Envoy evaluates exact-path
    # before prefix, so this naturally beats the catch-all).
    apex = _apex_route(cfg)
    if apex is not None:
        routes.append(apex)

    # 5. Catch-all (prefix "/", last resort).
    ca = _catch_all_route(cfg)
    if ca is not None:
        routes.append(ca)

    # Mark the parameter as intentionally unused if no hosts present
    # (kept for symmetry; future versions may surface stub redirects).
    _ = other_hosts

    if not routes:
        return None

    return {
        "name": f"vh_gateway_{gw}",
        "domains": [gw],
        "routes": routes,
    }


def generate_route_config_v2(cfg: RoutingConfigV2) -> dict[str, Any]:
    """Build the full Envoy ``route_config`` from a v2 config.

    Output shape::

        {
          "name": "main",
          "virtual_hosts": [
            {"name": ..., "domains": [...], "routes": [...]},
            ...
          ]
        }

    The result is deterministic — vhosts are sorted by canonical
    domain so a config that hasn't changed produces byte-identical
    output. This is what makes the byte-stable ratchet (R-5) safe.
    """
    gateway_hosts, other_hosts = _split_hosts_by_role(cfg)

    vhosts: list[dict[str, Any]] = []

    # Gateway vhost (path_aliases + apex + catch_all + path-prefixed
    # service routes live here).
    gw_vhost = _build_gateway_vhost(cfg, gateway_hosts, other_hosts)
    if gw_vhost is not None:
        vhosts.append(gw_vhost)

    # Subdomain hosts that are not the gateway — one vhost per
    # canonical, plus an alias-redirect vhost when aliases exist.
    for h in sorted(other_hosts, key=lambda x: x.canonical):
        vhosts.append(_build_subdomain_vhost(h))
        alias_vhost = _build_alias_redirect_vhost(h)
        if alias_vhost is not None:
            vhosts.append(alias_vhost)

    return {
        "name": "main",
        "virtual_hosts": vhosts,
    }
