"""Profile parsing: YAML/dict deserialization into ControllerProfileConfig."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from media_stack.core.edge.provider_registry import load_builtin_edge_router_provider_specs
from media_stack.core.controller_profile.catalog_loader import load_bootstrap_profile_catalog
from media_stack.core.controller_profile.models import (
    ControllerChaosSettings,
    ControllerExposureSettings,
    ControllerProfileCatalog,
    ControllerProfileConfig,
)
from media_stack.core.controller_profile.normalizers import (
    _as_bool,
    _coerce_url_list,
    _install_apps_for_profile,
    _normalize_app_name,
    _normalize_chaos_actions,
    _normalize_deployment_target,
    _normalize_host,
    _normalize_optional_port,
    _normalize_purpose,
    _normalize_route_strategy,
    _join_host,
    _parse_private_network_cidr,
    _parse_storage_gb,
    _resolve_install_profile,
    _split_app_csv,
    _to_positive_int,
)

_MIN_PROFILE_DISK_ALLOCATION_GB = 20


def parse_profile_dict(
    cls: type[ControllerProfileConfig],
    payload: dict[str, Any],
    *,
    source_path: Path | None = None,
    catalog: ControllerProfileCatalog | None = None,
) -> ControllerProfileConfig:
    """Core parsing logic extracted from ControllerProfileConfig.from_dict."""
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
    if disk_allocation_gb < _MIN_PROFILE_DISK_ALLOCATION_GB:
        raise ValueError(
            "resources.disk_space_gb must be at least " f"{_MIN_PROFILE_DISK_ALLOCATION_GB}GB"
        )
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
    edge_router_provider = str(routing.get("provider") or "").strip().lower()
    if edge_router_provider:
        valid_edge_router_providers = {
            str(spec.key or "").strip().lower()
            for spec in load_builtin_edge_router_provider_specs()
            if str(spec.key or "").strip()
        }
        if edge_router_provider not in valid_edge_router_providers:
            allowed = ", ".join(sorted(valid_edge_router_providers))
            raise ValueError(f"routing.provider must be one of: {allowed}")
    internet_exposed = _as_bool(
        routing.get("internet_exposed"),
        default=False,
        catalog=active_catalog,
    )
    base_domain = _normalize_host(routing.get("base_domain") or "local")
    if not base_domain:
        raise ValueError("routing.base_domain must be a non-empty string")
    raw_subdomain = routing.get("stack_subdomain")
    if raw_subdomain is not None:
        # Explicit value in profile -- empty string means no subdomain.
        stack_subdomain = _normalize_host(raw_subdomain)
    else:
        # Not specified -- derive from metadata.name.
        stack_subdomain = _normalize_host(stack_name)

    app_path_prefix = str(routing.get("app_path_prefix") or "/app").strip()
    if not app_path_prefix:
        app_path_prefix = "/app"
    if not app_path_prefix.startswith("/"):
        app_path_prefix = f"/{app_path_prefix}"
    app_path_prefix = app_path_prefix.rstrip("/") or "/app"

    gateway_host = _normalize_host(routing.get("gateway_host"))
    if not gateway_host and route_strategy in {"path-prefix", "hybrid"}:
        gateway_host = _join_host("apps", stack_subdomain, base_domain)
    gateway_port = _normalize_optional_port(
        routing.get("gateway_port"),
        field_name="routing.gateway_port",
    )

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

    chaos = payload.get("chaos")
    if chaos is not None and not isinstance(chaos, dict):
        raise ValueError("chaos must be an object when provided")
    chaos = chaos or {}
    chaos_enabled = _as_bool(
        chaos.get("enabled"),
        default=active_catalog.chaos_default_enabled,
        catalog=active_catalog,
    )
    chaos_duration_minutes = _to_positive_int(
        chaos.get("duration_minutes"),
        default=active_catalog.chaos_default_duration_minutes,
        field_name="chaos.duration_minutes",
        minimum=1,
        maximum=120,
    )
    chaos_interval_seconds = _to_positive_int(
        chaos.get("interval_seconds"),
        default=active_catalog.chaos_default_interval_seconds,
        field_name="chaos.interval_seconds",
        minimum=0,
        maximum=3600,
    )
    chaos_actions = _normalize_chaos_actions(
        chaos.get("actions"),
        allowed=active_catalog.chaos_allowed_actions,
        default=active_catalog.chaos_default_actions,
    )

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
    live_tv_icon_urls = _coerce_url_list(
        live_tv_defaults.get("default_program_icon_urls")
        or live_tv_defaults.get("default_program_icon_url")
    )
    live_tv_default_program_icon_url = str(
        live_tv_icon_urls[0]
        if live_tv_icon_urls
        else active_catalog.live_tv_default_program_icon_url
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
        exposure=ControllerExposureSettings(
            internet_exposed=internet_exposed,
            route_strategy=route_strategy,
            edge_router_provider=edge_router_provider,
            base_domain=base_domain,
            stack_subdomain=stack_subdomain,
            gateway_host=gateway_host,
            gateway_port=gateway_port,
            app_path_prefix=app_path_prefix,
            media_server_direct_host=media_server_direct_host,
            auth_provider=auth_provider,
            auth_middleware=auth_middleware,
        ),
        chaos=ControllerChaosSettings(
            enabled=chaos_enabled,
            duration_minutes=chaos_duration_minutes,
            interval_seconds=chaos_interval_seconds,
            actions=chaos_actions,
        ),
        source_path=source_path,
    )


def parse_profile_yaml_file(
    cls: type[ControllerProfileConfig],
    path: Path,
    *,
    catalog: ControllerProfileCatalog | None = None,
) -> ControllerProfileConfig:
    """Core YAML-file parsing logic extracted from ControllerProfileConfig.from_yaml_file."""
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if payload is None:
        payload = {}
    if not isinstance(payload, dict):
        raise ValueError("Bootstrap profile YAML must contain an object at root")
    return parse_profile_dict(cls, payload, source_path=path, catalog=catalog)


def maybe_load_bootstrap_profile(path: Path | None) -> ControllerProfileConfig | None:
    if path is None:
        return None
    if not path.exists():
        raise ValueError(f"Bootstrap profile file not found: {path}")
    return ControllerProfileConfig.from_yaml_file(path)


def normalize_selected_apps_csv(value: str) -> str:
    catalog = load_bootstrap_profile_catalog()
    apps = _split_app_csv(value, catalog)
    unknown = [app for app in apps if app not in catalog.app_key_set]
    if unknown:
        raise ValueError(f"Unsupported app(s) in selected apps: {', '.join(sorted(set(unknown)))}")
    return ",".join(apps)
