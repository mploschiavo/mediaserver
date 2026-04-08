"""Virtual host assembly for Envoy dynamic config."""

from __future__ import annotations

import copy
from typing import Any

from media_stack.core.platforms.compose.edge.providers.envoy.helpers import _virtual_host_name


def build_virtual_hosts(
    routes_by_host: dict[str, list[tuple[int, dict[str, Any]]]],
) -> tuple[list[dict[str, Any]], int]:
    """Assemble sorted virtual host list from ranked routes, returning (vhosts, route_count).

    Also appends a localhost catch-all vhost mirroring the gateway host.
    """
    virtual_hosts: list[dict[str, Any]] = []
    route_count = 0
    for host in sorted(routes_by_host.keys()):
        ranked_routes = sorted(routes_by_host[host], key=lambda item: item[0], reverse=True)
        host_routes = [route for _, route in ranked_routes]
        route_count += len(host_routes)
        virtual_hosts.append(
            {
                "name": _virtual_host_name(host),
                "domains": [host, f"{host}:*"],
                "routes": host_routes,
            }
        )

    # Add a localhost catch-all vhost that mirrors the gateway host routes.
    # This lets compose users access services at localhost:80/app/sonarr
    # without DNS setup. Find the gateway vhost (has /app/ routes) and clone it.
    gateway_vhost = None
    for vh in virtual_hosts:
        if any(
            r.get("match", {}).get("prefix", "").startswith("/app/")
            for r in vh.get("routes", [])
        ):
            gateway_vhost = vh
            break
    if gateway_vhost:
        localhost_vhost = copy.deepcopy(gateway_vhost)
        localhost_vhost["name"] = "vhost_localhost"
        localhost_vhost["domains"] = ["localhost", "localhost:*", "127.0.0.1", "127.0.0.1:*"]
        virtual_hosts.append(localhost_vhost)

        # Add a wildcard catch-all vhost so any hostname (e.g. comp.my,
        # custom DNS, IP address) can reach the gateway routes.  This must
        # come LAST — Envoy evaluates vhosts in order and the first match
        # with the longest domain suffix wins, but "*" only matches when
        # nothing else does.
        catchall_vhost = copy.deepcopy(gateway_vhost)
        catchall_vhost["name"] = "vhost_catchall"
        catchall_vhost["domains"] = ["*"]
        virtual_hosts.append(catchall_vhost)

    if not virtual_hosts:
        virtual_hosts = [
            {
                "name": "vhost_default",
                "domains": ["*"],
                "routes": [
                    {
                        "match": {"prefix": "/"},
                        "direct_response": {"status": 404},
                    }
                ],
            }
        ]

    return virtual_hosts, route_count
