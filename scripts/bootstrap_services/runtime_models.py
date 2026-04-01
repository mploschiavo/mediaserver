"""Shared runtime models for bootstrap orchestration."""

from __future__ import annotations

from typing import Any

from .config_models import (
    ArrDownloadHandlingPolicy,
    ArrMediaManagementPolicy,
    ArrQualityUpgradePolicy,
    ServarrAppConfig,
)
from .enums import BootstrapMode


class BootstrapRuntime:
    """Runtime state bag for bootstrap orchestration.

    Core runtime identity and wiring fields are explicit.
    App/feature-specific toggles and optional runtime values are accepted
    dynamically so feature growth does not require editing this model.
    """

    def __init__(
        self,
        *,
        mode: BootstrapMode,
        cfg: dict[str, Any],
        config_root: str,
        wait_timeout: int,
        arr_apps_raw: list[dict[str, Any]],
        arr_apps: list[ServarrAppConfig],
        app_keys: dict[str, str],
        prowlarr_url: str,
        prowlarr_key: str,
        qbit_cfg: dict[str, Any],
        sab_cfg: dict[str, Any],
        torrent_client_key: str,
        usenet_client_key: str,
        arr_media_management_cfg: ArrMediaManagementPolicy,
        arr_download_handling_cfg: ArrDownloadHandlingPolicy,
        arr_quality_upgrade_cfg: ArrQualityUpgradePolicy,
        app_auth_cfg: dict[str, Any],
        adapter_hooks_cfg: dict[str, Any],
        prowlarr_indexers: list[dict[str, Any]],
        sab_remote_path_mappings: list[dict[str, Any]],
        qb_user: str,
        qb_pass: str,
        sab_username: str,
        sab_password: str,
        auto_indexers: bool,
        trigger_sync: bool,
        fully_preconfigured: bool,
        media_server_backend: str = "jellyfin",
        request_manager_backend: str = "jellyseerr",
        feature_flags: dict[str, bool] | None = None,
        runtime_values: dict[str, Any] | None = None,
        **dynamic_values: Any,
    ) -> None:
        self.mode = mode
        self.cfg = cfg
        self.config_root = config_root
        self.wait_timeout = wait_timeout
        self.arr_apps_raw = arr_apps_raw
        self.arr_apps = arr_apps
        self.app_keys = app_keys
        self.prowlarr_url = prowlarr_url
        self.prowlarr_key = prowlarr_key
        self.qbit_cfg = qbit_cfg
        self.sab_cfg = sab_cfg
        self.torrent_client_key = torrent_client_key
        self.usenet_client_key = usenet_client_key
        self.arr_media_management_cfg = arr_media_management_cfg
        self.arr_download_handling_cfg = arr_download_handling_cfg
        self.arr_quality_upgrade_cfg = arr_quality_upgrade_cfg
        self.app_auth_cfg = app_auth_cfg
        self.adapter_hooks_cfg = adapter_hooks_cfg
        self.prowlarr_indexers = prowlarr_indexers
        self.sab_remote_path_mappings = sab_remote_path_mappings
        self.qb_user = qb_user
        self.qb_pass = qb_pass
        self.sab_username = sab_username
        self.sab_password = sab_password
        self.auto_indexers = bool(auto_indexers)
        self.trigger_sync = bool(trigger_sync)
        self.fully_preconfigured = bool(fully_preconfigured)
        self.media_server_backend = str(media_server_backend or "jellyfin").strip() or "jellyfin"
        self.request_manager_backend = (
            str(request_manager_backend or "jellyseerr").strip() or "jellyseerr"
        )

        self.feature_flags: dict[str, bool] = {
            str(key): bool(value) for key, value in (feature_flags or {}).items()
        }
        self.runtime_values: dict[str, Any] = dict(runtime_values or {})

        for key, value in dynamic_values.items():
            token = str(key or "").strip()
            if not token:
                continue
            if isinstance(value, bool):
                self.feature_flags[token] = bool(value)
            else:
                self.runtime_values[token] = value

    def __getattr__(self, name: str) -> Any:
        if name in self.feature_flags:
            return self.feature_flags[name]
        if name in self.runtime_values:
            return self.runtime_values[name]
        raise AttributeError(name)

    # Generic torrent-client aliases retained for neutral shared-runtime naming.
    @property
    def torrent_client_cfg(self) -> dict[str, Any]:
        return self.qbit_cfg

    @property
    def torrent_client_username(self) -> str:
        return self.qb_user

    @property
    def torrent_client_password(self) -> str:
        return self.qb_pass

    @property
    def configure_torrent_arr_clients(self) -> bool:
        return bool(self.feature_flags.get("configure_qbit_arr_clients", False))

    @property
    def set_torrent_categories(self) -> bool:
        return bool(self.feature_flags.get("set_qbit_categories", False))

    @property
    def torrent_client_login_required(self) -> bool:
        return bool(self.feature_flags.get("qbit_login_required", False))
