"""Shared runtime models for bootstrap orchestration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .config_models import (
    ArrDownloadHandlingPolicy,
    ArrMediaManagementPolicy,
    ArrQualityUpgradePolicy,
    ServarrAppConfig,
)
from .enums import BootstrapMode


@dataclass
class BootstrapRuntime:
    mode: BootstrapMode
    cfg: dict[str, Any]
    config_root: str
    wait_timeout: int
    arr_apps_raw: list[dict[str, Any]]
    arr_apps: list[ServarrAppConfig]
    app_keys: dict[str, str]
    prowlarr_url: str
    prowlarr_key: str
    qbit_cfg: dict[str, Any]
    sab_cfg: dict[str, Any]
    torrent_client_key: str
    usenet_client_key: str
    arr_media_management_cfg: ArrMediaManagementPolicy
    arr_download_handling_cfg: ArrDownloadHandlingPolicy
    arr_quality_upgrade_cfg: ArrQualityUpgradePolicy
    app_auth_cfg: dict[str, Any]
    adapter_hooks_cfg: dict[str, Any]
    prowlarr_indexers: list[dict[str, Any]]
    sab_remote_path_mappings: list[dict[str, Any]]
    qb_user: str
    qb_pass: str
    sab_username: str
    sab_password: str
    auto_indexers: bool
    trigger_sync: bool
    fully_preconfigured: bool
    configure_qbit_arr_clients: bool
    configure_sab_arr_clients: bool
    configure_arr_media_management: bool
    configure_arr_download_handling: bool
    configure_arr_quality_upgrade: bool
    configure_arr_discovery_lists: bool
    set_qbit_categories: bool
    qbit_login_required: bool
    refresh_health_after_bootstrap: bool
    configure_maintainerr_policy: bool
    maintainerr_required: bool
    configure_maintainerr_integrations: bool
    maintainerr_integrations_required: bool
    configure_homepage_services: bool
    homepage_required: bool
    configure_bazarr_integration: bool
    bazarr_required: bool
    configure_jellyseerr_services: bool
    jellyseerr_required: bool
    configure_jellyfin_livetv: bool
    jellyfin_livetv_required: bool
    configure_jellyfin_libraries: bool
    jellyfin_libraries_required: bool
    configure_jellyfin_plugins: bool
    jellyfin_plugins_required: bool
    configure_jellyfin_playback: bool
    jellyfin_playback_required: bool
    configure_jellyfin_home_rails: bool
    jellyfin_home_rails_required: bool
    configure_auto_collections: bool
    auto_collections_required: bool
    configure_disk_guardrails: bool
    disk_guardrails_required: bool
    configure_media_hygiene: bool
    media_hygiene_required: bool
    configure_jellyfin_prewarm: bool
    jellyfin_prewarm_required: bool
    media_server_backend: str = "jellyfin"
