"""Bootstrap profile model for distribution-friendly deployment defaults."""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

_DEPLOYMENT_ALIASES = {
    "k8s": "k8s",
    "kubernetes": "k8s",
    "microk8s": "k8s",
    "compose": "compose",
    "docker-compose": "compose",
    "docker_compose": "compose",
    "dockercompose": "compose",
}
_PURPOSE_VALUES = {"dev", "test", "prod"}
_ROUTE_STRATEGY_ALIASES = {
    "subdomain": "subdomain",
    "path-prefix": "path-prefix",
    "path_prefix": "path-prefix",
    "pathprefix": "path-prefix",
    "hybrid": "hybrid",
    "local": "subdomain",
}
_AUTH_PROVIDERS = {"none", "authelia", "authentik"}
_INSTALL_PROFILES = {"minimal", "standard", "full"}
_APP_KEYS = (
    "jellyfin",
    "jellyseerr",
    "sonarr",
    "radarr",
    "lidarr",
    "readarr",
    "bazarr",
    "prowlarr",
    "qbittorrent",
    "sabnzbd",
    "tautulli",
    "maintainerr",
    "unpackerr",
    "homepage",
)
_APP_ALIASES = {
    "mainainerr": "maintainerr",
}
_BOOL_TRUE = {"1", "true", "yes", "on", "y"}
_BOOL_FALSE = {"0", "false", "no", "off", "n"}

_MINIMAL_APPS_ENABLED = {
    "jellyfin",
    "jellyseerr",
    "prowlarr",
    "qbittorrent",
    "homepage",
}
_STANDARD_APPS_ENABLED = {
    *_MINIMAL_APPS_ENABLED,
    "sonarr",
    "radarr",
    "bazarr",
    "sabnzbd",
    "maintainerr",
    "unpackerr",
}

_DEFAULT_LIVE_TV_TUNER_URL = "https://iptv-org.github.io/iptv/countries/us.m3u"
_DEFAULT_LIVE_TV_GUIDE_URL = "https://iptv-epg.org/files/epg-us.xml"
_DEFAULT_LIVE_TV_ICON_URL = "https://raw.githubusercontent.com/iptv-org/logo/master/tv.png"


def _as_bool(value: Any, *, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return bool(value)
    token = str(value).strip().lower()
    if not token:
        return default
    if token in _BOOL_TRUE:
        return True
    if token in _BOOL_FALSE:
        return False
    raise ValueError(f"Invalid boolean value '{value}'")


def _normalize_deployment_target(value: Any) -> str:
    token = str(value or "").strip().lower()
    normalized = _DEPLOYMENT_ALIASES.get(token, "")
    if not normalized:
        raise ValueError(
            "metadata.platform must be one of: k8s, kubernetes, compose, dockercompose, docker-compose"
        )
    return normalized


def _normalize_purpose(value: Any) -> str:
    token = str(value or "").strip().lower()
    if token not in _PURPOSE_VALUES:
        raise ValueError("metadata.purpose must be one of: dev, test, prod")
    return token


def _normalize_route_strategy(value: Any) -> str:
    token = str(value or "").strip().lower()
    normalized = _ROUTE_STRATEGY_ALIASES.get(token, "")
    if not normalized:
        raise ValueError("routing.strategy must be one of: subdomain, path-prefix, hybrid, local")
    return normalized


def _resolve_install_profile(value: Any) -> str:
    token = str(value or "").strip().lower()
    if token not in _INSTALL_PROFILES:
        raise ValueError("install_profile must be one of: minimal, standard, full")
    return token


def _normalize_app_name(value: Any) -> str:
    token = str(value or "").strip().lower().replace(" ", "").replace("_", "")
    if not token:
        return ""
    return _APP_ALIASES.get(token, token)


def _split_app_csv(value: str) -> tuple[str, ...]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in str(value or "").split(","):
        token = _normalize_app_name(raw)
        if not token or token in seen:
            continue
        seen.add(token)
        out.append(token)
    return tuple(out)


def _parse_storage_gb(value: Any) -> int:
    if isinstance(value, int) and not isinstance(value, bool):
        return int(value)
    token = str(value or "").strip().lower().replace(" ", "")
    if not token:
        raise ValueError("resources.disk_space_gb is required")
    match = re.fullmatch(r"(?P<num>\d+(?:\.\d+)?)(?P<unit>gb|g|tb|t)?", token)
    if not match:
        raise ValueError(
            "resources.disk_space_gb must be an integer GB value or a value like 500GB/1TB"
        )
    magnitude = float(match.group("num"))
    unit = str(match.group("unit") or "gb")
    if unit in {"tb", "t"}:
        magnitude *= 1000.0
    return int(round(magnitude))


def _normalize_host(value: Any) -> str:
    return str(value or "").strip().lower().strip(".")


def _join_host(*parts: str) -> str:
    tokens = [str(item).strip().strip(".") for item in parts if str(item).strip().strip(".")]
    return ".".join(tokens).lower()


def _parse_private_network_cidr(value: Any) -> str:
    token = str(value or "").strip()
    if not token:
        raise ValueError("resources.network_cidr is required")
    try:
        network = ipaddress.ip_network(token, strict=False)
    except ValueError as exc:
        raise ValueError(f"Invalid resources.network_cidr '{token}'") from exc
    if not network.is_private:
        raise ValueError(
            f"Network CIDR '{token}' is not private. Use RFC1918 ranges (10/8, 172.16/12, 192.168/16)."
        )
    return str(network)


def _coerce_url_list(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        token = value.strip()
        return (token,) if token else ()
    if not isinstance(value, list):
        return ()
    out: list[str] = []
    for item in value:
        token = str(item or "").strip()
        if token:
            out.append(token)
    return tuple(out)


def _install_apps_for_profile(profile: str) -> dict[str, bool]:
    if profile == "minimal":
        return {app_name: app_name in _MINIMAL_APPS_ENABLED for app_name in _APP_KEYS}
    if profile == "standard":
        return {app_name: app_name in _STANDARD_APPS_ENABLED for app_name in _APP_KEYS}
    return {app_name: True for app_name in _APP_KEYS}


@dataclass(frozen=True)
class BootstrapExposureSettings:
    internet_exposed: bool = False
    route_strategy: str = "subdomain"
    base_domain: str = "local"
    stack_subdomain: str = "media-stack"
    gateway_host: str = ""
    app_path_prefix: str = "/app"
    jellyfin_direct_host: str = ""
    auth_provider: str = "none"
    auth_middleware: str = ""

    @property
    def media_server_direct_host(self) -> str:
        return str(self.jellyfin_direct_host or "").strip()

    @property
    def ingress_domain(self) -> str:
        return _join_host(self.stack_subdomain, self.base_domain)

    @property
    def normalized_app_path_prefix(self) -> str:
        token = str(self.app_path_prefix or "").strip()
        if not token:
            return "/app"
        if not token.startswith("/"):
            token = f"/{token}"
        token = token.rstrip("/")
        return token or "/app"


@dataclass(frozen=True)
class BootstrapProfileConfig:
    deployment_target: str
    purpose: str
    stack_name: str
    disk_allocation_gb: int
    network_cidr: str
    install_profile: str
    install_apps: dict[str, bool] = field(default_factory=dict)
    preconfigure_apps: bool = True
    preconfigure_api_keys: bool = True
    apply_initial_preferences: bool = True
    auto_download_content: bool = False
    live_tv_tuner_urls: tuple[str, ...] = field(default_factory=tuple)
    live_tv_guide_urls: tuple[str, ...] = field(default_factory=tuple)
    live_tv_default_program_icon_url: str = _DEFAULT_LIVE_TV_ICON_URL
    exposure: BootstrapExposureSettings = field(default_factory=BootstrapExposureSettings)
    source_path: Path | None = None

    @property
    def enabled_apps(self) -> tuple[str, ...]:
        return tuple(
            app_name for app_name in _APP_KEYS if bool(self.install_apps.get(app_name, False))
        )

    @property
    def selected_apps_csv(self) -> str:
        return ",".join(self.enabled_apps)

    @classmethod
    def from_dict(
        cls,
        payload: dict[str, Any],
        *,
        source_path: Path | None = None,
    ) -> "BootstrapProfileConfig":
        if not isinstance(payload, dict):
            raise ValueError("Bootstrap profile root must be an object")

        metadata = payload.get("metadata")
        if not isinstance(metadata, dict):
            raise ValueError("metadata must be an object")
        resources = payload.get("resources")
        if not isinstance(resources, dict):
            raise ValueError("resources must be an object")

        stack_name = str(metadata.get("name") or "").strip()
        if not stack_name:
            raise ValueError("metadata.name is required")

        deployment_target = _normalize_deployment_target(metadata.get("platform"))
        purpose_token = _normalize_purpose(metadata.get("purpose"))

        disk_allocation_gb = _parse_storage_gb(resources.get("disk_space_gb"))
        if disk_allocation_gb < 200:
            raise ValueError("resources.disk_space_gb must be at least 200GB")
        network_cidr = _parse_private_network_cidr(resources.get("network_cidr"))

        install_profile = _resolve_install_profile(payload.get("install_profile"))
        install_apps = _install_apps_for_profile(install_profile)

        app_overrides = payload.get("apps")
        if app_overrides is not None:
            if not isinstance(app_overrides, dict):
                raise ValueError("apps must be an object when provided")
            for raw_key, raw_value in app_overrides.items():
                app_key = _normalize_app_name(raw_key)
                if app_key not in _APP_KEYS:
                    raise ValueError(f"Unsupported app key under apps: '{raw_key}'")
                install_apps[app_key] = _as_bool(
                    raw_value, default=install_apps.get(app_key, False)
                )

        bootstrap = payload.get("bootstrap")
        if bootstrap is not None and not isinstance(bootstrap, dict):
            raise ValueError("bootstrap must be an object when provided")
        bootstrap = bootstrap or {}
        preconfigure_apps = _as_bool(bootstrap.get("preconfigure_apps"), default=True)
        preconfigure_api_keys = _as_bool(bootstrap.get("preconfigure_api_keys"), default=True)
        apply_initial_preferences = _as_bool(
            bootstrap.get("apply_initial_preferences"), default=True
        )
        auto_download_content = _as_bool(
            bootstrap.get("auto_download_content"), default=(install_profile == "full")
        )

        routing = payload.get("routing")
        if routing is not None and not isinstance(routing, dict):
            raise ValueError("routing must be an object when provided")
        routing = routing or {}
        route_strategy = _normalize_route_strategy(routing.get("strategy") or "subdomain")
        internet_exposed = _as_bool(routing.get("internet_exposed"), default=False)
        base_domain = _normalize_host(routing.get("base_domain") or "local")
        if not base_domain:
            raise ValueError("routing.base_domain must be a non-empty string")
        stack_subdomain = _normalize_host(routing.get("stack_subdomain") or stack_name)
        if not stack_subdomain:
            raise ValueError("routing.stack_subdomain resolved empty")

        app_path_prefix = str(routing.get("app_path_prefix") or "/app").strip()
        if not app_path_prefix:
            app_path_prefix = "/app"
        if not app_path_prefix.startswith("/"):
            app_path_prefix = f"/{app_path_prefix}"
        app_path_prefix = app_path_prefix.rstrip("/") or "/app"

        gateway_host = _normalize_host(routing.get("gateway_host"))
        if not gateway_host and route_strategy in {"path-prefix", "hybrid"}:
            gateway_host = _join_host("apps", stack_subdomain, base_domain)

        direct_hosts = routing.get("direct_hosts")
        if direct_hosts is not None and not isinstance(direct_hosts, dict):
            raise ValueError("routing.direct_hosts must be an object when provided")
        direct_hosts = direct_hosts or {}
        jellyfin_direct_host = _normalize_host(direct_hosts.get("jellyfin"))
        if not jellyfin_direct_host:
            jellyfin_direct_host = _join_host("jellyfin", stack_subdomain, base_domain)

        auth = payload.get("auth")
        if auth is not None and not isinstance(auth, dict):
            raise ValueError("auth must be an object when provided")
        auth = auth or {}
        auth_enabled = _as_bool(auth.get("enabled"), default=False)
        auth_provider = str(auth.get("provider") or "none").strip().lower()
        if not auth_enabled:
            auth_provider = "none"
        if auth_provider not in _AUTH_PROVIDERS:
            raise ValueError("auth.provider must be one of: none, authelia, authentik")
        auth_middleware = str(auth.get("middleware") or "").strip()
        if auth_provider == "authelia" and not auth_middleware:
            auth_middleware = "authelia@docker"
        if auth_provider == "authentik" and not auth_middleware:
            auth_middleware = "authentik@docker"
        if auth_provider == "none":
            auth_middleware = ""

        live_tv_defaults = payload.get("live_tv_defaults")
        if live_tv_defaults is not None and not isinstance(live_tv_defaults, dict):
            raise ValueError("live_tv_defaults must be an object when provided")
        live_tv_defaults = live_tv_defaults or {}
        live_tv_tuner_urls = (
            _coerce_url_list(live_tv_defaults.get("playlists"))
            or _coerce_url_list(live_tv_defaults.get("tuner_urls"))
            or _coerce_url_list(live_tv_defaults.get("tuner_url"))
            or (_DEFAULT_LIVE_TV_TUNER_URL,)
        )
        live_tv_guide_urls = (
            _coerce_url_list(live_tv_defaults.get("guides"))
            or _coerce_url_list(live_tv_defaults.get("guide_urls"))
            or _coerce_url_list(live_tv_defaults.get("guide_url"))
            or (_DEFAULT_LIVE_TV_GUIDE_URL,)
        )
        live_tv_default_program_icon_url = str(
            live_tv_defaults.get("default_program_icon_url") or _DEFAULT_LIVE_TV_ICON_URL
        ).strip()
        if not live_tv_default_program_icon_url:
            live_tv_default_program_icon_url = _DEFAULT_LIVE_TV_ICON_URL

        return cls(
            deployment_target=deployment_target,
            purpose=purpose_token,
            stack_name=stack_name,
            disk_allocation_gb=disk_allocation_gb,
            network_cidr=network_cidr,
            install_profile=install_profile,
            install_apps=install_apps,
            preconfigure_apps=preconfigure_apps,
            preconfigure_api_keys=preconfigure_api_keys,
            apply_initial_preferences=apply_initial_preferences,
            auto_download_content=auto_download_content,
            live_tv_tuner_urls=live_tv_tuner_urls,
            live_tv_guide_urls=live_tv_guide_urls,
            live_tv_default_program_icon_url=live_tv_default_program_icon_url,
            exposure=BootstrapExposureSettings(
                internet_exposed=internet_exposed,
                route_strategy=route_strategy,
                base_domain=base_domain,
                stack_subdomain=stack_subdomain,
                gateway_host=gateway_host,
                app_path_prefix=app_path_prefix,
                jellyfin_direct_host=jellyfin_direct_host,
                auth_provider=auth_provider,
                auth_middleware=auth_middleware,
            ),
            source_path=source_path,
        )

    @classmethod
    def from_yaml_file(cls, path: Path) -> "BootstrapProfileConfig":
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
        if payload is None:
            payload = {}
        if not isinstance(payload, dict):
            raise ValueError("Bootstrap profile YAML must contain an object at root")
        return cls.from_dict(payload, source_path=path)


def maybe_load_bootstrap_profile(path: Path | None) -> BootstrapProfileConfig | None:
    if path is None:
        return None
    if not path.exists():
        raise ValueError(f"Bootstrap profile file not found: {path}")
    return BootstrapProfileConfig.from_yaml_file(path)


def normalize_selected_apps_csv(value: str) -> str:
    apps = _split_app_csv(value)
    unknown = [app for app in apps if app not in _APP_KEYS]
    if unknown:
        raise ValueError(f"Unsupported app(s) in selected apps: {', '.join(sorted(set(unknown)))}")
    return ",".join(apps)
