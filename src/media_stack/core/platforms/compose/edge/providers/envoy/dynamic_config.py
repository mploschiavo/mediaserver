"""Render Envoy runtime config from normalized compose edge labels."""

from __future__ import annotations

import copy
import os
import re

from media_stack.core.edge.tls_certificate_service import (
    TlsCertificateService as _TlsCertificateService,
)
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import yaml

from media_stack.services.apps.stack.routing_defaults import (
    DEFAULT_REDIRECT_PRIORITY_SLUG as _PRIORITY_SLUG,
    DEFAULT_REDIRECT_FALLBACK_SLUG as _FALLBACK_SLUG,
    APP_ROOT_DASHBOARD_SLUG as _DASHBOARD_SLUG,
)
from media_stack.core.platforms.compose.edge.providers.envoy.clusters import (
    build_clusters_from_service_map,
)
from media_stack.core.platforms.compose.edge.providers.envoy.helpers import (
    _cluster_name,
    _extract_backtick_tokens,
    _path_prefix_app_slug,
    _path_prefix_root,
    _rule_hosts,
    _rule_path_prefix,
    _session_cookie_name,
    _strip_prefix_value,
    _tokenize,
    _virtual_host_name,
)
from media_stack.core.platforms.compose.edge.providers.envoy.routes import (
    cookie_fallback_route_cfg,
    fallback_regex_rewrite,
    html_accept_header_match,
    primary_route_cfg,
    referer_fallback_route_cfg,
)
from media_stack.core.platforms.compose.edge.providers.envoy.virtual_hosts import (
    build_virtual_hosts,
)
from media_stack.core.platforms.compose.services.edge_route_graph import ComposeEdgeRouteGraphService
from media_stack.core.platforms.compose.services.spec import ComposeSpecResolver
from media_stack.core.auth.gateway_policy import GatewayAuthPolicy
from media_stack.core.auth.envoy_ext_authz import (
    apply_per_route_auth_policy,
    inject_ext_authz_into_payload,
)
import logging

_DEFAULT_TEMPLATE_RELATIVE_PATH = Path("config/defaults/compose/envoy.runtime.base.yaml")
_PRIMARY_ROUTE_RANK_BASE = 3000
_HTML_FALLBACK_REDIRECT_ROUTE_RANK_BASE = 2500
_REFERER_FALLBACK_ROUTE_RANK_BASE = 2000
_COOKIE_FALLBACK_ROUTE_RANK_BASE = 1000
_DEFAULT_HTML_REDIRECT_ROUTE_RANK = 0


@dataclass(frozen=True)
class EnvoyDynamicConfigRender:
    payload: dict[str, Any]
    route_count: int
    cluster_count: int
    ignored_redirect_middleware_count: int


TemplateLoaderFn = Callable[[Path], dict[str, Any]]


@dataclass
class EnvoyDynamicConfigService:
    route_graph_service: ComposeEdgeRouteGraphService
    spec_resolver: ComposeSpecResolver
    runtime_template_path: Path | None = None
    template_loader: TemplateLoaderFn | None = None
    auth_policy: GatewayAuthPolicy | None = None

    @staticmethod
    def _repo_root() -> Path:
        return Path(__file__).resolve().parents[8]

    def _resolve_runtime_template_path(self) -> Path:
        if self.runtime_template_path is not None:
            candidate = Path(self.runtime_template_path).expanduser()
        else:
            env = self.spec_resolver.compose_environment()
            token = str(env.get("ENVOY_RUNTIME_TEMPLATE_FILE") or "").strip()
            if token:
                candidate = Path(token).expanduser()
            else:
                candidate = self._repo_root() / _DEFAULT_TEMPLATE_RELATIVE_PATH

        if candidate.is_absolute():
            return candidate
        return (self._repo_root() / candidate).resolve()

    def _load_runtime_template_payload(self) -> dict[str, Any]:
        template_path = self._resolve_runtime_template_path()
        if self.template_loader is not None:
            payload = self.template_loader(template_path)
        else:
            if not template_path.exists():
                raise RuntimeError(
                    "Envoy runtime template file not found: "
                    f"{template_path}. Set ENVOY_RUNTIME_TEMPLATE_FILE to override."
                )
            payload = yaml.safe_load(template_path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise RuntimeError(
                "Envoy runtime template must deserialize to a mapping payload: " f"{template_path}"
            )
        return copy.deepcopy(payload)

    @staticmethod
    def _resolve_route_config(payload: dict[str, Any]) -> dict[str, Any]:
        static_resources = payload.get("static_resources")
        if not isinstance(static_resources, dict):
            raise RuntimeError("Envoy runtime template is missing static_resources mapping.")
        listeners = static_resources.get("listeners")
        if not isinstance(listeners, list) or not listeners:
            raise RuntimeError("Envoy runtime template must declare at least one listener.")
        listener = listeners[0]
        if not isinstance(listener, dict):
            raise RuntimeError("Envoy runtime template listener entry must be a mapping.")
        filter_chains = listener.get("filter_chains")
        if not isinstance(filter_chains, list) or not filter_chains:
            raise RuntimeError("Envoy runtime template listener must include filter_chains.")
        first_chain = filter_chains[0]
        if not isinstance(first_chain, dict):
            raise RuntimeError("Envoy runtime template filter chain entry must be a mapping.")
        filters = first_chain.get("filters")
        if not isinstance(filters, list) or not filters:
            raise RuntimeError("Envoy runtime template filter chain must include filters.")
        first_filter = filters[0]
        if not isinstance(first_filter, dict):
            raise RuntimeError("Envoy runtime template filter entry must be a mapping.")
        typed_config = first_filter.get("typed_config")
        if not isinstance(typed_config, dict):
            raise RuntimeError(
                "Envoy runtime template http_connection_manager typed_config is missing."
            )
        route_config = typed_config.get("route_config")
        if not isinstance(route_config, dict):
            raise RuntimeError(
                "Envoy runtime template typed_config must include route_config mapping."
            )
        return route_config

    @staticmethod
    def _replace_virtual_hosts(
        payload: dict[str, Any], virtual_hosts: list[dict[str, Any]]
    ) -> None:
        route_config = EnvoyDynamicConfigService._resolve_route_config(payload)
        route_config["virtual_hosts"] = list(virtual_hosts)

    @staticmethod
    def _replace_clusters(payload: dict[str, Any], clusters: list[dict[str, Any]]) -> None:
        static_resources = payload.get("static_resources")
        if not isinstance(static_resources, dict):
            raise RuntimeError("Envoy runtime template is missing static_resources mapping.")
        static_resources["clusters"] = list(clusters)

    def _apply_route_auth(self, route: dict[str, Any], service_name: str) -> None:
        """Apply per-route auth policy if gateway auth is active."""
        if self.auth_policy and self.auth_policy.ext_authz:
            apply_per_route_auth_policy(route, service_name, self.auth_policy)

    def render(self, services: dict[str, dict[str, Any]]) -> EnvoyDynamicConfigRender:
        route_graph = self.route_graph_service.render(services)
        http_cfg = dict(route_graph.payload.get("http") or {})
        routers = dict(http_cfg.get("routers") or {})
        middlewares = dict(http_cfg.get("middlewares") or {})
        service_map = dict(http_cfg.get("services") or {})

        routes_by_host: dict[str, list[tuple[int, dict[str, Any]]]] = {}
        default_html_redirect_by_host: dict[str, str] = {}
        ignored_redirect_middleware_count = 0

        for router_name in sorted(routers.keys()):
            router_cfg = routers.get(router_name) or {}
            if not isinstance(router_cfg, dict):
                continue
            rule = str(router_cfg.get("rule") or "").strip()
            hosts = _rule_hosts(rule)
            if not hosts:
                continue
            path_prefix = _rule_path_prefix(rule) or "/"

            service_name = str(router_cfg.get("service") or "").strip()
            if not service_name:
                service_name = str(router_name or "").strip()
            if not service_name:
                continue
            cluster_name_val = _cluster_name(service_name)

            strip_prefix = ""
            router_middlewares = router_cfg.get("middlewares")
            if isinstance(router_middlewares, list):
                middleware_names = [str(item or "").strip() for item in router_middlewares]
            else:
                middleware_names = []
            for middleware_name in middleware_names:
                if not middleware_name:
                    continue
                middleware_cfg = middlewares.get(middleware_name)
                if not isinstance(middleware_cfg, dict):
                    continue
                if "redirectRegex" in middleware_cfg:
                    ignored_redirect_middleware_count += 1
                if not strip_prefix:
                    strip_prefix = _strip_prefix_value(middleware_cfg)

            # Determine if this service needs prefix stripping.
            # 1. Explicit stripPrefix middleware in labels (legacy)
            # 2. Registry flag: preserve_path_prefix=False means strip
            needs_strip = bool(strip_prefix and strip_prefix == path_prefix)
            if not needs_strip and path_prefix != "/":
                try:
                    from media_stack.api.services.registry import get_service
                    svc = get_service(service_name)
                    if svc and not svc.preserve_path_prefix:
                        needs_strip = True
                        strip_prefix = path_prefix  # propagate to fallback routes
                except Exception as exc:
                    logging.getLogger("media_stack").debug("[DEBUG] Swallowed: %s", exc)
                    pass

            if needs_strip and path_prefix != "/":
                regex_rewrite = {
                    "pattern": {
                        "google_re2": {},
                        "regex": f"^{re.escape(path_prefix)}/?(.*)$",
                    },
                    "substitution": r"/\1",
                }
            else:
                regex_rewrite = None

            rank = len(path_prefix)
            primary_rank = _PRIMARY_ROUTE_RANK_BASE + rank
            html_primary_rank = primary_rank + 1
            html_fallback_redirect_rank = _HTML_FALLBACK_REDIRECT_ROUTE_RANK_BASE + rank
            referer_fallback_rank = _REFERER_FALLBACK_ROUTE_RANK_BASE + rank
            cookie_fallback_rank = _COOKIE_FALLBACK_ROUTE_RANK_BASE + rank
            for host in hosts:
                host_token = str(host or "").strip().lower()
                if not host_token:
                    continue
                route_cfg = primary_route_cfg(
                    host=host_token,
                    path_prefix=path_prefix,
                    cluster_name=cluster_name_val,
                    include_session_cookie=False,
                )
                if regex_rewrite is not None:
                    route_cfg["route"]["regex_rewrite"] = dict(regex_rewrite)
                routes_by_host.setdefault(host_token, []).append((primary_rank, dict(route_cfg)))
                # Redirect /app/service (no trailing slash) → /app/service/
                # so relative URLs in SPAs resolve correctly.
                if path_prefix and path_prefix != "/" and not path_prefix.endswith("/"):
                    routes_by_host.setdefault(host_token, []).append((
                        primary_rank + 2,
                        {
                            "match": {"path": path_prefix},
                            "redirect": {"path_redirect": path_prefix + "/"},
                        },
                    ))
                html_route_cfg = primary_route_cfg(
                    host=host_token,
                    path_prefix=path_prefix,
                    cluster_name=cluster_name_val,
                    include_session_cookie=True,
                    prefer_uncompressed_upstream=True,
                )
                if regex_rewrite is not None:
                    html_route_cfg["route"]["regex_rewrite"] = dict(regex_rewrite)
                html_match = dict(html_route_cfg.get("match") or {})
                html_match_headers = list(html_match.get("headers") or [])
                html_match_headers.append(
                    {
                        "name": "accept",
                        "safe_regex_match": {
                            "google_re2": {},
                            "regex": r"(?i).*text/html.*",
                        },
                    }
                )
                html_match["headers"] = html_match_headers
                html_route_cfg["match"] = html_match
                routes_by_host.setdefault(host_token, []).append(
                    (html_primary_rank, html_route_cfg)
                )
                if path_prefix and path_prefix != "/":
                    slug = _path_prefix_app_slug(path_prefix)
                    existing_default = default_html_redirect_by_host.get(host_token, "")
                    existing_slug = _path_prefix_app_slug(existing_default)
                    if not existing_default:
                        default_html_redirect_by_host[host_token] = path_prefix
                    elif slug == _PRIORITY_SLUG:
                        default_html_redirect_by_host[host_token] = path_prefix
                    elif slug == _FALLBACK_SLUG and existing_slug != _PRIORITY_SLUG:
                        default_html_redirect_by_host[host_token] = path_prefix
                if path_prefix and path_prefix != "/":
                    fb_regex_rewrite = fallback_regex_rewrite(
                        path_prefix=path_prefix,
                        strip_prefix=strip_prefix,
                    )
                    fallback_route = referer_fallback_route_cfg(
                        host=host_token,
                        path_prefix=path_prefix,
                        cluster_name=cluster_name_val,
                        regex_rewrite=fb_regex_rewrite,
                    )
                    routes_by_host.setdefault(host_token, []).append(
                        (referer_fallback_rank, fallback_route)
                    )
                    cookie_fb_route = cookie_fallback_route_cfg(
                        host=host_token,
                        path_prefix=path_prefix,
                        cluster_name=cluster_name_val,
                        regex_rewrite=fb_regex_rewrite,
                    )
                    if cookie_fb_route:
                        routes_by_host.setdefault(host_token, []).append(
                            (cookie_fallback_rank, cookie_fb_route)
                        )

        for host, default_path_prefix in default_html_redirect_by_host.items():
            host_token = str(host or "").strip().lower()
            path_prefix = str(default_path_prefix or "").strip()
            if not host_token or not path_prefix or path_prefix == "/":
                continue
            default_slug = _path_prefix_app_slug(path_prefix)
            default_cluster = f"service_{default_slug}"
            # HTML browsers: redirect to the default app's path-prefix URL.
            routes_by_host.setdefault(host_token, []).append(
                (
                    _DEFAULT_HTML_REDIRECT_ROUTE_RANK,
                    {
                        "match": {
                            "prefix": "/",
                            "headers": [
                                html_accept_header_match(),
                            ],
                        },
                        "redirect": {
                            "path_redirect": path_prefix,
                        },
                    },
                )
            )
            # Bare app-root redirect: /app and /app/ -> /app/<dashboard> so
            # users who type the prefix root into the browser get the dashboard.
            app_root = _path_prefix_root(path_prefix)
            if app_root and app_root != "/":
                homepage_path = f"{app_root}/{_DASHBOARD_SLUG}"
                for bare_path in (app_root, f"{app_root}/"):
                    routes_by_host.setdefault(host_token, []).append(
                        (
                            _PRIMARY_ROUTE_RANK_BASE + len(bare_path) + 1,
                            {
                                "match": {"path": bare_path},
                                "redirect": {"path_redirect": homepage_path},
                            },
                        )
                    )
            # Catch-all for unknown /app/X paths in HTML browsers: redirect
            # to /app/homepage so users see the dashboard instead of a blank
            # page or wrong service.  Rank just above the root HTML redirect
            # but below all known service routes.
            if app_root and app_root != "/":
                routes_by_host.setdefault(host_token, []).append(
                    (
                        _DEFAULT_HTML_REDIRECT_ROUTE_RANK + 1,
                        {
                            "match": {
                                "prefix": f"{app_root}/",
                                "headers": [
                                    html_accept_header_match(),
                                ],
                            },
                            "redirect": {
                                "path_redirect": homepage_path,
                            },
                        },
                    )
                )
            # Exact root match: redirect "/" to the default app path for all
            # request types (browsers and TV apps). Rank above cookie fallback
            # so stale app cookies don't hijack root. TV apps that follow
            # redirects will land on /app/jellyfin which proxies correctly.
            routes_by_host.setdefault(host_token, []).append(
                (
                    _COOKIE_FALLBACK_ROUTE_RANK_BASE + 9999,
                    {
                        "match": {"path": "/"},
                        "redirect": {
                            "path_redirect": path_prefix,
                        },
                    },
                )
            )
            # Non-HTML catch-all: proxy all remaining unmatched requests to
            # the default app so paths without cookie/referer still work.
            routes_by_host.setdefault(host_token, []).append(
                (
                    _DEFAULT_HTML_REDIRECT_ROUTE_RANK - 1,
                    {
                        "match": {"prefix": "/"},
                        "route": {
                            "cluster": default_cluster,
                            "timeout": "0s",
                        },
                    },
                )
            )

        # /app/controller → /app/media-stack-controller redirect alias.
        # The controller service ID is media-stack-controller, but users
        # expect /app/controller to work as a shortcut.
        for host_token, host_routes in routes_by_host.items():
            has_controller = any(
                r.get("match", {}).get("prefix") == "/app/media-stack-controller"
                for _, r in host_routes
                if isinstance(r, dict)
            )
            if has_controller:
                host_routes.append((
                    _PRIMARY_ROUTE_RANK_BASE + len("/app/controller") + 1,
                    {
                        "match": {"prefix": "/app/controller/"},
                        "redirect": {"prefix_rewrite": "/app/media-stack-controller/"},
                    },
                ))
                host_routes.append((
                    _PRIMARY_ROUTE_RANK_BASE + len("/app/controller") + 2,
                    {
                        "match": {"path": "/app/controller"},
                        "redirect": {"path_redirect": "/app/media-stack-controller"},
                    },
                ))

        # OIDC callback route: /login → redirect to /app/jellyseerr/login
        # Jellyseerr's OIDC flow redirects to /login at the root (without
        # the /app/jellyseerr prefix) because it uses window.location.origin.
        # Route this to Jellyseerr so the OIDC callback completes.
        if self.auth_policy and self.auth_policy.ext_authz:
            for host_token, host_routes in routes_by_host.items():
                has_jellyseerr = any(
                    r.get("match", {}).get("prefix") == "/app/jellyseerr"
                    for _, r in host_routes
                    if isinstance(r, dict)
                )
                if has_jellyseerr:
                    from media_stack.core.auth.envoy_ext_authz import route_ext_authz_disabled_config as _authz_off
                    host_routes.append((
                        _PRIMARY_ROUTE_RANK_BASE + len("/login") + 1,
                        {
                            "match": {"prefix": "/login"},
                            "redirect": {"prefix_rewrite": "/app/jellyseerr/login"},
                            "typed_per_filter_config": _authz_off(),
                        },
                    ))

        clusters = build_clusters_from_service_map(service_map)

        # Add auth portal vhost + cluster when gateway auth is active.
        # The auth portal (e.g. auth.media-stack.local) must be reachable
        # through Envoy so browsers can load the login page.
        _auth_vhost = None
        if self.auth_policy and self.auth_policy.ext_authz:
            ext = self.auth_policy.ext_authz
            # Derive auth portal hostname from the gateway host
            # (e.g. apps.media-stack.local → auth.media-stack.local)
            env = self.spec_resolver.compose_environment()
            gw_host = str(env.get("APP_GATEWAY_HOST", "")).strip()
            if gw_host:
                parts = gw_host.split(".", 1)
                auth_host = f"auth.{parts[1]}" if len(parts) > 1 else f"auth.{gw_host}"
                auth_cluster_name = f"service_{ext.host}"
                # Proxy all requests on auth subdomain to auth provider
                auth_route: dict[str, Any] = {
                    "match": {"prefix": "/"},
                    "route": {"cluster": auth_cluster_name, "timeout": "0s"},
                }
                # Disable ext_authz on the auth portal itself
                from media_stack.core.auth.envoy_ext_authz import route_ext_authz_disabled_config
                auth_route["typed_per_filter_config"] = route_ext_authz_disabled_config()
                _auth_vhost = {
                    "name": f"vhost_{ext.host}",
                    "domains": [auth_host, f"{auth_host}:*"],
                    "routes": [auth_route],
                }
                # Remove any existing routes for this host to prevent
                # build_virtual_hosts from creating a duplicate vhost.
                routes_by_host.pop(auth_host, None)
                # Add cluster for auth provider (if not already in clusters)
                auth_cluster_names = {c["name"] for c in clusters}
                if auth_cluster_name not in auth_cluster_names:
                    from media_stack.core.platforms.compose.edge.providers.envoy.clusters import build_cluster_entry
                    clusters.append(build_cluster_entry(
                        auth_cluster_name, address=ext.host, port=ext.port,
                    ))

        virtual_hosts, route_count = build_virtual_hosts(routes_by_host)

        # Insert auth portal vhost before the catch-all (if created above)
        if _auth_vhost is not None:
            catchall_idx = next(
                (i for i, vh in enumerate(virtual_hosts) if vh.get("name") == "vhost_catchall"),
                len(virtual_hosts),
            )
            virtual_hosts.insert(catchall_idx, _auth_vhost)
            route_count += 1

        # Apply per-route auth policy (ext_authz bypass for native/public services)
        if self.auth_policy and self.auth_policy.ext_authz:
            self._apply_auth_to_virtual_hosts(virtual_hosts, service_map)

        payload = self._load_runtime_template_payload()
        self._replace_virtual_hosts(payload, virtual_hosts)
        self._replace_clusters(payload, clusters)

        # Inject ext_authz filter and cluster into the payload
        if self.auth_policy and self.auth_policy.ext_authz:
            # Build auth portal URL for Authelia redirect parameter
            _auth_portal = ""
            if _auth_vhost is not None:
                _auth_domains = _auth_vhost.get("domains", [])
                if _auth_domains:
                    _auth_portal = f"https://{_auth_domains[0]}"
            inject_ext_authz_into_payload(payload, self.auth_policy, _auth_portal)

        # Inject TLS transport socket if cert files exist (compose with TLS).
        # K8s uses ingress TLS termination, so certs won't be present.
        self._inject_tls_if_available(payload)

        return EnvoyDynamicConfigRender(
            payload=payload,
            route_count=route_count,
            cluster_count=len(clusters),
            ignored_redirect_middleware_count=ignored_redirect_middleware_count,
        )

    def _inject_tls_if_available(self, payload: dict[str, Any]) -> None:
        """Add TLS transport socket to the main listener.

        Compose deployments mount certs at /certs/. K8s uses ingress TLS
        termination so certs won't be present and Envoy runs plain HTTP.

        If a cert dir is mounted but empty on the compose path, we
        auto-mint a self-signed cert on the spot so the generator never
        silently produces a plain-HTTP listener (which was the root
        cause of a real apps-host TLS regression).
        """
        cert_path, key_path = self._resolve_or_mint_certs()
        if not cert_path:
            return

        listeners = payload.get("static_resources", {}).get("listeners", [])
        if not listeners:
            return
        main_listener = listeners[0]
        filter_chains = main_listener.get("filter_chains", [])
        if not filter_chains:
            return
        # Only add if not already present
        if "transport_socket" in filter_chains[0]:
            return
        filter_chains[0]["transport_socket"] = {
            "name": "envoy.transport_sockets.tls",
            "typed_config": {
                "@type": "type.googleapis.com/envoy.extensions.transport_sockets.tls.v3.DownstreamTlsContext",
                "common_tls_context": {
                    "tls_certificates": [{
                        "certificate_chain": {"filename": cert_path},
                        "private_key": {"filename": key_path},
                    }],
                },
            },
        }
        main_listener["name"] = "listener_https"

    _CERT_CANDIDATE_PATHS: tuple[tuple[str, str], ...] = (
        ("/certs/media-stack.crt", "/certs/media-stack.key"),
        ("/etc/envoy/certs/media-stack.crt",
         "/etc/envoy/certs/media-stack.key"),
    )

    def _resolve_or_mint_certs(self) -> tuple[str | None, str | None]:
        """Return (cert_path, key_path) for the compose Envoy listener.

        Resolution order:
          1. First cert+key pair that already exists on disk.
          2. If none exist but a writable candidate DIR is present,
             mint a self-signed pair into it and return that.
          3. Otherwise (K8s: no cert dir mounted), return (None, None)
             so the caller falls back to plain HTTP (K8s terminates
             TLS upstream).
        """
        for c, k in self._CERT_CANDIDATE_PATHS:
            if Path(c).exists() and Path(k).exists():
                return c, k
        for c, k in self._CERT_CANDIDATE_PATHS:
            minted = self._try_mint_cert(Path(c), Path(k))
            if minted:
                return c, k
        return None, None

    def _try_mint_cert(self, cert_file: Path, key_file: Path) -> bool:
        """Try to mint a self-signed cert at (cert_file, key_file).
        Returns True on success. Failures are logged at DEBUG — the
        caller will try the next candidate path."""
        cert_dir = cert_file.parent
        if not (cert_dir.exists() and os.access(cert_dir, os.W_OK)):
            return False
        try:
            svc = _TlsCertificateService(
                cert_dir=cert_dir, cert_name=cert_file.name,
                key_name=key_file.name,
            )
            svc.regenerate()
            return True
        except Exception as exc:  # noqa: BLE001
            logging.getLogger("media_stack").debug(
                "[DEBUG] compose cert mint failed at %s: %s", cert_dir, exc,
            )
            return False

    def _apply_auth_to_virtual_hosts(
        self,
        virtual_hosts: list[dict[str, Any]],
        service_map: dict[str, Any],
    ) -> None:
        """Apply per-route ext_authz bypass based on service auth policy.

        Walks all routes in all virtual hosts. For each route, extracts the
        cluster name to resolve the service, then applies auth policy.
        """
        if not self.auth_policy:
            return

        # Build cluster→service_name mapping
        cluster_to_service: dict[str, str] = {}
        for svc_name in service_map:
            cluster_to_service[_cluster_name(svc_name)] = svc_name

        for vhost in virtual_hosts:
            for route in vhost.get("routes", []):
                route_action = route.get("route") or {}
                cluster = route_action.get("cluster", "")
                svc_name = cluster_to_service.get(cluster, "")
                if svc_name:
                    self._apply_route_auth(route, svc_name)
