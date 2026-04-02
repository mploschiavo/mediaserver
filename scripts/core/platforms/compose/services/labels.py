"""Compose edge/auth routing label helpers."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ComposeLabelConfig:
    project_name: str
    route_strategy: str = "subdomain"
    allowed_route_strategies: tuple[str, ...] = ()
    app_gateway_host: str = ""
    app_path_prefix: str = "/app"
    media_server_direct_host: str = ""
    internet_exposed: bool = False
    auth_provider: str = ""
    auth_middleware: str = ""
    edge_router_provider: str = ""
    edge_compose_provider_specs: dict[str, dict[str, str]] = field(default_factory=dict)
    auth_provider_middleware_defaults: dict[str, str] = field(default_factory=dict)
    media_server_service_names: tuple[str, ...] = field(default_factory=tuple)


@dataclass
class ComposeLabelService:
    cfg: ComposeLabelConfig

    def edge_router_provider(self) -> str:
        return str(self.cfg.edge_router_provider or "").strip().lower()

    def route_strategy(self) -> str:
        strategy = str(self.cfg.route_strategy or "").strip().lower()
        allowed = tuple(
            str(item or "").strip().lower()
            for item in tuple(self.cfg.allowed_route_strategies or ())
            if str(item or "").strip()
        )
        if strategy and strategy in set(allowed):
            return strategy
        return allowed[0] if allowed else strategy

    def _edge_provider_spec(self) -> dict[str, str]:
        provider = self.edge_router_provider()
        if not provider:
            return {}
        specs = self.cfg.edge_compose_provider_specs or {}
        raw_spec = specs.get(provider) if isinstance(specs, dict) else None
        if not isinstance(raw_spec, dict):
            return {}
        out: dict[str, str] = {}
        for raw_key, raw_value in raw_spec.items():
            key = str(raw_key or "").strip()
            value = str(raw_value or "").strip()
            if key and value:
                out[key] = value
        return out

    @staticmethod
    def _format_template(template: str, **kwargs: object) -> str:
        try:
            return str(template).format(**kwargs)
        except Exception:
            return ""

    def _path_route_prefix(self, service_name: str) -> str:
        token = str(self.cfg.app_path_prefix or "").strip()
        if not token:
            token = "/app"
        if not token.startswith("/"):
            token = f"/{token}"
        token = token.rstrip("/")
        return f"{token}/{service_name}"

    def _router_names(self, labels: dict[str, str]) -> set[str]:
        prefix = str(self._edge_provider_spec().get("router_label_prefix") or "").strip()
        if not prefix:
            return set()
        names: set[str] = set()
        for key in labels.keys():
            if not key.startswith(prefix):
                continue
            suffix = key[len(prefix) :]
            if "." not in suffix:
                continue
            router_name = suffix.split(".", 1)[0].strip()
            if router_name:
                names.add(router_name)
        return names

    def _apply_router_middleware(
        self, labels: dict[str, str], router_name: str, middleware_name: str
    ) -> None:
        if not middleware_name:
            return
        key_template = str(
            self._edge_provider_spec().get("router_middleware_key_template") or ""
        ).strip()
        if not key_template:
            return
        key = self._format_template(key_template, router_name=router_name)
        if not key:
            return
        existing = str(labels.get(key, "") or "").strip()
        items = [item.strip() for item in existing.split(",") if item.strip()]
        if middleware_name not in items:
            items.append(middleware_name)
        labels[key] = ",".join(items)

    def _is_edge_router_service(self, labels: dict[str, str]) -> bool:
        enable_key = str(self._edge_provider_spec().get("enable_label_key") or "").strip()
        if not enable_key:
            return False
        enabled = str(labels.get(enable_key, "") or "").strip().lower()
        return enabled in {"true", "1", "yes", "on"}

    def _is_media_server_service(self, service_name: str) -> bool:
        service_key = str(service_name or "").strip().lower()
        media_services = {
            str(item or "").strip().lower()
            for item in tuple(self.cfg.media_server_service_names or ())
            if str(item or "").strip()
        }
        return bool(service_key and service_key in media_services)

    def _clear_router_labels(self, labels: dict[str, str]) -> None:
        prefix = str(self._edge_provider_spec().get("router_label_prefix") or "").strip()
        if not prefix:
            return
        for key in list(labels.keys()):
            if key.startswith(prefix):
                labels.pop(key, None)

    def _apply_edge_routing_labels(self, service_name: str, labels: dict[str, str]) -> None:
        if not self._is_edge_router_service(labels):
            return
        spec = self._edge_provider_spec()
        strategy = self.route_strategy()
        is_media_server = self._is_media_server_service(service_name)
        gateway_host = str(self.cfg.app_gateway_host or "").strip()

        if strategy == "path-prefix" and gateway_host and not is_media_server:
            self._clear_router_labels(labels)

        if strategy in {"path-prefix", "hybrid"} and gateway_host:
            path_router = f"{service_name}-path"
            path_prefix = self._path_route_prefix(service_name)
            strip_name = f"{service_name}-stripprefix"
            router_rule_key_template = str(spec.get("router_rule_key_template") or "").strip()
            router_service_key_template = str(spec.get("router_service_key_template") or "").strip()
            strip_prefix_key_template = str(spec.get("strip_prefix_key_template") or "").strip()
            path_rule_template = str(spec.get("path_rule_template") or "").strip()

            rule_key = self._format_template(router_rule_key_template, router_name=path_router)
            if rule_key and path_rule_template:
                labels[rule_key] = self._format_template(
                    path_rule_template,
                    gateway_host=gateway_host,
                    path_prefix=path_prefix,
                    service_name=service_name,
                    router_name=path_router,
                )

            service_key = self._format_template(
                router_service_key_template,
                router_name=path_router,
            )
            if service_key:
                labels[service_key] = service_name

            strip_key = self._format_template(
                strip_prefix_key_template,
                middleware_name=strip_name,
                service_name=service_name,
            )
            if strip_key:
                labels[strip_key] = path_prefix

            self._apply_router_middleware(labels, path_router, strip_name)

        if is_media_server:
            direct_host = str(self.cfg.media_server_direct_host or "").strip()
            if direct_host:
                media_rule_key_template = str(
                    spec.get("media_server_rule_key_template") or ""
                ).strip()
                media_rule_template = str(spec.get("direct_host_rule_template") or "").strip()
                media_rule_key = self._format_template(
                    media_rule_key_template,
                    service_name=service_name,
                )
                if media_rule_key and media_rule_template:
                    labels[media_rule_key] = self._format_template(
                        media_rule_template,
                        direct_host=direct_host,
                        service_name=service_name,
                    )

    def _auth_middleware(self) -> str:
        explicit = str(self.cfg.auth_middleware or "").strip()
        if explicit:
            return explicit
        provider = str(self.cfg.auth_provider or "").strip().lower()
        defaults = {
            str(key or "").strip().lower(): str(value or "").strip()
            for key, value in dict(self.cfg.auth_provider_middleware_defaults or {}).items()
            if str(key or "").strip()
        }
        return str(defaults.get(provider) or "").strip()

    def _apply_auth_labels(self, service_name: str, labels: dict[str, str]) -> None:
        if not bool(self.cfg.internet_exposed):
            return
        middleware = self._auth_middleware()
        if not middleware:
            return
        if self._is_media_server_service(service_name):
            # Media server keeps direct-app connectivity with native auth for TV/mobile clients.
            return
        for router_name in sorted(self._router_names(labels)):
            self._apply_router_middleware(labels, router_name, middleware)

    def normalize_labels(self, service_name: str, spec: dict[str, Any]) -> dict[str, str]:
        labels: dict[str, str] = {}
        raw_labels = spec.get("labels")
        if isinstance(raw_labels, dict):
            for key, value in raw_labels.items():
                labels[str(key)] = str(value)
        elif isinstance(raw_labels, list):
            for item in raw_labels:
                token = str(item or "").strip()
                if "=" not in token:
                    continue
                key, _, value = token.partition("=")
                labels[key.strip()] = value
        labels.setdefault("com.docker.compose.project", self.cfg.project_name)
        labels.setdefault("com.docker.compose.service", service_name)
        self._apply_edge_routing_labels(service_name, labels)
        self._apply_auth_labels(service_name, labels)
        return labels
