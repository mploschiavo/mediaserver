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
"""

from __future__ import annotations

import logging
import os
import socket
from typing import Any

_log = logging.getLogger("media_stack.dns_check")


def _resolve_host(host: str) -> str | None:
    try:
        return socket.gethostbyname(host)
    except (socket.gaierror, socket.herror):
        return None
    except Exception as exc:
        _log.debug("DNS resolution failed for %s: %s", host, exc)
        return None


def _is_usable(ip: str) -> bool:
    """Filter out loopback + link-local addresses that would always
    render as 'doesn't match' since public DNS never points there."""
    return bool(ip) and not ip.startswith(("127.", "169.254."))


def _cluster_ips() -> list[str]:
    """All IPs that could legitimately serve traffic for this cluster.

    The match check (``matches_cluster``) returns true if the
    resolved hostname IP matches ANY of these — because a single
    cluster can be reached via multiple IPs:
      - LAN IP (the InternalIP, what /etc/hosts would map to)
      - Public IP (what an internet-exposed DNS record points to,
        usually the operator's home router after port-forward)
      - Cloud LB IP (when the cluster has a real LoadBalancer)

    Preference order — the FIRST usable address from each source is
    added; duplicates de-duped:

      1. ``CLUSTER_EXTERNAL_IP`` env — operator override.
      2. Public IP via echo service (api.ipify.org). For NAT'd home
         clusters this is the only way to know the IP DNS records
         point at — node addresses give you the LAN IP, ingress LB
         status doesn't populate without a real LB.
      3. K8s ingress LoadBalancer status (real cloud LB).
      4. K8s Node ExternalIP (cloud nodes).
      5. K8s Node InternalIP (bare-metal LAN).
      6. Local socket trick (controller-on-same-host fallback).
    """
    ips: list[str] = []

    def _maybe_add(ip: str) -> None:
        if _is_usable(ip) and ip not in ips:
            ips.append(ip)

    # Explicit override always goes first so the operator can pin it.
    explicit = os.environ.get("CLUSTER_EXTERNAL_IP", "").strip()
    if explicit:
        _maybe_add(explicit)

    # Public IP echo — short timeout because the dashboard polls
    # /api/dns-check on every keystroke. api.ipify.org is the canonical
    # provider; ifconfig.me as backup. Either returns "1.2.3.4\n".
    import urllib.request
    for url in ("https://api.ipify.org", "https://ifconfig.me/ip"):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "media-stack/dns-check"})
            with urllib.request.urlopen(req, timeout=3) as resp:
                pub = resp.read().decode("utf-8", errors="replace").strip()
                _maybe_add(pub)
                break
        except Exception as exc:
            _log.debug("public IP echo (%s) failed: %s", url, exc)

    # K8s sources (no-ops outside cluster).
    try:
        from kubernetes import client as k8s_client, config as k8s_config
        try:
            k8s_config.load_incluster_config()
        except Exception:
            k8s_config.load_kube_config()

        netv1 = k8s_client.NetworkingV1Api()
        namespace = os.environ.get("K8S_NAMESPACE", "media-stack")
        try:
            ing = netv1.read_namespaced_ingress("media-stack-ingress", namespace)
            lbs = (ing.status.load_balancer.ingress if ing.status and ing.status.load_balancer else None) or []
            for entry in lbs:
                ip = getattr(entry, "ip", None) or getattr(entry, "hostname", None)
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
        # kubernetes client not installed (compose deploys); skip.
        pass

    # Last resort — host's outbound IP.
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            local = sock.getsockname()[0]
            if local:
                _maybe_add(str(local))
    except Exception as exc:
        _log.debug("local IP discovery failed: %s", exc)

    return ips


def check(host: str) -> dict[str, Any]:
    """Resolve ``host`` and compare to the cluster's external IP.

    Response shape:
        {
          "host": "<input>",
          "resolves": bool,
          "resolved_ip": "1.2.3.4" | "",
          "cluster_ip": "1.2.3.4" | "",
          "matches_cluster": True | False | None,  # None when cluster_ip unknown
        }
    """
    host = (host or "").strip()
    if not host:
        return {"host": "", "resolves": False, "resolved_ip": "",
                "cluster_ip": "", "cluster_ips": [], "matches_cluster": None}
    resolved = _resolve_host(host)
    cluster_ips = _cluster_ips()
    primary = cluster_ips[0] if cluster_ips else ""
    if resolved is None:
        return {
            "host": host, "resolves": False, "resolved_ip": "",
            "cluster_ip": primary, "cluster_ips": cluster_ips,
            "matches_cluster": None,
        }
    matches: bool | None
    if cluster_ips:
        # Match against ANY usable cluster IP — the same cluster can
        # be reached via LAN IP (from inside the network) AND public
        # IP (from outside via NAT). Either is a legitimate "this is
        # my cluster" answer.
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
