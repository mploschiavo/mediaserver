"""DNS reachability check for the dashboard's Routing tab.

Why this exists
---------------
Before this endpoint, the Routing tab let users save any hostname.
Path-based URLs would silently break when the hostname didn't actually
resolve to the cluster's IP (typo in domain, missing DNS record, or
the domain pointing at a totally different server). The user would
discover the mistake later by clicking a service link and getting
``ERR_NAME_NOT_RESOLVED`` in the browser.

This endpoint resolves the typed hostname server-side and compares it
to what the controller thinks the cluster's external IP is. The
dashboard renders one of three states:

  - resolves AND matches cluster IP → green check, save with confidence
  - resolves but to a DIFFERENT IP   → amber warning, "lands on
                                       another machine"
  - doesn't resolve at all           → amber warning, "add DNS or
                                       /etc/hosts entry"

The endpoint is intentionally narrow: it does NOT make HTTP requests
to the typed host (which would be slow + might trigger third-party
servers). DNS-only.

ADR-0012: top-level FunctionDef count is held at 0 — every helper is
an instance method on ``DnsCheckService`` exposed via module-level
alias bound to the singleton ``_INSTANCE``.
"""

from __future__ import annotations

import logging
import os
import socket
from typing import Any

_log = logging.getLogger("media_stack.dns_check")

_PUBLIC_IP_ECHO_PROVIDERS: tuple[str, ...] = (
    "https://api.ipify.org", "https://ifconfig.me/ip",
)


class DnsCheckService:
    """DNS reachability + cluster-IP probing for the Routing tab."""

    def resolve_host(self, host: str) -> str | None:
        try:
            return socket.gethostbyname(host)
        except (socket.gaierror, socket.herror):
            return None
        except Exception as exc:
            _log.debug("DNS resolution failed for %s: %s", host, exc)
            return None

    def is_usable(self, ip: str) -> bool:
        return bool(ip) and not ip.startswith(("127.", "169.254."))

    def cluster_ips(self) -> list[str]:
        ips: list[str] = []

        def _maybe_add(ip: str) -> None:
            if self.is_usable(ip) and ip not in ips:
                ips.append(ip)

        explicit = os.environ.get("CLUSTER_EXTERNAL_IP", "").strip()
        if explicit:
            _maybe_add(explicit)

        import urllib.request
        for url in _PUBLIC_IP_ECHO_PROVIDERS:
            try:
                req = urllib.request.Request(
                    url, headers={"User-Agent": "media-stack/dns-check"},
                )
                with urllib.request.urlopen(req, timeout=3) as resp:
                    pub = resp.read().decode("utf-8", errors="replace").strip()
                    _maybe_add(pub)
                    break
            except Exception as exc:
                _log.debug("public IP echo (%s) failed: %s", url, exc)

        try:
            from kubernetes import client as k8s_client, config as k8s_config
            try:
                k8s_config.load_incluster_config()
            except Exception:
                k8s_config.load_kube_config()

            netv1 = k8s_client.NetworkingV1Api()
            namespace = os.environ.get("K8S_NAMESPACE", "media-stack")
            try:
                ing = netv1.read_namespaced_ingress(
                    "media-stack-ingress", namespace,
                )
                lbs = (
                    ing.status.load_balancer.ingress
                    if ing.status and ing.status.load_balancer else None
                ) or []
                for entry in lbs:
                    ip = (
                        getattr(entry, "ip", None)
                        or getattr(entry, "hostname", None)
                    )
                    if ip:
                        _maybe_add(str(ip))
            except Exception as exc:
                _log.debug("ingress status read failed: %s", exc)

            try:
                corev1 = k8s_client.CoreV1Api()
                nodes = corev1.list_node().items or []
                for typ in ("ExternalIP", "InternalIP"):
                    for node in nodes:
                        addrs = (node.status.addresses if node.status else []) or []
                        for addr in addrs:
                            if getattr(addr, "type", "") == typ and addr.address:
                                _maybe_add(str(addr.address))
            except Exception as exc:
                _log.debug("node address read failed: %s", exc)
        except ImportError:
            pass

        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                sock.connect(("8.8.8.8", 80))
                local = sock.getsockname()[0]
                if local:
                    _maybe_add(str(local))
        except Exception as exc:
            _log.debug("local IP discovery failed: %s", exc)

        return ips

    def routing_hostnames(self) -> list[str]:
        try:
            from media_stack.api.services import config as config_svc
            from media_stack.core.service_registry.registry import SERVICES
        except Exception:
            return []
        try:
            routing = config_svc.get_routing()
        except Exception as exc:
            _log.debug("get_routing failed in dns_check: %s", exc)
            return []
        base = str(routing.get("base_domain") or "").strip()
        sub = str(routing.get("stack_subdomain") or "").strip()
        gw = str(routing.get("gateway_host") or "").strip()
        seen: list[str] = []

        def _add(h: str) -> None:
            h = (h or "").strip()
            if h and h not in seen:
                seen.append(h)

        _add(gw)
        if base and sub:
            for svc in SERVICES:
                _add(f"{svc.id}.{sub}.{base}")
        direct_hosts = routing.get("direct_hosts") or {}
        if isinstance(direct_hosts, dict):
            for value in direct_hosts.values():
                if isinstance(value, str):
                    _add(value)
        return seen

    def check_all(self) -> dict[str, Any]:
        cluster_ips = self.cluster_ips()
        primary = cluster_ips[0] if cluster_ips else ""
        entries: list[dict[str, Any]] = []
        for host in self.routing_hostnames():
            resolved = self.resolve_host(host)
            if resolved is None:
                entries.append({
                    "hostname": host,
                    "host": host,
                    "resolved": [],
                    "ips": [],
                    "status": "missing",
                    "matches_cluster": None,
                    "cluster_ip": primary,
                    "error": "no DNS record",
                })
                continue
            if cluster_ips and resolved not in cluster_ips:
                status = "conflict"
            elif cluster_ips:
                status = "ok"
            else:
                status = "ok"
            entries.append({
                "hostname": host,
                "host": host,
                "resolved": [resolved],
                "ips": [resolved],
                "status": status,
                "resolved_ip": resolved,
                "cluster_ip": primary,
                "matches_cluster": (
                    (resolved in cluster_ips) if cluster_ips else None
                ),
                "error": "",
            })
        return {
            "entries": entries,
            "cluster_ip": primary,
            "cluster_ips": cluster_ips,
        }

    def check(self, host: str) -> dict[str, Any]:
        host = (host or "").strip()
        if not host:
            return {
                "host": "", "resolves": False, "resolved_ip": "",
                "cluster_ip": "", "cluster_ips": [], "matches_cluster": None,
            }
        resolved = self.resolve_host(host)
        cluster_ips = self.cluster_ips()
        primary = cluster_ips[0] if cluster_ips else ""
        if resolved is None:
            return {
                "host": host, "resolves": False, "resolved_ip": "",
                "cluster_ip": primary, "cluster_ips": cluster_ips,
                "matches_cluster": None,
            }
        matches: bool | None
        if cluster_ips:
            matches = resolved in cluster_ips
        else:
            matches = None
        return {
            "host": host,
            "resolves": True,
            "resolved_ip": resolved,
            "cluster_ip": primary,
            "cluster_ips": cluster_ips,
            "matches_cluster": matches,
        }


_INSTANCE = DnsCheckService()

_resolve_host = _INSTANCE.resolve_host
_is_usable = _INSTANCE.is_usable
_cluster_ips = _INSTANCE.cluster_ips
_routing_hostnames = _INSTANCE.routing_hostnames
check_all = _INSTANCE.check_all
check = _INSTANCE.check
