"""Render Envoy runtime config from normalized compose edge labels."""

from __future__ import annotations

import copy
import re
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
                except Exception:
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

        clusters = build_clusters_from_service_map(service_map)
        virtual_hosts, route_count = build_virtual_hosts(routes_by_host)

        payload = self._load_runtime_template_payload()
        self._replace_virtual_hosts(payload, virtual_hosts)
        self._replace_clusters(payload, clusters)

        return EnvoyDynamicConfigRender(
            payload=payload,
            route_count=route_count,
            cluster_count=len(clusters),
            ignored_redirect_middleware_count=ignored_redirect_middleware_count,
        )
