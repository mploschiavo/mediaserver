"""Compose edge route graph rendering from normalized service labels.

This service is provider-neutral and derives router/service/middleware
structures from the active compose provider label spec.
"""

from __future__ import annotations


from media_stack.core.logging_utils import log_swallowed
from dataclasses import dataclass
from typing import Any

from media_stack.adapters.compose.services.labels import ComposeLabelService
from media_stack.adapters.compose.services.spec import ComposeSpecResolver
import logging

_TOKEN_RENAMES = {
    "certresolver": "certResolver",
    "entrypoints": "entryPoints",
    "insecureskipverify": "insecureSkipVerify",
    "loadbalancer": "loadBalancer",
    "passhostheader": "passHostHeader",
    "redirectregex": "redirectRegex",
    "stripprefix": "stripPrefix",
}


def _truthy(value: object) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on", "y"}


def _split_csv(value: object) -> list[str]:
    return [token.strip() for token in str(value or "").split(",") if token.strip()]


def _coerce_scalar(value: object) -> Any:
    token = str(value or "").strip()
    lower = token.lower()
    if lower in {"true", "false"}:
        return lower == "true"
    try:
        return int(token)
    except Exception:
        return token


def _normalize_token(name: str) -> str:
    token = str(name or "").strip()
    return _TOKEN_RENAMES.get(token.lower(), token)


def _set_nested(payload: dict[str, Any], dotted_path: str, value: Any) -> None:
    keys = [_normalize_token(part) for part in str(dotted_path or "").split(".") if part]
    if not keys:
        return
    cursor = payload
    for key in keys[:-1]:
        nested = cursor.get(key)
        if not isinstance(nested, dict):
            nested = {}
            cursor[key] = nested
        cursor = nested
    cursor[keys[-1]] = value


@dataclass(frozen=True)
class ComposeEdgeRouteGraphRender:
    payload: dict[str, Any]
    router_count: int
    service_count: int
    middleware_count: int


@dataclass
class ComposeEdgeRouteGraphService:
    label_service: ComposeLabelService
    spec_resolver: ComposeSpecResolver

    @staticmethod
    def _default_service_prefix(router_prefix: str) -> str:
        token = str(router_prefix or "").strip()
        if not token:
            return ""
        if ".routers." in token:
            return token.replace(".routers.", ".services.")
        return ""

    @staticmethod
    def _default_middleware_prefix(router_prefix: str) -> str:
        token = str(router_prefix or "").strip()
        if not token:
            return ""
        if ".routers." in token:
            return token.replace(".routers.", ".middlewares.")
        return ""

    def _label_prefixes(self) -> tuple[str, str, str, str]:
        provider_spec = self.label_service.provider_spec()
        enable_key = str(provider_spec.get("enable_label_key") or "").strip()
        router_prefix = str(provider_spec.get("router_label_prefix") or "").strip()
        service_prefix = str(provider_spec.get("service_label_prefix") or "").strip()
        middleware_prefix = str(provider_spec.get("middleware_label_prefix") or "").strip()
        if not service_prefix:
            service_prefix = self._default_service_prefix(router_prefix)
        if not middleware_prefix:
            middleware_prefix = self._default_middleware_prefix(router_prefix)
        return enable_key, router_prefix, service_prefix, middleware_prefix

    @staticmethod
    def _parse_label_group(labels: dict[str, str], prefix: str) -> dict[str, dict[str, str]]:
        out: dict[str, dict[str, str]] = {}
        token = str(prefix or "").strip()
        if not token:
            return out
        for raw_key, raw_value in labels.items():
            key = str(raw_key or "").strip()
            if not key.startswith(token):
                continue
            suffix = key[len(token) :]
            if "." not in suffix:
                continue
            group_name, _, field = suffix.partition(".")
            group = str(group_name or "").strip()
            field_key = str(field or "").strip()
            if not group or not field_key:
                continue
            out.setdefault(group, {})[field_key] = str(raw_value or "")
        return out

    def render(self, services: dict[str, dict[str, Any]]) -> ComposeEdgeRouteGraphRender:
        enable_key, router_prefix, service_prefix, middleware_prefix = self._label_prefixes()
        if not enable_key or not router_prefix:
            return ComposeEdgeRouteGraphRender(
                payload={"http": {"routers": {}, "services": {}, "middlewares": {}}},
                router_count=0,
                service_count=0,
                middleware_count=0,
            )

        routers: dict[str, dict[str, Any]] = {}
        middlewares: dict[str, dict[str, Any]] = {}
        service_ports: dict[str, int] = {}
        service_schemes: dict[str, str] = {}
        service_pass_host_header: dict[str, bool] = {}
        service_owner: dict[str, str] = {}
        container_name_by_service: dict[str, str] = {}

        for service_name, spec in services.items():
            service_key = str(service_name or "").strip()
            if not service_key:
                continue
            container_name = self.spec_resolver.container_name(service_key, spec)
            container_name_by_service[service_key] = container_name
            labels = self.label_service.normalize_labels(service_key, spec)
            if not _truthy(labels.get(enable_key)):
                continue

            router_groups = self._parse_label_group(labels, router_prefix)
            for router_name, fields in router_groups.items():
                router_cfg = routers.setdefault(router_name, {})
                for field_name, raw_value in fields.items():
                    value: Any = str(raw_value or "")
                    normalized_field = str(field_name or "").strip().lower()
                    if normalized_field in {"middlewares", "entrypoints"}:
                        value = _split_csv(raw_value)
                    elif normalized_field == "tls":
                        value = _truthy(raw_value)
                    else:
                        value = _coerce_scalar(raw_value)
                    _set_nested(router_cfg, field_name, value)

                router_service = str(router_cfg.get("service") or "").strip()
                if not router_service:
                    router_service = service_key
                    router_cfg["service"] = router_service
                service_owner.setdefault(router_service, service_key)

            service_groups = self._parse_label_group(labels, service_prefix)
            for logical_service_name, fields in service_groups.items():
                service_owner.setdefault(logical_service_name, service_key)
                raw_port = str(fields.get("loadbalancer.server.port") or "").strip()
                if raw_port:
                    try:
                        service_ports[logical_service_name] = int(raw_port)
                    except Exception as exc:
                        log_swallowed(exc)
                        continue
                raw_scheme = str(fields.get("loadbalancer.server.scheme") or "").strip().lower()
                if raw_scheme:
                    service_schemes[logical_service_name] = raw_scheme
                raw_pass_host = fields.get("loadbalancer.passhostheader")
                if raw_pass_host is not None:
                    service_pass_host_header[logical_service_name] = _truthy(raw_pass_host)

            middleware_groups = self._parse_label_group(labels, middleware_prefix)
            for middleware_name, fields in middleware_groups.items():
                middleware_cfg = middlewares.setdefault(middleware_name, {})
                for field_name, raw_value in fields.items():
                    value: Any = _coerce_scalar(raw_value)
                    if str(field_name or "").strip().lower().endswith("prefixes"):
                        value = _split_csv(raw_value)
                    _set_nested(middleware_cfg, field_name, value)

        http_services: dict[str, dict[str, Any]] = {}
        for logical_service_name in sorted(service_owner.keys()):
            port = service_ports.get(logical_service_name)
            if not port:
                continue
            owner_service = service_owner.get(logical_service_name, "")
            target_container = container_name_by_service.get(owner_service)
            if not target_container:
                continue
            scheme = service_schemes.get(logical_service_name, "http")
            load_balancer: dict[str, Any] = {
                "servers": [{"url": f"{scheme}://{target_container}:{port}"}],
            }
            if logical_service_name in service_pass_host_header:
                load_balancer["passHostHeader"] = service_pass_host_header[logical_service_name]
            http_services[logical_service_name] = {"loadBalancer": load_balancer}

        payload: dict[str, Any] = {"http": {}}
        if routers:
            payload["http"]["routers"] = {name: routers[name] for name in sorted(routers.keys())}
        if http_services:
            payload["http"]["services"] = {
                name: http_services[name] for name in sorted(http_services.keys())
            }
        if middlewares:
            payload["http"]["middlewares"] = {
                name: middlewares[name] for name in sorted(middlewares.keys())
            }

        if not payload["http"]:
            payload = {"http": {"routers": {}, "services": {}, "middlewares": {}}}

        return ComposeEdgeRouteGraphRender(
            payload=payload,
            router_count=len(payload.get("http", {}).get("routers", {})),
            service_count=len(payload.get("http", {}).get("services", {})),
            middleware_count=len(payload.get("http", {}).get("middlewares", {})),
        )
