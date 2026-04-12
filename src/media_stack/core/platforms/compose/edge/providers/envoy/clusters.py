"""Cluster generation for Envoy dynamic config."""

from __future__ import annotations

from typing import Any
from urllib import parse

from media_stack.core.platforms.compose.edge.providers.envoy.helpers import _cluster_name


class EnvoyClusterService:
    """Wraps Envoy cluster generation functions."""

    def build_cluster_entry(self, name: str, *, address: str, port: int) -> dict[str, Any]:
        """Build a single Envoy cluster entry."""
        return {
            "name": name, "type": "STRICT_DNS", "connect_timeout": "5s",
            "lb_policy": "ROUND_ROBIN",
            "load_assignment": {
                "cluster_name": name,
                "endpoints": [{"lb_endpoints": [{"endpoint": {"address": {
                    "socket_address": {"address": address, "port_value": int(port)}}}}]}],
            },
        }

    def build_clusters_from_service_map(self, service_map: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
        """Build Envoy cluster list from the Traefik-style service map."""
        clusters: list[dict[str, Any]] = []
        seen_clusters: set[str] = set()
        for service_name in sorted(service_map.keys()):
            service_cfg = service_map.get(service_name) or {}
            if not isinstance(service_cfg, dict):
                continue
            load_balancer = service_cfg.get("loadBalancer")
            if not isinstance(load_balancer, dict):
                continue
            servers = load_balancer.get("servers")
            if not isinstance(servers, list) or not servers:
                continue
            first_server = servers[0]
            if not isinstance(first_server, dict):
                continue
            target_url = str(first_server.get("url") or "").strip()
            if not target_url:
                continue
            parsed = parse.urlparse(target_url)
            address = str(parsed.hostname or "").strip()
            if not address:
                continue
            port = int(parsed.port or (443 if parsed.scheme == "https" else 80))
            name = _cluster_name(service_name)
            if name in seen_clusters:
                continue
            seen_clusters.add(name)
            clusters.append(self.build_cluster_entry(name, address=address, port=port))
        return clusters


_instance = EnvoyClusterService()
build_cluster_entry = _instance.build_cluster_entry
build_clusters_from_service_map = _instance.build_clusters_from_service_map
