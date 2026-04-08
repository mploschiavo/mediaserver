"""Dataclass models for bootstrap profile configuration."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from media_stack.core.controller_profile.normalizers import _join_host


@dataclass(frozen=True)
class ControllerProfileCatalog:
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
    chaos_default_enabled: bool
    chaos_default_duration_minutes: int
    chaos_default_interval_seconds: int
    chaos_allowed_actions: tuple[str, ...]
    chaos_default_actions: tuple[str, ...]
    live_tv_tuner_urls: tuple[str, ...]
    live_tv_guide_urls: tuple[str, ...]
    live_tv_default_program_icon_url: str

    @property
    def app_key_set(self) -> set[str]:
        return set(self.app_keys)


@dataclass(frozen=True)
class ControllerExposureSettings:
    internet_exposed: bool = False
    route_strategy: str = "subdomain"
    edge_router_provider: str = ""
    base_domain: str = "local"
    stack_subdomain: str = "media-stack"
    gateway_host: str = ""
    gateway_port: str = ""
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
class ControllerChaosSettings:
    enabled: bool = False
    duration_minutes: int = 5
    interval_seconds: int = 60
    actions: tuple[str, ...] = field(
        default_factory=lambda: ("restart_container", "pause_container", "network_disconnect")
    )


@dataclass(frozen=True)
class ControllerProfileConfig:
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
    exposure: ControllerExposureSettings = field(default_factory=ControllerExposureSettings)
    chaos: ControllerChaosSettings = field(default_factory=ControllerChaosSettings)
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
        catalog: "ControllerProfileCatalog | None" = None,
    ) -> "ControllerProfileConfig":
        from media_stack.core.controller_profile.parser import parse_profile_dict

        return parse_profile_dict(cls, payload, source_path=source_path, catalog=catalog)

    @classmethod
    def from_yaml_file(
        cls,
        path: Path,
        *,
        catalog: "ControllerProfileCatalog | None" = None,
    ) -> "ControllerProfileConfig":
        from media_stack.core.controller_profile.parser import parse_profile_yaml_file

        return parse_profile_yaml_file(cls, path, catalog=catalog)
