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


class ControllerPlanSummary:
    """Plan summary with dict-based storage for features and counts.

    Feature booleans live in ``features``, integer counters in ``counts``.
    Named property accessors are provided for backward compatibility so that
    existing ``plan.configure_jellyfin_libraries`` style access keeps working.
    """

    __slots__ = ("mode", "features", "counts")

    def __init__(
        self,
        *,
        mode: BootstrapMode,
        features: dict[str, bool] | None = None,
        counts: dict[str, int] | None = None,
        # --- backward-compat keyword args (routed into dicts) ---
        arr_apps: int | None = None,
        prowlarr_indexers: int | None = None,
        auto_indexers: bool | None = None,
        configure_arr_clients: bool | None = None,
        configure_torrent_arr_clients: bool | None = None,
        configure_sab_arr_clients: bool | None = None,
        sab_remote_path_mappings: int | None = None,
        configure_arr_media_management: bool | None = None,
        configure_arr_quality_upgrade: bool | None = None,
        configure_arr_download_handling: bool | None = None,
        configure_arr_discovery_lists: bool | None = None,
        set_torrent_categories: bool | None = None,
        torrent_client_login_required: bool | None = None,
        refresh_health_after_setup: bool | None = None,
        app_auth_enabled: bool | None = None,
        configure_homepage: bool | None = None,
        configure_bazarr: bool | None = None,
        configure_jellyseerr: bool | None = None,
        configure_jellyfin_libraries: bool | None = None,
        configure_jellyfin_livetv: bool | None = None,
        configure_jellyfin_plugins: bool | None = None,
        configure_jellyfin_playback: bool | None = None,
        configure_jellyfin_home_rails: bool | None = None,
        configure_auto_collections: bool | None = None,
        configure_disk_guardrails: bool | None = None,
        configure_jellyfin_prewarm: bool | None = None,
        configure_media_hygiene: bool | None = None,
        configure_maintainerr_policy: bool | None = None,
        configure_maintainerr_integrations: bool | None = None,
        jellyfin_livetv_tuners: int | None = None,
        jellyfin_livetv_guides: int | None = None,
        fully_preconfigured: bool | None = None,
        trigger_sync: bool | None = None,
    ) -> None:
        object.__setattr__(self, "mode", mode)

        # Seed from explicit dicts when provided (new callers)
        _features: dict[str, bool] = dict(features or {})
        _counts: dict[str, int] = dict(counts or {})

        # Merge in any legacy keyword args so old call-sites keep working.
        _legacy_bools: dict[str, bool | None] = {
            "auto_indexers": auto_indexers,
            "configure_arr_clients": configure_arr_clients,
            "configure_torrent_arr_clients": configure_torrent_arr_clients,
            "configure_sab_arr_clients": configure_sab_arr_clients,
            "configure_arr_media_management": configure_arr_media_management,
            "configure_arr_quality_upgrade": configure_arr_quality_upgrade,
            "configure_arr_download_handling": configure_arr_download_handling,
            "configure_arr_discovery_lists": configure_arr_discovery_lists,
            "set_torrent_categories": set_torrent_categories,
            "torrent_client_login_required": torrent_client_login_required,
            "refresh_health_after_setup": refresh_health_after_setup,
            "app_auth_enabled": app_auth_enabled,
            "configure_homepage": configure_homepage,
            "configure_bazarr": configure_bazarr,
            "configure_jellyseerr": configure_jellyseerr,
            "configure_jellyfin_libraries": configure_jellyfin_libraries,
            "configure_jellyfin_livetv": configure_jellyfin_livetv,
            "configure_jellyfin_plugins": configure_jellyfin_plugins,
            "configure_jellyfin_playback": configure_jellyfin_playback,
            "configure_jellyfin_home_rails": configure_jellyfin_home_rails,
            "configure_auto_collections": configure_auto_collections,
            "configure_disk_guardrails": configure_disk_guardrails,
            "configure_jellyfin_prewarm": configure_jellyfin_prewarm,
            "configure_media_hygiene": configure_media_hygiene,
            "configure_maintainerr_policy": configure_maintainerr_policy,
            "configure_maintainerr_integrations": configure_maintainerr_integrations,
            "fully_preconfigured": fully_preconfigured,
            "trigger_sync": trigger_sync,
        }
        for key, value in _legacy_bools.items():
            if value is not None and key not in _features:
                _features[key] = bool(value)

        _legacy_counts: dict[str, int | None] = {
            "arr_apps": arr_apps,
            "prowlarr_indexers": prowlarr_indexers,
            "sab_remote_path_mappings": sab_remote_path_mappings,
            "jellyfin_livetv_tuners": jellyfin_livetv_tuners,
            "jellyfin_livetv_guides": jellyfin_livetv_guides,
        }
        for key, value in _legacy_counts.items():
            if value is not None and key not in _counts:
                _counts[key] = int(value)

        object.__setattr__(self, "features", _features)
        object.__setattr__(self, "counts", _counts)

    # --- immutability (mirrors frozen dataclass behaviour) ---

    def __setattr__(self, name: str, value: Any) -> None:
        raise AttributeError(f"cannot set '{name}' on frozen ControllerPlanSummary")

    def __delattr__(self, name: str) -> None:
        raise AttributeError(f"cannot delete '{name}' on frozen ControllerPlanSummary")

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, ControllerPlanSummary):
            return NotImplemented
        return (self.mode, self.features, self.counts) == (
            other.mode,
            other.features,
            other.counts,
        )

    def __repr__(self) -> str:
        return (
            f"ControllerPlanSummary(mode={self.mode!r}, "
            f"features={self.features!r}, counts={self.counts!r})"
        )

    # --- backward-compat named property accessors ---

    # Count properties
    @property
    def arr_apps(self) -> int:
        return self.counts.get("arr_apps", 0)

    @property
    def prowlarr_indexers(self) -> int:
        return self.counts.get("prowlarr_indexers", 0)

    @property
    def sab_remote_path_mappings(self) -> int:
        return self.counts.get("sab_remote_path_mappings", 0)

    @property
    def jellyfin_livetv_tuners(self) -> int:
        return self.counts.get("jellyfin_livetv_tuners", 0)

    @property
    def jellyfin_livetv_guides(self) -> int:
        return self.counts.get("jellyfin_livetv_guides", 0)

    # Feature boolean properties
    @property
    def auto_indexers(self) -> bool:
        return self.features.get("auto_indexers", False)

    @property
    def configure_arr_clients(self) -> bool:
        return self.features.get("configure_arr_clients", False)

    @property
    def configure_torrent_arr_clients(self) -> bool:
        return self.features.get("configure_torrent_arr_clients", False)

    @property
    def configure_sab_arr_clients(self) -> bool:
        return self.features.get("configure_sab_arr_clients", False)

    @property
    def configure_arr_media_management(self) -> bool:
        return self.features.get("configure_arr_media_management", False)

    @property
    def configure_arr_quality_upgrade(self) -> bool:
        return self.features.get("configure_arr_quality_upgrade", False)

    @property
    def configure_arr_download_handling(self) -> bool:
        return self.features.get("configure_arr_download_handling", False)

    @property
    def configure_arr_discovery_lists(self) -> bool:
        return self.features.get("configure_arr_discovery_lists", False)

    @property
    def set_torrent_categories(self) -> bool:
        return self.features.get("set_torrent_categories", False)

    @property
    def torrent_client_login_required(self) -> bool:
        return self.features.get("torrent_client_login_required", False)

    @property
    def refresh_health_after_setup(self) -> bool:
        return self.features.get("refresh_health_after_setup", False)

    @property
    def app_auth_enabled(self) -> bool:
        return self.features.get("app_auth_enabled", False)

    @property
    def configure_homepage(self) -> bool:
        return self.features.get("configure_homepage", False)

    @property
    def configure_bazarr(self) -> bool:
        return self.features.get("configure_bazarr", False)

    @property
    def configure_jellyseerr(self) -> bool:
        return self.features.get("configure_jellyseerr", False)

    @property
    def configure_jellyfin_libraries(self) -> bool:
        return self.features.get("configure_jellyfin_libraries", False)

    @property
    def configure_jellyfin_livetv(self) -> bool:
        return self.features.get("configure_jellyfin_livetv", False)

    @property
    def configure_jellyfin_plugins(self) -> bool:
        return self.features.get("configure_jellyfin_plugins", False)

    @property
    def configure_jellyfin_playback(self) -> bool:
        return self.features.get("configure_jellyfin_playback", False)

    @property
    def configure_jellyfin_home_rails(self) -> bool:
        return self.features.get("configure_jellyfin_home_rails", False)

    @property
    def configure_auto_collections(self) -> bool:
        return self.features.get("configure_auto_collections", False)

    @property
    def configure_disk_guardrails(self) -> bool:
        return self.features.get("configure_disk_guardrails", False)

    @property
    def configure_jellyfin_prewarm(self) -> bool:
        return self.features.get("configure_jellyfin_prewarm", False)

    @property
    def configure_media_hygiene(self) -> bool:
        return self.features.get("configure_media_hygiene", False)

    @property
    def configure_maintainerr_policy(self) -> bool:
        return self.features.get("configure_maintainerr_policy", False)

    @property
    def configure_maintainerr_integrations(self) -> bool:
        return self.features.get("configure_maintainerr_integrations", False)

    @property
    def fully_preconfigured(self) -> bool:
        return self.features.get("fully_preconfigured", False)

    @property
    def trigger_sync(self) -> bool:
        return self.features.get("trigger_sync", False)

    # Legacy aliases (kept for backward compat)
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
        """Build log line by iterating dicts instead of hardcoding field names."""
        parts = [f"mode={self.mode.value}"]
        for key, value in self.counts.items():
            parts.append(f"{key}={value}")
        for key, value in self.features.items():
            parts.append(f"{key}={value}")
        return ", ".join(parts)


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
