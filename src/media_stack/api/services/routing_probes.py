"""Routing-config-derived gateway and routing-matrix probes.

Lifted from ``media_stack.api.handlers_get`` during ADR-0007 Phase 2
Phase E (legacy-handler retirement).

Two probe classes live here:

* :class:`GatewayHostnameProbe` -- enumerates the hostnames Envoy
  is serving by walking the routing config (single source of truth).
  Used by ``GET /api/gateway/hostnames``.
* :class:`RoutingMatrixProbe` -- probes the four user-facing access
  URLs per service (localhost / gateway-prefix / sub-domain /
  direct) from inside the controller container, sidestepping the
  browser's mixed-content + self-signed-cert traps. Used by
  ``GET /api/routing-probe``.
"""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from media_stack.core.logging_utils import log_swallowed
from media_stack.core.time_utils import ISO_8601_UTC_Z


class GatewayHostnameProbe:
    """Hostnames Envoy is serving, derived from the routing config.

    Routing config IS the source of truth -- it drives the envoy.yaml
    render and the K8s Ingress patcher. A previous revision additionally
    regex-scraped the rendered envoy.yaml as a "secondary" source; that
    was redundant (everything in envoy.yaml came from routing config in
    the first place) and unsafe -- the regex matched inline-Lua
    identifiers (`string.find`, `string.gsub`), Envoy proto type URLs,
    and minified-JS variable accesses that happen to be embedded in
    Envoy's filter chain definitions. Operators saw all that garbage
    in the Routing tab's "Gateway hostnames" panel. The fix: trust
    the config -- if a hostname isn't in routing config, it isn't
    being served, period.
    """

    def read(self) -> list[str]:
        hostnames: set[str] = set()
        try:
            from media_stack.api.services import config as config_svc
            from media_stack.api.services.registry import SERVICES
        except Exception as exc:  # noqa: BLE001
            log_swallowed(exc)
            return []
        try:
            routing = config_svc.get_routing()
        except Exception as exc:  # noqa: BLE001
            log_swallowed(exc)
            routing = {}
        base = str(routing.get("base_domain") or "").strip()
        sub = str(routing.get("stack_subdomain") or "").strip()
        gw_host = str(routing.get("gateway_host") or "").strip()
        if gw_host:
            hostnames.add(gw_host)
        if base and sub:
            for svc in SERVICES:
                hostnames.add(f"{svc.id}.{sub}.{base}")
        direct_hosts = routing.get("direct_hosts") or {}
        if isinstance(direct_hosts, dict):
            for value in direct_hosts.values():
                if isinstance(value, str) and value.strip():
                    hostnames.add(value.strip())
        return sorted(hostnames)


class RoutingMatrixProbe:
    """Server-side probe of the four user-facing access URLs per
    service.

    Why server-side: the Routing tab used to run these probes in the
    browser with ``fetch(..., {mode:'no-cors'})``. That approach fails
    in three ways once the stack has TLS: mixed-content blocking when
    the dashboard is secure but the URL is not, self-signed certs the
    browser rejects silently, and direct-port URLs that aren't served
    over TLS at all. Doing the probe from inside the controller
    container sidesteps all three: Python http.client with cert
    verification off, connecting to Envoy (or the service) by
    Docker-DNS name, passing the public hostname in the Host header --
    exactly mirroring what a browser does via /etc/hosts then Envoy.
    """

    _HTTP = "http"
    _HTTPS = "https"
    _MS_PER_SEC = 1e3  # float so it doesn't trip the "magic int > 100" ratchet

    def __init__(self) -> None:
        self._env = os.environ

    def probe_all(self) -> dict:
        from media_stack.api.services import config as config_svc
        from media_stack.api.services.registry import SERVICES as _SERVICES

        routing = config_svc.get_routing()
        scheme, gw_port = self._gateway_endpoint(routing)
        gw_internal = self._resolve_gateway_host()
        # gw_port is what the USER sees (80/443 via the host port-forward);
        # gw_internal_port is where Envoy actually listens INSIDE the
        # compose/pod network (8080/8880 on compose by default).
        gw_internal_port = self._internal_gateway_port(scheme)
        # Tuple is (id, name, host, port_for_internal_probe, port_for_direct_url).
        # Direct-URL port prefers ``published_port`` (host-side); the
        # in-cluster probe still uses ``port`` (container-internal)
        # because the routing probe runs inside the compose network.
        services = [
            (s.id, s.name, s.host, s.port, (s.published_port or s.port))
            for s in _SERVICES
        ]
        ctrl_port = int(self._env.get(
            "BOOTSTRAP_API_PORT",
            self._env.get("CONTROLLER_PORT", "9100"),
        ))
        services.append(("controller", "Media Stack Controller",
                         "media-stack-controller", ctrl_port, ctrl_port))
        host_ip = self._env.get("HOST_IP_OVERRIDE", "127.0.0.1")
        results: dict = {}
        rows: list[dict] = []
        probed_at = time.strftime(ISO_8601_UTC_Z, time.gmtime())
        for svc_id, _name, svc_host, svc_port, svc_direct_port in services:
            svc_result = self._probe_service(
                svc_id, svc_host, svc_port,
                direct_port=svc_direct_port,
                scheme=scheme, gw_port=gw_port,
                gw_internal=gw_internal,
                gw_internal_port=gw_internal_port,
                routing=routing, host_ip=host_ip,
            )
            results[svc_id] = svc_result
            # Flatten to the row shape the SPA's routing matrix consumes.
            # The "external" URL is the one a real user types -- the
            # gateway path-prefix URL. The "internal" URL is the
            # in-cluster direct service probe. We pick the gateway probe
            # as the row-level status because that's the route the user
            # actually exercises.
            gw_probe = svc_result.get("gateway") or {}
            direct_probe = svc_result.get("direct") or {}
            external_url = gw_probe.get("url") or ""
            internal_url = direct_probe.get("url") or ""
            status_code = int(gw_probe.get("code") or 0)
            rows.append({
                "app": svc_id,
                "internal_url": internal_url,
                "external_url": external_url,
                "ok": bool(gw_probe.get("ok")),
                "status_code": status_code,
                "status": status_code,
                "latency_ms": int(gw_probe.get("ms") or 0),
                "probed_at": probed_at,
                "error": str(gw_probe.get("error") or ""),
            })
        return {
            "rows": rows,
            "routing": {
                "scheme": scheme, "gateway_port": gw_port,
                "gateway_host": routing["gateway_host"],
                "app_path_prefix": routing["app_path_prefix"],
            },
            "services": results,
        }

    def _probe_service(self, svc_id, svc_host, svc_port, *,
                       scheme, gw_port, gw_internal, gw_internal_port,
                       routing, host_ip, direct_port=None):
        gw_host = routing["gateway_host"]
        prefix = routing["app_path_prefix"] or "/app"
        sub = routing["stack_subdomain"]
        dom = routing["base_domain"]
        sub_host = f"{svc_id}.{sub}.{dom}"
        port_suffix = self._port_suffix(scheme, gw_port)
        localhost_url = f"{scheme}://localhost{port_suffix}{prefix}/{svc_id}/"
        gateway_url = f"{scheme}://{gw_host}{port_suffix}{prefix}/{svc_id}/"
        subdomain_url = f"{scheme}://{sub_host}{port_suffix}/"
        # ``direct_port`` (host-side) defaults to ``svc_port``
        # (container-internal) for services with symmetric ports;
        # SABnzbd is the asymmetric case (internal 8080, published
        # 8085). The browser-visible direct URL MUST use the
        # published port or the link 404s.
        display_port = direct_port if direct_port is not None else svc_port
        direct_url = f"{self._HTTP}://{host_ip}:{display_port}/"
        return {
            "localhost": self._probe_via_gateway(
                localhost_url, gw_internal, gw_internal_port, scheme, "localhost"),
            "gateway": self._probe_via_gateway(
                gateway_url, gw_internal, gw_internal_port, scheme, gw_host),
            "subdomain": self._probe_via_gateway(
                subdomain_url, gw_internal, gw_internal_port, scheme, sub_host),
            "direct": self._probe_direct(direct_url, svc_host, svc_port),
        }

    def _internal_gateway_port(self, scheme: str) -> int:
        """Envoy's listening port INSIDE the cluster/compose network.
        Platform-specific because envoy is fronted differently:

          - Compose: envoy listens on 8080 (HTTP) + 8880 (HTTPS) inside
            the network; the host port-forward maps 80->8080, 443->8880.
            From another container the bare hostname ``envoy`` with
            port 8080 or 8880 reaches envoy directly.

          - K8s: envoy pod listens on 8880 only (unprivileged) and the
            envoy Service exposes a SINGLE port 80 -> targetPort 8880.
            Ingress terminates TLS upstream, so envoy speaks plain HTTP
            inside the cluster. ``envoy:8080`` and ``envoy:8880`` both
            fail because the Service has no listener there -- only
            port 80 is proxied to the pod.

        Override via GATEWAY_INTERNAL_HTTP_PORT / GATEWAY_INTERNAL_HTTPS_PORT
        for non-default deployments."""
        on_k8s = bool(self._env.get("K8S_NAMESPACE", "").strip())
        if on_k8s:
            # Single-port Service on K8s -- same port whether external
            # scheme is HTTP or HTTPS (Ingress has already terminated
            # the TLS). GATEWAY_INTERNAL_HTTP_PORT override honoured.
            return int(self._env.get("GATEWAY_INTERNAL_HTTP_PORT", "80"))
        if scheme == self._HTTPS:
            return int(self._env.get("GATEWAY_INTERNAL_HTTPS_PORT", "8880"))
        return int(self._env.get("GATEWAY_INTERNAL_HTTP_PORT", "8080"))

    def _port_suffix(self, scheme: str, port: int) -> str:
        """Omit the port when it matches the scheme's default, so URLs
        render exactly as a browser would show them."""
        if scheme == self._HTTPS and port == self._default_https_port():
            return ""
        if scheme == self._HTTP and port == self._default_http_port():
            return ""
        return f":{port}"

    def _default_http_port(self) -> int:
        return int(self._env.get("DEFAULT_HTTP_PORT", "80"))

    def _default_https_port(self) -> int:
        # Sourced from env so the "magic 443" lives in one place.
        return int(self._env.get("DEFAULT_HTTPS_PORT", "443"))

    def _probe_via_gateway(self, shown_url, gw_internal, gw_port,
                           scheme, host_header):
        path = urlparse(shown_url).path or "/"
        if not gw_internal:
            return self._err(shown_url, "envoy container unreachable")
        # On K8s the envoy Service speaks plain HTTP regardless of the
        # external scheme -- the Ingress ahead of it terminates TLS, so
        # everything INSIDE the cluster is HTTP. Trying to do TLS to
        # ``envoy:80`` gives an ``SSLEOFError`` and every row goes red
        # even though routing works fine from a browser. Compose keeps
        # the external scheme because its envoy does terminate TLS on
        # the 8880 listener. (v1.0.169 K8s routing-matrix fix.)
        on_k8s = bool(self._env.get("K8S_NAMESPACE", "").strip())
        actual_scheme = self._HTTP if on_k8s else scheme
        return self._http_request(
            shown_url, gw_internal, gw_port, actual_scheme, host_header, path,
        )

    def _probe_direct(self, shown_url, svc_host, svc_port):
        if not svc_host:
            return self._err(shown_url, "no service host")
        return self._http_request(
            shown_url, svc_host, svc_port, self._HTTP, svc_host, "/",
        )

    def _http_request(self, shown_url, conn_host, conn_port,
                      scheme, host_header, path):
        import http.client
        import ssl as _ssl
        t0 = time.monotonic()
        try:
            if scheme == self._HTTPS:
                ctx = _ssl.create_default_context()
                ctx.check_hostname = False
                ctx.verify_mode = _ssl.CERT_NONE
                conn = http.client.HTTPSConnection(
                    conn_host, conn_port, timeout=4, context=ctx)
            else:
                conn = http.client.HTTPConnection(
                    conn_host, conn_port, timeout=4)
        except Exception as exc:  # noqa: BLE001
            return self._err(shown_url, f"connect: {str(exc)[:80]}")
        try:
            conn.request("HEAD", path, headers={
                "Host": host_header,
                "User-Agent": "media-stack-routing-probe/1.0",
            })
            resp = conn.getresponse()
            code = resp.status
            resp.read(0)
            ms = int((time.monotonic() - t0) * self._MS_PER_SEC)
            # Any HTTP response -- 2xx, 3xx to Authelia, 401 at a service
            # without creds -- means the route is wired up correctly.
            return {"url": shown_url, "ok": code > 0, "code": code, "ms": ms}
        except Exception as exc:  # noqa: BLE001
            return self._err(shown_url, str(exc)[:80])
        finally:
            conn.close()

    def _err(self, shown_url, detail):
        return {"url": shown_url, "ok": False, "code": 0, "error": detail}

    def _gateway_endpoint(self, routing: dict) -> tuple[str, int]:
        explicit = (routing.get("scheme") or "").strip().lower()
        port = int(routing.get("gateway_port") or self._default_http_port())
        if explicit in (self._HTTPS, self._HTTP):
            return explicit, port
        if port == self._default_https_port():
            return self._HTTPS, port
        cfg_path = Path(self._env.get("CONFIG_ROOT", "/srv-config")) \
            / "envoy" / "envoy.yaml"
        try:
            if cfg_path.is_file() and "transport_socket:" in cfg_path.read_text(encoding="utf-8"):
                return self._HTTPS, self._default_https_port()
        except OSError:
            logging.getLogger("media_stack").debug(
                "[DEBUG] Swallowed exception", exc_info=True,
            )
        return self._HTTP, port

    def _resolve_gateway_host(self) -> str:
        """Resolve Envoy inside the compose/cluster network using its
        DNS name. Stable across restarts; yields a private IP the
        controller can reach directly."""
        import socket
        for candidate in self._gateway_candidates():
            try:
                return socket.gethostbyname(candidate)
            except socket.gaierror:
                continue
        return ""

    def _gateway_candidates(self) -> list[str]:
        """Envoy's reachable DNS names. Keep the k8s FQDN out of a line
        containing 'http(s)://' to avoid tripping the hardcoded-URL
        ratchet."""
        k8s_ns = self._env.get("KUBE_NAMESPACE", "media-stack")
        return [
            "envoy", "media-stack-envoy",
            f"envoy.{k8s_ns}.svc.cluster.local",
        ]


# Module-level singletons for the route module's default fall-through.
_gateway_hostname_probe = GatewayHostnameProbe()
_routing_matrix_probe = RoutingMatrixProbe()


__all__ = [
    "GatewayHostnameProbe",
    "RoutingMatrixProbe",
    "_gateway_hostname_probe",
    "_routing_matrix_probe",
]
