"""Typed models for runtime factory composition."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from ..enums import BootstrapMode
from ..runtime_models import ControllerRuntime

BoolCfgFn = Callable[[dict[str, Any], str, bool], bool]
CoerceListFn = Callable[[Any], list[Any]]
DeepMergeFn = Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]]
LoadDefaultJsonFn = Callable[[str, Any], Any]
EnvTruthyFn = Callable[[str, bool], bool]
ReadApiKeyFn = Callable[[str, str], str]
BuildSabMappingsFn = Callable[[dict[str, Any]], list[dict[str, Any]]]


@dataclass(frozen=True)
class ControllerCliArgs:
    mode: BootstrapMode
    config_path: str
    config_root: str
    wait_timeout: int
    auto_prowlarr_indexers: bool
    runtime_env: str = "prod"


@dataclass(frozen=True)
class ControllerPlanSummary:
    mode: BootstrapMode
    arr_apps: int
    prowlarr_indexers: int
    auto_indexers: bool
    configure_arr_clients: bool
    configure_torrent_arr_clients: bool
    configure_sab_arr_clients: bool
    sab_remote_path_mappings: int
    configure_arr_media_management: bool
    configure_arr_quality_upgrade: bool
    configure_arr_download_handling: bool
    configure_arr_discovery_lists: bool
    set_torrent_categories: bool
    torrent_client_login_required: bool
    refresh_health_after_bootstrap: bool
    app_auth_enabled: bool
    configure_homepage: bool
    configure_bazarr: bool
    configure_jellyseerr: bool
    configure_jellyfin_libraries: bool
    configure_jellyfin_livetv: bool
    configure_jellyfin_plugins: bool
    configure_jellyfin_playback: bool
    configure_jellyfin_home_rails: bool
    configure_auto_collections: bool
    configure_disk_guardrails: bool
    configure_jellyfin_prewarm: bool
    configure_media_hygiene: bool
    configure_maintainerr_policy: bool
    configure_maintainerr_integrations: bool
    jellyfin_livetv_tuners: int
    jellyfin_livetv_guides: int
    fully_preconfigured: bool
    trigger_sync: bool

    @property
    def configure_qbit_arr_clients(self) -> bool:
        return self.configure_torrent_arr_clients

    @property
    def set_qbit_categories(self) -> bool:
        return self.set_torrent_categories

    @property
    def qbit_login_required(self) -> bool:
        return self.torrent_client_login_required

    def to_log_line(self) -> str:
        return (
            f"mode={self.mode.value}, "
            f"arr_apps={self.arr_apps}, "
            f"prowlarr_indexers={self.prowlarr_indexers}, "
            f"auto_indexers={self.auto_indexers}, "
            f"configure_arr_clients={self.configure_arr_clients}, "
            f"configure_torrent_arr_clients={self.configure_torrent_arr_clients}, "
            f"configure_sab_arr_clients={self.configure_sab_arr_clients}, "
            f"sab_remote_path_mappings={self.sab_remote_path_mappings}, "
            f"configure_arr_media_management={self.configure_arr_media_management}, "
            f"configure_arr_quality_upgrade={self.configure_arr_quality_upgrade}, "
            f"configure_arr_download_handling={self.configure_arr_download_handling}, "
            f"configure_arr_discovery_lists={self.configure_arr_discovery_lists}, "
            f"set_torrent_categories={self.set_torrent_categories}, "
            f"torrent_client_login_required={self.torrent_client_login_required}, "
            f"refresh_health_after_bootstrap={self.refresh_health_after_bootstrap}, "
            f"app_auth_enabled={self.app_auth_enabled}, "
            f"configure_homepage={self.configure_homepage}, "
            f"configure_bazarr={self.configure_bazarr}, "
            f"configure_jellyseerr={self.configure_jellyseerr}, "
            f"configure_jellyfin_libraries={self.configure_jellyfin_libraries}, "
            f"configure_jellyfin_livetv={self.configure_jellyfin_livetv}, "
            f"configure_jellyfin_plugins={self.configure_jellyfin_plugins}, "
            f"configure_jellyfin_playback={self.configure_jellyfin_playback}, "
            f"configure_jellyfin_home_rails={self.configure_jellyfin_home_rails}, "
            f"configure_auto_collections={self.configure_auto_collections}, "
            f"configure_disk_guardrails={self.configure_disk_guardrails}, "
            f"configure_jellyfin_prewarm={self.configure_jellyfin_prewarm}, "
            f"configure_media_hygiene={self.configure_media_hygiene}, "
            f"configure_maintainerr_policy={self.configure_maintainerr_policy}, "
            f"configure_maintainerr_integrations={self.configure_maintainerr_integrations}, "
            f"jellyfin_livetv_tuners={self.jellyfin_livetv_tuners}, "
            f"jellyfin_livetv_guides={self.jellyfin_livetv_guides}, "
            f"fully_preconfigured={self.fully_preconfigured}, "
            f"trigger_sync={self.trigger_sync}"
        )


@dataclass(frozen=True)
class ControllerRuntimeBuildResult:
    cfg: dict[str, Any]
    runtime: ControllerRuntime
    plan: ControllerPlanSummary


@dataclass
class ControllerRuntimeFactoryDependencies:
    load_bootstrap_default_json: LoadDefaultJsonFn
    deep_merge_objects: DeepMergeFn
    bool_cfg: BoolCfgFn
    coerce_list: CoerceListFn
    env_truthy: EnvTruthyFn
    read_api_key: ReadApiKeyFn
    build_sab_remote_path_mappings: BuildSabMappingsFn
