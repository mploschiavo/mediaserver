"""Virtual host assembly for Envoy dynamic config."""

from __future__ import annotations

import copy
import os
from typing import Any

from media_stack.core.platforms.compose.edge.providers.envoy.helpers import _virtual_host_name



class EnvoyVirtualHostService:
    @staticmethod
    def _extra_domain_aliases(host: str) -> list[str]:
        """Derive extra domain aliases for a direct-host vhost.
    
        If the runtime routing uses a different domain suffix than the profile
        (e.g. profile has .local but runtime has .my), add the runtime suffix
        as an alias so both domains reach the service.
        """
        extras: list[str] = []
        try:
            from media_stack.api.services import config as _cfg_svc
            routing = _cfg_svc.get_routing()
            gw_host = str(routing.get("gateway_host", ""))
            if not gw_host:
                return extras
            # Extract the domain suffix from gateway_host (e.g. comp.my -> my)
            gw_parts = gw_host.split(".")
            if len(gw_parts) < 2:
                return extras
            gw_suffix = ".".join(gw_parts[1:])  # e.g. "my" or "media-stack.my"
            # Extract service slug from the host (first segment before first dot)
            host_parts = host.split(".")
            svc_slug = host_parts[0]
            host_suffix = ".".join(host_parts[1:]) if len(host_parts) > 1 else ""
            if host_suffix and host_suffix != gw_suffix:
                # Build alias: svc_slug.gw_suffix (e.g. controller.media-stack.my)
                alias = f"{svc_slug}.{gw_suffix}"
                if alias != host:
                    extras.append(alias)
                    extras.append(f"{alias}:*")
                # Also try with intermediate subdomain from gateway
                # e.g. media-stack-controller.media-stack.my from
                # media-stack-controller.media-stack.local
                mid_parts = host_parts[1:-1]  # middle segments
                if mid_parts:
                    mid_alias = f"{svc_slug}.{'.'.join(mid_parts)}.{gw_suffix}"
                    if mid_alias != host and mid_alias != alias:
                        extras.append(mid_alias)
                        extras.append(f"{mid_alias}:*")
        except Exception:
            pass
        return extras
    
    
    def build_virtual_hosts(self, 
        routes_by_host: dict[str, list[tuple[int, dict[str, Any]]]],
    ) -> tuple[list[dict[str, Any]], int]:
        """Assemble sorted virtual host list from ranked routes, returning (vhosts, route_count).
    
        Also appends a localhost catch-all vhost mirroring the gateway host.
        """
        virtual_hosts: list[dict[str, Any]] = []
        route_count = 0
        # Pre-populate with all primary hostnames to prevent aliases from
        # colliding with other vhosts' primary domains.
        all_domains: set[str] = set()
        for host in routes_by_host:
            all_domains.add(host)
            all_domains.add(f"{host}:*")
        for host in sorted(routes_by_host.keys()):
            ranked_routes = sorted(routes_by_host[host], key=lambda item: item[0], reverse=True)
            host_routes = [route for _, route in ranked_routes]
            route_count += len(host_routes)
            domains = [host, f"{host}:*"]
            # Add runtime domain aliases (e.g. .my suffix when profile uses .local)
            # but skip any already claimed by another vhost — Envoy requires unique domains.
            for alias in _extra_domain_aliases(host):
                if alias not in all_domains:
                    domains.append(alias)
            all_domains.update(domains)
            virtual_hosts.append(
                {
                    "name": _virtual_host_name(host),
                    "domains": domains,
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


_instance = EnvoyVirtualHostService()
build_virtual_hosts = _instance.build_virtual_hosts
_extra_domain_aliases = _instance._extra_domain_aliases
