"""Render Envoy runtime config from normalized compose edge labels."""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from urllib import parse

import yaml

from core.platforms.compose.services.edge_route_graph import ComposeEdgeRouteGraphService
from core.platforms.compose.services.spec import ComposeSpecResolver

_HOST_RULE_RE = re.compile(r"Host\((?P<body>[^)]*)\)", flags=re.IGNORECASE)
_PATH_PREFIX_RULE_RE = re.compile(r"PathPrefix\((?P<body>[^)]*)\)", flags=re.IGNORECASE)
_BACKTICK_TOKEN_RE = re.compile(r"`([^`]+)`")
_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")
_DEFAULT_TEMPLATE_RELATIVE_PATH = Path("config/defaults/compose/envoy.runtime.base.yaml")


def _tokenize(value: object) -> str:
    return _NON_ALNUM_RE.sub("_", str(value or "").strip().lower()).strip("_")


def _extract_backtick_tokens(value: str) -> tuple[str, ...]:
    return tuple(
        token.strip()
        for token in _BACKTICK_TOKEN_RE.findall(str(value or ""))
        if str(token or "").strip()
    )


def _rule_hosts(rule: str) -> tuple[str, ...]:
    match = _HOST_RULE_RE.search(str(rule or ""))
    if not match:
        return ()
    return _extract_backtick_tokens(str(match.group("body") or ""))


def _rule_path_prefix(rule: str) -> str:
    match = _PATH_PREFIX_RULE_RE.search(str(rule or ""))
    if not match:
        return ""
    tokens = _extract_backtick_tokens(str(match.group("body") or ""))
    if not tokens:
        return ""
    value = str(tokens[0] or "").strip()
    if not value:
        return ""
    if not value.startswith("/"):
        value = f"/{value}"
    return value


def _strip_prefix_value(middleware_cfg: dict[str, Any]) -> str:
    strip_cfg = middleware_cfg.get("stripPrefix")
    if not isinstance(strip_cfg, dict):
        return ""
    prefixes = strip_cfg.get("prefixes")
    if isinstance(prefixes, list) and prefixes:
        value = str(prefixes[0] or "").strip()
    else:
        value = str(strip_cfg.get("prefix") or "").strip()
    if not value:
        return ""
    if not value.startswith("/"):
        value = f"/{value}"
    return value


def _cluster_name(service_name: str) -> str:
    token = _tokenize(service_name)
    return f"service_{token or 'app'}"


def _virtual_host_name(host: str) -> str:
    token = _tokenize(host)
    return f"vhost_{token or 'default'}"


def _path_prefix_app_slug(path_prefix: str) -> str:
    token = str(path_prefix or "").strip().rstrip("/")
    if not token:
        return ""
    slug = token.rsplit("/", 1)[-1].strip().lower()
    return _tokenize(slug)


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
    def _cluster_entry(name: str, *, address: str, port: int) -> dict[str, Any]:
        return {
            "name": name,
            "type": "STRICT_DNS",
            "connect_timeout": "5s",
            "lb_policy": "ROUND_ROBIN",
            "load_assignment": {
                "cluster_name": name,
                "endpoints": [
                    {
                        "lb_endpoints": [
                            {
                                "endpoint": {
                                    "address": {
                                        "socket_address": {
                                            "address": address,
                                            "port_value": int(port),
                                        }
                                    }
                                }
                            }
                        ]
                    }
                ],
            },
        }

    @staticmethod
    def _repo_root() -> Path:
        return Path(__file__).resolve().parents[7]

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

    @staticmethod
    def _route_headers(path_prefix: str, host: str) -> dict[str, Any]:
        app_slug = _path_prefix_app_slug(path_prefix)
        response_headers_to_add: list[dict[str, Any]] = [
            {
                "header": {
                    "key": "x-media-stack-prefix",
                    "value": path_prefix,
                },
                "append_action": "OVERWRITE_IF_EXISTS_OR_ADD",
            },
            {
                "header": {
                    "key": "x-media-stack-host",
                    "value": str(host or "").strip().lower(),
                },
                "append_action": "OVERWRITE_IF_EXISTS_OR_ADD",
            },
        ]
        if app_slug:
            response_headers_to_add.append(
                {
                    "header": {
                        "key": "set-cookie",
                        "value": f"media_stack_app={app_slug}; Path=/; SameSite=Lax",
                    },
                    "append_action": "APPEND_IF_EXISTS_OR_ADD",
                }
            )
        return {
            "request_headers_to_add": [
                {
                    "header": {
                        "key": "x-forwarded-prefix",
                        "value": path_prefix,
                    },
                    "append_action": "OVERWRITE_IF_EXISTS_OR_ADD",
                }
            ],
            "response_headers_to_add": response_headers_to_add,
        }

    @classmethod
    def _primary_route_cfg(cls, *, host: str, path_prefix: str, cluster_name: str) -> dict[str, Any]:
        route_cfg: dict[str, Any] = {
            "match": {"prefix": path_prefix},
            "route": {
                "cluster": cluster_name,
                "timeout": "0s",
            },
        }
        route_cfg.update(cls._route_headers(path_prefix, host))
        return route_cfg

    @classmethod
    def _referer_fallback_route_cfg(
        cls,
        *,
        host: str,
        path_prefix: str,
        cluster_name: str,
    ) -> dict[str, Any]:
        route_cfg: dict[str, Any] = {
            "match": {
                "prefix": "/",
                "headers": [
                    {
                        "name": "referer",
                        "safe_regex_match": {
                            "google_re2": {},
                            "regex": (
                                f"^https?://{re.escape(str(host or '').strip())}"
                                rf"(?:\:[0-9]+)?{re.escape(path_prefix)}(?:/.*)?$"
                            ),
                        },
                    }
                ],
            },
            "route": {
                "cluster": cluster_name,
                "timeout": "0s",
            },
        }
        route_cfg.update(cls._route_headers(path_prefix, host))
        return route_cfg

    @classmethod
    def _cookie_fallback_route_cfg(
        cls,
        *,
        host: str,
        path_prefix: str,
        cluster_name: str,
    ) -> dict[str, Any]:
        app_slug = _path_prefix_app_slug(path_prefix)
        if not app_slug:
            return {}
        route_cfg: dict[str, Any] = {
            "match": {
                "prefix": "/",
                "headers": [
                    {
                        "name": "cookie",
                        "safe_regex_match": {
                            "google_re2": {},
                            "regex": rf"(?:^|;\s*)media_stack_app={re.escape(app_slug)}(?:;|$)",
                        },
                    }
                ],
            },
            "route": {
                "cluster": cluster_name,
                "timeout": "0s",
            },
        }
        route_cfg.update(cls._route_headers(path_prefix, host))
        return route_cfg

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
            cluster_name = _cluster_name(service_name)

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

            if strip_prefix and strip_prefix == path_prefix and path_prefix != "/":
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
            for host in hosts:
                host_token = str(host or "").strip().lower()
                if not host_token:
                    continue
                route_cfg = self._primary_route_cfg(
                    host=host_token,
                    path_prefix=path_prefix,
                    cluster_name=cluster_name,
                )
                if regex_rewrite is not None:
                    route_cfg["route"]["regex_rewrite"] = dict(regex_rewrite)
                routes_by_host.setdefault(host_token, []).append((rank, dict(route_cfg)))
                if path_prefix and path_prefix != "/":
                    existing_default = default_html_redirect_by_host.get(host_token, "")
                    if not existing_default:
                        default_html_redirect_by_host[host_token] = path_prefix
                    elif _path_prefix_app_slug(path_prefix) == "homepage":
                        default_html_redirect_by_host[host_token] = path_prefix
                if path_prefix and path_prefix != "/":
                    fallback_route = self._referer_fallback_route_cfg(
                        host=host_token,
                        path_prefix=path_prefix,
                        cluster_name=cluster_name,
                    )
                    routes_by_host.setdefault(host_token, []).append(
                        (max(1, rank - 1), fallback_route)
                    )
                    cookie_fallback_route = self._cookie_fallback_route_cfg(
                        host=host_token,
                        path_prefix=path_prefix,
                        cluster_name=cluster_name,
                    )
                    if cookie_fallback_route:
                        routes_by_host.setdefault(host_token, []).append(
                            (max(1, rank - 2), cookie_fallback_route)
                        )

        for host, default_path_prefix in default_html_redirect_by_host.items():
            host_token = str(host or "").strip().lower()
            path_prefix = str(default_path_prefix or "").strip()
            if not host_token or not path_prefix or path_prefix == "/":
                continue
            routes_by_host.setdefault(host_token, []).append(
                (
                    0,
                    {
                        "match": {
                            "prefix": "/",
                            "headers": [
                                {
                                    "name": "accept",
                                    "safe_regex_match": {
                                        "google_re2": {},
                                        "regex": r"(?i).*text/html.*",
                                    },
                                }
                            ],
                        },
                        "redirect": {
                            "path_redirect": path_prefix,
                        },
                    },
                )
            )

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
            clusters.append(self._cluster_entry(name, address=address, port=port))

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

        payload = self._load_runtime_template_payload()
        self._replace_virtual_hosts(payload, virtual_hosts)
        self._replace_clusters(payload, clusters)

        return EnvoyDynamicConfigRender(
            payload=payload,
            route_count=route_count,
            cluster_count=len(clusters),
            ignored_redirect_middleware_count=ignored_redirect_middleware_count,
        )
