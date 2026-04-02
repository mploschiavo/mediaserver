"""Bootstrap profile model for distribution-friendly deployment defaults."""

from __future__ import annotations

import ipaddress
import os
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

_DEFAULT_PROFILE_CATALOG_PATH = (
    Path(__file__).resolve().parents[2] / "bootstrap" / "media-stack.bootstrap.catalog.yaml"
)


@dataclass(frozen=True)
class BootstrapProfileCatalog:
    deployment_aliases: dict[str, str]
    purpose_values: tuple[str, ...]
    route_strategy_aliases: dict[str, str]
    auth_providers: tuple[str, ...]
    auth_disabled_provider: str
    auth_provider_middleware_defaults: dict[str, str]
    app_keys: tuple[str, ...]
    app_aliases: dict[str, str]
    install_profiles: dict[str, tuple[str, ...]]
    bool_true_tokens: tuple[str, ...]
    bool_false_tokens: tuple[str, ...]
    live_tv_tuner_urls: tuple[str, ...]
    live_tv_guide_urls: tuple[str, ...]
    live_tv_default_program_icon_url: str

    @property
    def app_key_set(self) -> set[str]:
        return set(self.app_keys)


@dataclass(frozen=True)
class BootstrapExposureSettings:
    internet_exposed: bool = False
    route_strategy: str = "subdomain"
    base_domain: str = "local"
    stack_subdomain: str = "media-stack"
    gateway_host: str = ""
    app_path_prefix: str = "/app"
    media_server_direct_host: str = ""
    auth_provider: str = ""
    auth_middleware: str = ""

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
    app_catalog: tuple[str, ...] = field(default_factory=tuple)
    preconfigure_apps: bool = True
    preconfigure_api_keys: bool = True
    apply_initial_preferences: bool = True
    auto_download_content: bool = False
    live_tv_tuner_urls: tuple[str, ...] = field(default_factory=tuple)
    live_tv_guide_urls: tuple[str, ...] = field(default_factory=tuple)
    live_tv_default_program_icon_url: str = ""
    exposure: BootstrapExposureSettings = field(default_factory=BootstrapExposureSettings)
    source_path: Path | None = None

    @property
    def enabled_apps(self) -> tuple[str, ...]:
        keys = self.app_catalog or tuple(self.install_apps.keys())
        return tuple(app_name for app_name in keys if bool(self.install_apps.get(app_name, False)))

    @property
    def selected_apps_csv(self) -> str:
        return ",".join(self.enabled_apps)

    @classmethod
    def from_dict(
        cls,
        payload: dict[str, Any],
        *,
        source_path: Path | None = None,
        catalog: BootstrapProfileCatalog | None = None,
    ) -> "BootstrapProfileConfig":
        if not isinstance(payload, dict):
            raise ValueError("Bootstrap profile root must be an object")

        active_catalog = catalog or load_bootstrap_profile_catalog()

        metadata = payload.get("metadata")
        if not isinstance(metadata, dict):
            raise ValueError("metadata must be an object")
        resources = payload.get("resources")
        if not isinstance(resources, dict):
            raise ValueError("resources must be an object")

        stack_name = str(metadata.get("name") or "").strip()
        if not stack_name:
            raise ValueError("metadata.name is required")

        deployment_target = _normalize_deployment_target(metadata.get("platform"), active_catalog)
        purpose_token = _normalize_purpose(metadata.get("purpose"), active_catalog)

        disk_allocation_gb = _parse_storage_gb(resources.get("disk_space_gb"))
        if disk_allocation_gb < 200:
            raise ValueError("resources.disk_space_gb must be at least 200GB")
        network_cidr = _parse_private_network_cidr(resources.get("network_cidr"))

        install_profile = _resolve_install_profile(payload.get("install_profile"), active_catalog)
        install_apps = _install_apps_for_profile(install_profile, active_catalog)

        app_overrides = payload.get("apps")
        if app_overrides is not None:
            if not isinstance(app_overrides, dict):
                raise ValueError("apps must be an object when provided")
            for raw_key, raw_value in app_overrides.items():
                app_key = _normalize_app_name(raw_key, active_catalog)
                if app_key not in active_catalog.app_key_set:
                    raise ValueError(f"Unsupported app key under apps: '{raw_key}'")
                install_apps[app_key] = _as_bool(
                    raw_value,
                    default=install_apps.get(app_key, False),
                    catalog=active_catalog,
                )

        bootstrap = payload.get("bootstrap")
        if bootstrap is not None and not isinstance(bootstrap, dict):
            raise ValueError("bootstrap must be an object when provided")
        bootstrap = bootstrap or {}
        preconfigure_apps = _as_bool(
            bootstrap.get("preconfigure_apps"), default=True, catalog=active_catalog
        )
        preconfigure_api_keys = _as_bool(
            bootstrap.get("preconfigure_api_keys"), default=True, catalog=active_catalog
        )
        apply_initial_preferences = _as_bool(
            bootstrap.get("apply_initial_preferences"), default=True, catalog=active_catalog
        )
        auto_download_content = _as_bool(
            bootstrap.get("auto_download_content"),
            default=(install_profile == "full"),
            catalog=active_catalog,
        )

        routing = payload.get("routing")
        if routing is not None and not isinstance(routing, dict):
            raise ValueError("routing must be an object when provided")
        routing = routing or {}
        route_strategy = _normalize_route_strategy(
            routing.get("strategy") or "subdomain",
            active_catalog,
        )
        internet_exposed = _as_bool(
            routing.get("internet_exposed"),
            default=False,
            catalog=active_catalog,
        )
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
        media_server_direct_host = _normalize_host(direct_hosts.get("media_server"))
        if not media_server_direct_host:
            media_server_direct_host = _join_host("media", stack_subdomain, base_domain)

        auth = payload.get("auth")
        if auth is not None and not isinstance(auth, dict):
            raise ValueError("auth must be an object when provided")
        auth = auth or {}
        auth_enabled = _as_bool(auth.get("enabled"), default=False, catalog=active_catalog)
        auth_provider = (
            str(auth.get("provider") or active_catalog.auth_disabled_provider).strip().lower()
        )
        if not auth_enabled:
            auth_provider = active_catalog.auth_disabled_provider
        if auth_provider not in set(active_catalog.auth_providers):
            allowed = ", ".join(active_catalog.auth_providers)
            raise ValueError(f"auth.provider must be one of: {allowed}")
        auth_middleware = str(auth.get("middleware") or "").strip()
        if not auth_middleware:
            auth_middleware = str(
                active_catalog.auth_provider_middleware_defaults.get(auth_provider) or ""
            ).strip()

        live_tv_defaults = payload.get("live_tv_defaults")
        if live_tv_defaults is not None and not isinstance(live_tv_defaults, dict):
            raise ValueError("live_tv_defaults must be an object when provided")
        live_tv_defaults = live_tv_defaults or {}
        live_tv_tuner_urls = (
            _coerce_url_list(live_tv_defaults.get("playlists"))
            or _coerce_url_list(live_tv_defaults.get("tuner_urls"))
            or _coerce_url_list(live_tv_defaults.get("tuner_url"))
            or active_catalog.live_tv_tuner_urls
        )
        live_tv_guide_urls = (
            _coerce_url_list(live_tv_defaults.get("guides"))
            or _coerce_url_list(live_tv_defaults.get("guide_urls"))
            or _coerce_url_list(live_tv_defaults.get("guide_url"))
            or active_catalog.live_tv_guide_urls
        )
        live_tv_default_program_icon_url = str(
            live_tv_defaults.get("default_program_icon_url")
            or active_catalog.live_tv_default_program_icon_url
        ).strip()
        if not live_tv_default_program_icon_url:
            live_tv_default_program_icon_url = active_catalog.live_tv_default_program_icon_url

        return cls(
            deployment_target=deployment_target,
            purpose=purpose_token,
            stack_name=stack_name,
            disk_allocation_gb=disk_allocation_gb,
            network_cidr=network_cidr,
            install_profile=install_profile,
            install_apps=install_apps,
            app_catalog=active_catalog.app_keys,
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
                media_server_direct_host=media_server_direct_host,
                auth_provider=auth_provider,
                auth_middleware=auth_middleware,
            ),
            source_path=source_path,
        )

    @classmethod
    def from_yaml_file(
        cls,
        path: Path,
        *,
        catalog: BootstrapProfileCatalog | None = None,
    ) -> "BootstrapProfileConfig":
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
        if payload is None:
            payload = {}
        if not isinstance(payload, dict):
            raise ValueError("Bootstrap profile YAML must contain an object at root")
        return cls.from_dict(payload, source_path=path, catalog=catalog)


def _resolve_catalog_path(path: Path | None = None) -> Path:
    if path is not None:
        return path.expanduser()
    env_path = str(os.environ.get("BOOTSTRAP_PROFILE_CATALOG_FILE") or "").strip()
    if env_path:
        return Path(env_path).expanduser()
    return _DEFAULT_PROFILE_CATALOG_PATH


@lru_cache(maxsize=8)
def _load_bootstrap_profile_catalog_cached(path_token: str) -> BootstrapProfileCatalog:
    path = Path(path_token)
    if not path.exists():
        raise ValueError(f"Bootstrap profile catalog file not found: {path}")

    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if payload is None:
        payload = {}
    if not isinstance(payload, dict):
        raise ValueError("Bootstrap profile catalog must contain an object at root")

    deployment_aliases = _normalize_alias_dict(
        payload.get("deployment_aliases"),
        field_name="deployment_aliases",
    )
    purpose_values = _normalize_string_list(
        payload.get("purpose_values"),
        field_name="purpose_values",
    )
    route_strategy_aliases = _normalize_alias_dict(
        payload.get("route_strategy_aliases"),
        field_name="route_strategy_aliases",
    )
    auth_providers = _normalize_string_list(
        payload.get("auth_providers"),
        field_name="auth_providers",
    )
    auth_disabled_provider = str(payload.get("auth_disabled_provider") or "").strip().lower()
    if not auth_disabled_provider:
        auth_disabled_provider = auth_providers[0]
    if auth_disabled_provider not in set(auth_providers):
        raise ValueError(
            "auth_disabled_provider must be one of auth_providers "
            f"(got '{auth_disabled_provider}')"
        )
    raw_auth_defaults = payload.get("auth_provider_middleware_defaults")
    auth_provider_middleware_defaults: dict[str, str] = {}
    if raw_auth_defaults is not None:
        if not isinstance(raw_auth_defaults, dict):
            raise ValueError("auth_provider_middleware_defaults must be an object")
        for raw_provider, raw_middleware in raw_auth_defaults.items():
            provider_key = str(raw_provider or "").strip().lower()
            if not provider_key:
                continue
            if provider_key not in set(auth_providers):
                raise ValueError(
                    "auth_provider_middleware_defaults contains unknown provider "
                    f"'{provider_key}'"
                )
            auth_provider_middleware_defaults[provider_key] = str(raw_middleware or "").strip()
    for provider_key in auth_providers:
        auth_provider_middleware_defaults.setdefault(provider_key, "")

    apps_payload = payload.get("apps")
    if not isinstance(apps_payload, dict):
        raise ValueError("apps must be an object in bootstrap profile catalog")
    app_keys = _normalize_string_list(apps_payload.get("keys"), field_name="apps.keys")
    app_key_set = set(app_keys)

    raw_aliases = apps_payload.get("aliases")
    app_aliases: dict[str, str] = {}
    if raw_aliases is not None:
        if not isinstance(raw_aliases, dict):
            raise ValueError("apps.aliases must be an object when provided")
        for raw_key, raw_value in raw_aliases.items():
            alias_key = _normalize_app_token(raw_key)
            alias_target = _normalize_app_token(raw_value)
            if not alias_key or not alias_target:
                continue
            if alias_target not in app_key_set:
                raise ValueError(
                    f"apps.aliases contains unknown target '{raw_value}'. "
                    "Targets must be present in apps.keys."
                )
            app_aliases[alias_key] = alias_target

    install_profiles_payload = payload.get("install_profiles")
    if not isinstance(install_profiles_payload, dict) or not install_profiles_payload:
        raise ValueError("install_profiles must be a non-empty object")

    install_profiles: dict[str, tuple[str, ...]] = {}
    for raw_profile_name, raw_profile_spec in install_profiles_payload.items():
        profile_name = str(raw_profile_name or "").strip().lower()
        if not profile_name:
            continue

        enabled_raw: Any
        if isinstance(raw_profile_spec, dict):
            enabled_raw = raw_profile_spec.get("enabled_apps")
        else:
            enabled_raw = raw_profile_spec

        if isinstance(enabled_raw, str) and enabled_raw.strip() == "*":
            install_profiles[profile_name] = app_keys
            continue

        if not isinstance(enabled_raw, list):
            raise ValueError(f"install_profiles.{profile_name}.enabled_apps must be a list or '*'")

        enabled_list: list[str] = []
        seen: set[str] = set()
        for raw_app in enabled_raw:
            app_name = _normalize_app_token(raw_app)
            if not app_name:
                continue
            app_name = app_aliases.get(app_name, app_name)
            if app_name not in app_key_set:
                raise ValueError(
                    f"install_profiles.{profile_name}.enabled_apps contains unknown app '{raw_app}'"
                )
            if app_name in seen:
                continue
            seen.add(app_name)
            enabled_list.append(app_name)
        install_profiles[profile_name] = tuple(enabled_list)

    boolean_tokens = payload.get("boolean_tokens")
    if not isinstance(boolean_tokens, dict):
        raise ValueError("boolean_tokens must be an object")
    true_tokens_raw = boolean_tokens.get("true")
    if true_tokens_raw is None:
        true_tokens_raw = boolean_tokens.get(True)
    false_tokens_raw = boolean_tokens.get("false")
    if false_tokens_raw is None:
        false_tokens_raw = boolean_tokens.get(False)
    bool_true_tokens = _normalize_string_list(
        true_tokens_raw,
        field_name="boolean_tokens.true",
    )
    bool_false_tokens = _normalize_string_list(
        false_tokens_raw,
        field_name="boolean_tokens.false",
    )

    live_tv_defaults = payload.get("live_tv_defaults")
    if not isinstance(live_tv_defaults, dict):
        raise ValueError("live_tv_defaults must be an object")
    live_tv_tuner_urls = _coerce_url_list(
        live_tv_defaults.get("tuner_urls")
        or live_tv_defaults.get("tuner_url")
        or live_tv_defaults.get("playlists")
    )
    live_tv_guide_urls = _coerce_url_list(
        live_tv_defaults.get("guide_urls")
        or live_tv_defaults.get("guide_url")
        or live_tv_defaults.get("guides")
    )
    live_tv_default_program_icon_url = str(
        live_tv_defaults.get("default_program_icon_url") or ""
    ).strip()
    if not live_tv_tuner_urls:
        raise ValueError("live_tv_defaults must define at least one tuner URL")
    if not live_tv_guide_urls:
        raise ValueError("live_tv_defaults must define at least one guide URL")
    if not live_tv_default_program_icon_url:
        raise ValueError("live_tv_defaults.default_program_icon_url is required")

    return BootstrapProfileCatalog(
        deployment_aliases=deployment_aliases,
        purpose_values=purpose_values,
        route_strategy_aliases=route_strategy_aliases,
        auth_providers=auth_providers,
        auth_disabled_provider=auth_disabled_provider,
        auth_provider_middleware_defaults=auth_provider_middleware_defaults,
        app_keys=app_keys,
        app_aliases=app_aliases,
        install_profiles=install_profiles,
        bool_true_tokens=bool_true_tokens,
        bool_false_tokens=bool_false_tokens,
        live_tv_tuner_urls=live_tv_tuner_urls,
        live_tv_guide_urls=live_tv_guide_urls,
        live_tv_default_program_icon_url=live_tv_default_program_icon_url,
    )


def load_bootstrap_profile_catalog(path: Path | None = None) -> BootstrapProfileCatalog:
    resolved_path = _resolve_catalog_path(path)
    return _load_bootstrap_profile_catalog_cached(str(resolved_path))


def _normalize_alias_dict(value: Any, *, field_name: str) -> dict[str, str]:
    if not isinstance(value, dict) or not value:
        raise ValueError(f"{field_name} must be a non-empty object")
    out: dict[str, str] = {}
    for raw_key, raw_value in value.items():
        key = str(raw_key or "").strip().lower()
        normalized = str(raw_value or "").strip().lower()
        if key and normalized:
            out[key] = normalized
    if not out:
        raise ValueError(f"{field_name} must contain at least one mapping")
    return out


def _normalize_string_list(value: Any, *, field_name: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise ValueError(f"{field_name} must be a non-empty array")
    out: list[str] = []
    seen: set[str] = set()
    for raw in value:
        token = str(raw or "").strip().lower()
        if not token or token in seen:
            continue
        seen.add(token)
        out.append(token)
    if not out:
        raise ValueError(f"{field_name} must contain at least one value")
    return tuple(out)


def _normalize_app_token(value: Any) -> str:
    token = str(value or "").strip().lower()
    if not token:
        return ""
    return re.sub(r"[^a-z0-9]+", "", token)


def _normalize_app_name(value: Any, catalog: BootstrapProfileCatalog) -> str:
    token = _normalize_app_token(value)
    if not token:
        return ""
    return catalog.app_aliases.get(token, token)


def _as_bool(value: Any, *, default: bool, catalog: BootstrapProfileCatalog) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return bool(value)
    token = str(value).strip().lower()
    if not token:
        return default
    if token in set(catalog.bool_true_tokens):
        return True
    if token in set(catalog.bool_false_tokens):
        return False
    raise ValueError(f"Invalid boolean value '{value}'")


def _normalize_deployment_target(value: Any, catalog: BootstrapProfileCatalog) -> str:
    token = str(value or "").strip().lower()
    normalized = catalog.deployment_aliases.get(token, "")
    if not normalized:
        allowed = ", ".join(sorted(set(catalog.deployment_aliases.keys())))
        raise ValueError(f"metadata.platform must be one of: {allowed}")
    return normalized


def _normalize_purpose(value: Any, catalog: BootstrapProfileCatalog) -> str:
    token = str(value or "").strip().lower()
    if token not in set(catalog.purpose_values):
        allowed = ", ".join(catalog.purpose_values)
        raise ValueError(f"metadata.purpose must be one of: {allowed}")
    return token


def _normalize_route_strategy(value: Any, catalog: BootstrapProfileCatalog) -> str:
    token = str(value or "").strip().lower()
    normalized = catalog.route_strategy_aliases.get(token, "")
    if not normalized:
        allowed = ", ".join(sorted(set(catalog.route_strategy_aliases.keys())))
        raise ValueError(f"routing.strategy must be one of: {allowed}")
    return normalized


def _resolve_install_profile(value: Any, catalog: BootstrapProfileCatalog) -> str:
    token = str(value or "").strip().lower()
    if token not in catalog.install_profiles:
        allowed = ", ".join(sorted(catalog.install_profiles.keys()))
        raise ValueError(f"install_profile must be one of: {allowed}")
    return token


def _split_app_csv(value: str, catalog: BootstrapProfileCatalog) -> tuple[str, ...]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in str(value or "").split(","):
        token = _normalize_app_name(raw, catalog)
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


def _install_apps_for_profile(
    profile: str,
    catalog: BootstrapProfileCatalog,
) -> dict[str, bool]:
    enabled = set(catalog.install_profiles.get(profile) or ())
    return {app_name: app_name in enabled for app_name in catalog.app_keys}


def maybe_load_bootstrap_profile(path: Path | None) -> BootstrapProfileConfig | None:
    if path is None:
        return None
    if not path.exists():
        raise ValueError(f"Bootstrap profile file not found: {path}")
    return BootstrapProfileConfig.from_yaml_file(path)


def normalize_selected_apps_csv(value: str) -> str:
    catalog = load_bootstrap_profile_catalog()
    apps = _split_app_csv(value, catalog)
    unknown = [app for app in apps if app not in catalog.app_key_set]
    if unknown:
        raise ValueError(f"Unsupported app(s) in selected apps: {', '.join(sorted(set(unknown)))}")
    return ",".join(apps)
