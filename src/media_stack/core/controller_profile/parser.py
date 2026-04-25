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



class ProfileParserService:
    def parse_profile_dict(self,
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

        meta = self._parse_metadata_and_resources(payload, active_catalog)
        install_profile = _resolve_install_profile(payload.get("install_profile"), active_catalog)
        install_apps = self._build_install_apps(payload, install_profile, active_catalog)
        boot = self._parse_bootstrap_flags(payload, install_profile, active_catalog)
        exposure = self._build_exposure_settings(payload, meta["stack_name"], active_catalog)
        auth_provider, auth_middleware = self._parse_auth_block(payload, active_catalog)
        chaos_settings = self._parse_chaos_block(payload, active_catalog)
        live_tv = self._parse_live_tv_defaults(payload, active_catalog)

        return cls(
            deployment_target=meta["deployment_target"],
            purpose=meta["purpose_token"],
            stack_name=meta["stack_name"],
            disk_allocation_gb=meta["disk_allocation_gb"],
            network_cidr=meta["network_cidr"],
            install_profile=install_profile,
            install_apps=install_apps,
            app_catalog=active_catalog.app_keys,
            preconfigure_apps=boot["preconfigure_apps"],
            preconfigure_api_keys=boot["preconfigure_api_keys"],
            apply_initial_preferences=boot["apply_initial_preferences"],
            auto_download_content=boot["auto_download_content"],
            live_tv_tuner_urls=live_tv["tuner_urls"],
            live_tv_guide_urls=live_tv["guide_urls"],
            live_tv_default_program_icon_url=live_tv["default_program_icon_url"],
            exposure=ControllerExposureSettings(
                **exposure,
                auth_provider=auth_provider,
                auth_middleware=auth_middleware,
            ),
            chaos=ControllerChaosSettings(**chaos_settings),
            source_path=source_path,
        )

    @staticmethod
    def _parse_metadata_and_resources(
        payload: dict[str, Any], catalog: ControllerProfileCatalog,
    ) -> dict[str, Any]:
        """Validate ``metadata`` + ``resources`` and return derived fields.

        Collects all the required scalar fields that feed straight into
        the top level of the profile; failing here short-circuits the
        rest of the parse.
        """
        metadata = payload.get("metadata")
        if not isinstance(metadata, dict):
            raise ValueError("metadata must be an object")
        resources = payload.get("resources")
        if not isinstance(resources, dict):
            raise ValueError("resources must be an object")
        stack_name = str(metadata.get("name") or "").strip()
        if not stack_name:
            raise ValueError("metadata.name is required")
        deployment_target = _normalize_deployment_target(metadata.get("platform"), catalog)
        purpose_token = _normalize_purpose(metadata.get("purpose"), catalog)
        disk_allocation_gb = _parse_storage_gb(resources.get("disk_space_gb"))
        if disk_allocation_gb < _MIN_PROFILE_DISK_ALLOCATION_GB:
            raise ValueError(
                "resources.disk_space_gb must be at least "
                f"{_MIN_PROFILE_DISK_ALLOCATION_GB}GB"
            )
        network_cidr = _parse_private_network_cidr(resources.get("network_cidr"))
        return {
            "stack_name": stack_name,
            "deployment_target": deployment_target,
            "purpose_token": purpose_token,
            "disk_allocation_gb": disk_allocation_gb,
            "network_cidr": network_cidr,
        }

    @staticmethod
    def _build_install_apps(
        payload: dict[str, Any],
        install_profile: str,
        catalog: ControllerProfileCatalog,
    ) -> dict[str, bool]:
        """Seed install_apps from the profile, then apply per-app overrides."""
        install_apps = _install_apps_for_profile(install_profile, catalog)
        app_overrides = payload.get("apps")
        if app_overrides is None:
            return install_apps
        if not isinstance(app_overrides, dict):
            raise ValueError("apps must be an object when provided")
        for raw_key, raw_value in app_overrides.items():
            app_key = _normalize_app_name(raw_key, catalog)
            if app_key not in catalog.app_key_set:
                raise ValueError(f"Unsupported app key under apps: '{raw_key}'")
            install_apps[app_key] = _as_bool(
                raw_value,
                default=install_apps.get(app_key, False),
                catalog=catalog,
            )
        return install_apps

    @staticmethod
    def _parse_bootstrap_flags(
        payload: dict[str, Any],
        install_profile: str,
        catalog: ControllerProfileCatalog,
    ) -> dict[str, bool]:
        """Return the four bootstrap flags with correct per-profile defaults."""
        bootstrap = payload.get("bootstrap")
        if bootstrap is not None and not isinstance(bootstrap, dict):
            raise ValueError("bootstrap must be an object when provided")
        bootstrap = bootstrap or {}
        return {
            "preconfigure_apps": _as_bool(
                bootstrap.get("preconfigure_apps"), default=True, catalog=catalog,
            ),
            "preconfigure_api_keys": _as_bool(
                bootstrap.get("preconfigure_api_keys"), default=True, catalog=catalog,
            ),
            "apply_initial_preferences": _as_bool(
                bootstrap.get("apply_initial_preferences"), default=True, catalog=catalog,
            ),
            "auto_download_content": _as_bool(
                bootstrap.get("auto_download_content"),
                default=(install_profile == "full"),
                catalog=catalog,
            ),
        }

    @classmethod
    def _build_exposure_settings(
        cls,
        payload: dict[str, Any],
        stack_name: str,
        catalog: ControllerProfileCatalog,
    ) -> dict[str, Any]:
        """Return the routing-derived exposure fields as a dict.

        Auth-related fields are resolved separately by ``_parse_auth_block``
        so this helper stays focused on host/port/path routing concerns.
        """
        routing = payload.get("routing")
        if routing is not None and not isinstance(routing, dict):
            raise ValueError("routing must be an object when provided")
        routing = routing or {}
        route_strategy = _normalize_route_strategy(
            routing.get("strategy") or "subdomain", catalog,
        )
        edge_router_provider = cls._validate_edge_router_provider(routing)
        internet_exposed = _as_bool(
            routing.get("internet_exposed"), default=False, catalog=catalog,
        )
        base_domain, stack_subdomain = cls._resolve_base_and_subdomain(
            routing, stack_name,
        )
        app_path_prefix = cls._normalize_app_path_prefix(routing.get("app_path_prefix"))
        gateway_host, gateway_port = cls._resolve_gateway_endpoint(
            routing, route_strategy, stack_subdomain, base_domain,
        )
        media_server_direct_host = cls._resolve_media_direct_host(
            routing, stack_subdomain, base_domain,
        )
        return {
            "internet_exposed": internet_exposed,
            "route_strategy": route_strategy,
            "edge_router_provider": edge_router_provider,
            "base_domain": base_domain,
            "stack_subdomain": stack_subdomain,
            "gateway_host": gateway_host,
            "gateway_port": gateway_port,
            "app_path_prefix": app_path_prefix,
            "media_server_direct_host": media_server_direct_host,
        }

    @staticmethod
    def _resolve_base_and_subdomain(
        routing: dict[str, Any], stack_name: str,
    ) -> tuple[str, str]:
        """Return (base_domain, stack_subdomain); empty subdomain allowed."""
        base_domain = _normalize_host(routing.get("base_domain") or "local")
        if not base_domain:
            raise ValueError("routing.base_domain must be a non-empty string")
        raw_subdomain = routing.get("stack_subdomain")
        stack_subdomain = (
            _normalize_host(raw_subdomain)
            if raw_subdomain is not None
            else _normalize_host(stack_name)
        )
        return base_domain, stack_subdomain

    @staticmethod
    def _resolve_gateway_endpoint(
        routing: dict[str, Any],
        route_strategy: str,
        stack_subdomain: str,
        base_domain: str,
    ) -> tuple[str, int | None]:
        """Fill in the gateway host when strategy needs one but none was given."""
        gateway_host = _normalize_host(routing.get("gateway_host"))
        if not gateway_host and route_strategy in {"path-prefix", "hybrid"}:
            gateway_host = _join_host("apps", stack_subdomain, base_domain)
        gateway_port = _normalize_optional_port(
            routing.get("gateway_port"), field_name="routing.gateway_port",
        )
        return gateway_host, gateway_port

    @staticmethod
    def _resolve_media_direct_host(
        routing: dict[str, Any], stack_subdomain: str, base_domain: str,
    ) -> str:
        """Derive ``media.<subdomain>.<domain>`` when no explicit value is set."""
        direct_hosts = routing.get("direct_hosts")
        if direct_hosts is not None and not isinstance(direct_hosts, dict):
            raise ValueError("routing.direct_hosts must be an object when provided")
        direct_hosts = direct_hosts or {}
        media_server_direct_host = _normalize_host(direct_hosts.get("media_server"))
        if not media_server_direct_host:
            media_server_direct_host = _join_host("media", stack_subdomain, base_domain)
        return media_server_direct_host

    @staticmethod
    def _validate_edge_router_provider(routing: dict[str, Any]) -> str:
        """Empty string means "use default"; otherwise verify against registry."""
        edge_router_provider = str(routing.get("provider") or "").strip().lower()
        if not edge_router_provider:
            return ""
        valid_edge_router_providers = {
            str(spec.key or "").strip().lower()
            for spec in load_builtin_edge_router_provider_specs()
            if str(spec.key or "").strip()
        }
        if edge_router_provider not in valid_edge_router_providers:
            allowed = ", ".join(sorted(valid_edge_router_providers))
            raise ValueError(f"routing.provider must be one of: {allowed}")
        return edge_router_provider

    @staticmethod
    def _normalize_app_path_prefix(raw: Any) -> str:
        """Ensure the prefix starts with ``/`` and has no trailing slash."""
        app_path_prefix = str(raw or "/app").strip()
        if not app_path_prefix:
            app_path_prefix = "/app"
        if not app_path_prefix.startswith("/"):
            app_path_prefix = f"/{app_path_prefix}"
        return app_path_prefix.rstrip("/") or "/app"

    @staticmethod
    def _parse_auth_block(
        payload: dict[str, Any], catalog: ControllerProfileCatalog,
    ) -> tuple[str, str]:
        """Return (auth_provider, auth_middleware) resolved against the catalog."""
        auth = payload.get("auth")
        if auth is not None and not isinstance(auth, dict):
            raise ValueError("auth must be an object when provided")
        auth = auth or {}
        auth_enabled = _as_bool(auth.get("enabled"), default=False, catalog=catalog)
        auth_provider = (
            str(auth.get("provider") or catalog.auth_disabled_provider).strip().lower()
        )
        if not auth_enabled:
            auth_provider = catalog.auth_disabled_provider
        if auth_provider not in set(catalog.auth_providers):
            allowed = ", ".join(catalog.auth_providers)
            raise ValueError(f"auth.provider must be one of: {allowed}")
        auth_middleware = str(auth.get("middleware") or "").strip()
        if not auth_middleware:
            auth_middleware = str(
                catalog.auth_provider_middleware_defaults.get(auth_provider) or ""
            ).strip()
        return auth_provider, auth_middleware

    @staticmethod
    def _parse_chaos_block(
        payload: dict[str, Any], catalog: ControllerProfileCatalog,
    ) -> dict[str, Any]:
        """Resolve the chaos settings with catalog-driven defaults and bounds."""
        chaos = payload.get("chaos")
        if chaos is not None and not isinstance(chaos, dict):
            raise ValueError("chaos must be an object when provided")
        chaos = chaos or {}
        return {
            "enabled": _as_bool(
                chaos.get("enabled"),
                default=catalog.chaos_default_enabled,
                catalog=catalog,
            ),
            "duration_minutes": _to_positive_int(
                chaos.get("duration_minutes"),
                default=catalog.chaos_default_duration_minutes,
                field_name="chaos.duration_minutes",
                minimum=1, maximum=120,
            ),
            "interval_seconds": _to_positive_int(
                chaos.get("interval_seconds"),
                default=catalog.chaos_default_interval_seconds,
                field_name="chaos.interval_seconds",
                minimum=0, maximum=3600,
            ),
            "actions": _normalize_chaos_actions(
                chaos.get("actions"),
                allowed=catalog.chaos_allowed_actions,
                default=catalog.chaos_default_actions,
            ),
        }

    @staticmethod
    def _parse_live_tv_defaults(
        payload: dict[str, Any], catalog: ControllerProfileCatalog,
    ) -> dict[str, Any]:
        """Resolve Live TV URLs falling back to catalog defaults when absent."""
        live_tv_defaults = payload.get("live_tv_defaults")
        if live_tv_defaults is not None and not isinstance(live_tv_defaults, dict):
            raise ValueError("live_tv_defaults must be an object when provided")
        live_tv_defaults = live_tv_defaults or {}
        tuner_urls = (
            _coerce_url_list(live_tv_defaults.get("playlists"))
            or _coerce_url_list(live_tv_defaults.get("tuner_urls"))
            or _coerce_url_list(live_tv_defaults.get("tuner_url"))
            or catalog.live_tv_tuner_urls
        )
        guide_urls = (
            _coerce_url_list(live_tv_defaults.get("guides"))
            or _coerce_url_list(live_tv_defaults.get("guide_urls"))
            or _coerce_url_list(live_tv_defaults.get("guide_url"))
            or catalog.live_tv_guide_urls
        )
        icon_urls = _coerce_url_list(
            live_tv_defaults.get("default_program_icon_urls")
            or live_tv_defaults.get("default_program_icon_url")
        )
        default_program_icon_url = str(
            icon_urls[0] if icon_urls else catalog.live_tv_default_program_icon_url
        ).strip()
        if not default_program_icon_url:
            default_program_icon_url = catalog.live_tv_default_program_icon_url
        return {
            "tuner_urls": tuner_urls,
            "guide_urls": guide_urls,
            "default_program_icon_url": default_program_icon_url,
        }
    
    
    def parse_profile_yaml_file(self, 
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
    
    
    def maybe_load_bootstrap_profile(self, path: Path | None) -> ControllerProfileConfig | None:
        if path is None:
            return None
        if not path.exists():
            raise ValueError(f"Bootstrap profile file not found: {path}")
        return ControllerProfileConfig.from_yaml_file(path)
    
    
    def normalize_selected_apps_csv(self, value: str) -> str:
        catalog = load_bootstrap_profile_catalog()
        apps = _split_app_csv(value, catalog)
        unknown = [app for app in apps if app not in catalog.app_key_set]
        if unknown:
            raise ValueError(f"Unsupported app(s) in selected apps: {', '.join(sorted(set(unknown)))}")
        return ",".join(apps)


_instance = ProfileParserService()
parse_profile_dict = _instance.parse_profile_dict
parse_profile_yaml_file = _instance.parse_profile_yaml_file
maybe_load_bootstrap_profile = _instance.maybe_load_bootstrap_profile
normalize_selected_apps_csv = _instance.normalize_selected_apps_csv
