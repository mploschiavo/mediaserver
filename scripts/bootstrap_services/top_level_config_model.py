"""Strict top-level bootstrap config model."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .config_models import (
    ArrDiscoveryListsConfig,
    ArrDownloadHandlingPolicy,
    ArrMediaManagementPolicy,
    ArrQualityUpgradePolicy,
    DiskGuardrailsConfig,
    DownloadClientsConfig,
    JellyfinLibrariesConfig,
    JellyfinLiveTvConfig,
    JellyfinPlaybackConfig,
    JellyfinPluginsConfig,
    JellyfinPrewarmConfig,
    ServarrAppConfig,
    TechnologyBindingsConfig,
)

SUPPORTED_BOOTSTRAP_CONFIG_VERSION = 2


def _expect_dict(
    data: dict[str, Any], key: str, default: dict[str, Any] | None = None
) -> dict[str, Any]:
    value = data.get(key, default if default is not None else {})
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"$.{key} must be an object")
    return dict(value)


def _expect_list(data: dict[str, Any], key: str, default: list[Any] | None = None) -> list[Any]:
    value = data.get(key, default if default is not None else [])
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"$.{key} must be an array")
    return list(value)


def _expect_bool(data: dict[str, Any], key: str, default: bool = False) -> bool:
    value = data.get(key, default)
    if not isinstance(value, bool):
        raise ValueError(f"$.{key} must be a boolean")
    return value


def _expect_str(data: dict[str, Any], key: str, default: str = "") -> str:
    value = data.get(key, default)
    if not isinstance(value, str):
        raise ValueError(f"$.{key} must be a string")
    return value


def _expect_int(data: dict[str, Any], key: str) -> int:
    if key not in data:
        raise ValueError(f"$.{key} is required")
    value = data.get(key)
    if not isinstance(value, int):
        raise ValueError(f"$.{key} must be an integer")
    return int(value)


@dataclass(frozen=True)
class ConfigOverlaySettings:
    enabled: bool = False
    env: str = "prod"
    base_path: str = "config/runtime/base.json"
    overlay_dir: str = "config/runtime/overlays"
    env_overlays: dict[str, str] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, value: dict[str, Any] | None) -> "ConfigOverlaySettings":
        src = dict(value or {})
        env_overlays = src.get("env_overlays") or {}
        if env_overlays is None:
            env_overlays = {}
        if not isinstance(env_overlays, dict):
            raise ValueError("$.config_overlays.env_overlays must be an object")
        return cls(
            enabled=bool(src.get("enabled", False)),
            env=str(src.get("env", "prod")).strip() or "prod",
            base_path=str(src.get("base_path", "config/runtime/base.json")).strip()
            or "config/runtime/base.json",
            overlay_dir=str(src.get("overlay_dir", "config/runtime/overlays")).strip()
            or "config/runtime/overlays",
            env_overlays={
                str(key).strip().lower(): str(item).strip()
                for key, item in env_overlays.items()
                if str(key).strip() and str(item).strip()
            },
            raw=src,
        )


@dataclass(frozen=True)
class TopLevelBootstrapConfig:
    config_version: int
    adapter_hooks: dict[str, Any]
    app_auth: dict[str, Any]
    arr_apps: list[dict[str, Any]]
    arr_discovery_lists: dict[str, Any]
    arr_download_handling: dict[str, Any]
    arr_media_management: dict[str, Any]
    arr_quality_upgrade: dict[str, Any]
    bazarr: dict[str, Any]
    disk_guardrails: dict[str, Any]
    download_clients: dict[str, Any]
    flaresolverr: dict[str, Any]
    homepage: dict[str, Any]
    jellyfin_auto_collections: dict[str, Any]
    jellyfin_home_rails: dict[str, Any]
    jellyfin_libraries: dict[str, Any]
    jellyfin_livetv: dict[str, Any]
    jellyfin_playback: dict[str, Any]
    jellyfin_plugins: dict[str, Any]
    jellyfin_prewarm: dict[str, Any]
    jellyseerr: dict[str, Any]
    maintainerr: dict[str, Any]
    media_hygiene: dict[str, Any]
    prowlarr_auto_add_tested_indexers: bool
    prowlarr_auto_indexer_exclude_name_tokens: list[str]
    prowlarr_indexer_reputation: dict[str, Any]
    prowlarr_indexers: list[dict[str, Any]]
    prowlarr_url: str
    quality_profiles: dict[str, Any]
    readarr: dict[str, Any]
    refresh_health_after_bootstrap: bool
    sonarr_seed_series: dict[str, Any]
    technology_bindings: dict[str, Any]
    trigger_indexer_sync: bool
    arr_indexer_sync: dict[str, Any]
    config_overlays: ConfigOverlaySettings
    unknown: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, cfg: dict[str, Any]) -> "TopLevelBootstrapConfig":
        if not isinstance(cfg, dict):
            raise ValueError("Bootstrap config root must be an object")
        src = dict(cfg)
        config_version = _expect_int(src, "config_version")
        if config_version != SUPPORTED_BOOTSTRAP_CONFIG_VERSION:
            raise ValueError(
                "$.config_version "
                f"{config_version} is not supported. "
                f"Expected {SUPPORTED_BOOTSTRAP_CONFIG_VERSION}. "
                "Migrate your bootstrap config before running."
            )

        # Validate key nested sections through typed models so invalid shapes fail fast.
        DownloadClientsConfig.from_dict(_expect_dict(src, "download_clients", {}))
        ArrDiscoveryListsConfig.from_dict(_expect_dict(src, "arr_discovery_lists", {}))
        ArrMediaManagementPolicy.from_dict(_expect_dict(src, "arr_media_management", {}))
        ArrDownloadHandlingPolicy.from_dict(_expect_dict(src, "arr_download_handling", {}))
        ArrQualityUpgradePolicy.from_dict(_expect_dict(src, "arr_quality_upgrade", {}))
        ServarrAppConfig.from_list(_expect_list(src, "arr_apps", []))
        technology_bindings_raw = _expect_dict(src, "technology_bindings", {})
        request_manager_value = technology_bindings_raw.get("request_manager")
        if request_manager_value is not None and not isinstance(request_manager_value, str):
            raise ValueError("$.technology_bindings.request_manager must be a string")
        technology_bindings_typed = TechnologyBindingsConfig.from_dict(technology_bindings_raw)
        if not technology_bindings_typed.torrent_client:
            raise ValueError("$.technology_bindings.torrent_client must be a non-empty string")
        if not technology_bindings_typed.usenet_client:
            raise ValueError("$.technology_bindings.usenet_client must be a non-empty string")
        if not technology_bindings_typed.media_server:
            raise ValueError("$.technology_bindings.media_server must be a non-empty string")
        JellyfinLiveTvConfig.from_dict(_expect_dict(src, "jellyfin_livetv", {}))
        JellyfinLibrariesConfig.from_dict(_expect_dict(src, "jellyfin_libraries", {}))
        JellyfinPluginsConfig.from_dict(_expect_dict(src, "jellyfin_plugins", {}))
        JellyfinPlaybackConfig.from_dict(_expect_dict(src, "jellyfin_playback", {}))
        JellyfinPrewarmConfig.from_dict(_expect_dict(src, "jellyfin_prewarm", {}))
        DiskGuardrailsConfig.from_dict(_expect_dict(src, "disk_guardrails", {}))

        known_keys = {
            "config_version",
            "adapter_hooks",
            "app_auth",
            "arr_apps",
            "arr_discovery_lists",
            "arr_download_handling",
            "arr_media_management",
            "arr_quality_upgrade",
            "bazarr",
            "disk_guardrails",
            "download_clients",
            "flaresolverr",
            "homepage",
            "jellyfin_auto_collections",
            "jellyfin_home_rails",
            "jellyfin_libraries",
            "jellyfin_livetv",
            "jellyfin_playback",
            "jellyfin_plugins",
            "jellyfin_prewarm",
            "jellyseerr",
            "maintainerr",
            "media_hygiene",
            "prowlarr_auto_add_tested_indexers",
            "prowlarr_auto_indexer_exclude_name_tokens",
            "prowlarr_indexer_reputation",
            "prowlarr_indexers",
            "prowlarr_url",
            "quality_profiles",
            "readarr",
            "refresh_health_after_bootstrap",
            "sonarr_seed_series",
            "technology_bindings",
            "trigger_indexer_sync",
            "arr_indexer_sync",
            "config_overlays",
        }
        unknown = {key: value for key, value in src.items() if key not in known_keys}
        if unknown:
            unknown_keys = ", ".join(sorted(unknown.keys()))
            raise ValueError(
                "Unsupported top-level bootstrap config keys: "
                f"{unknown_keys}. "
                "Migrate config_version to the current schema before running."
            )

        arr_apps_raw = _expect_list(src, "arr_apps", [])
        if any(not isinstance(item, dict) for item in arr_apps_raw):
            raise ValueError("$.arr_apps must contain only objects")
        prowlarr_indexers_raw = _expect_list(src, "prowlarr_indexers", [])
        if any(not isinstance(item, dict) for item in prowlarr_indexers_raw):
            raise ValueError("$.prowlarr_indexers must contain only objects")
        exclude_tokens_raw = _expect_list(src, "prowlarr_auto_indexer_exclude_name_tokens", [])
        exclude_tokens = [str(item).strip() for item in exclude_tokens_raw if str(item).strip()]

        return cls(
            config_version=config_version,
            adapter_hooks=_expect_dict(src, "adapter_hooks", {}),
            app_auth=_expect_dict(src, "app_auth", {}),
            arr_apps=[dict(item) for item in arr_apps_raw],
            arr_discovery_lists=_expect_dict(src, "arr_discovery_lists", {}),
            arr_download_handling=_expect_dict(src, "arr_download_handling", {}),
            arr_media_management=_expect_dict(src, "arr_media_management", {}),
            arr_quality_upgrade=_expect_dict(src, "arr_quality_upgrade", {}),
            bazarr=_expect_dict(src, "bazarr", {}),
            disk_guardrails=_expect_dict(src, "disk_guardrails", {}),
            download_clients=_expect_dict(src, "download_clients", {}),
            flaresolverr=_expect_dict(src, "flaresolverr", {}),
            homepage=_expect_dict(src, "homepage", {}),
            jellyfin_auto_collections=_expect_dict(src, "jellyfin_auto_collections", {}),
            jellyfin_home_rails=_expect_dict(src, "jellyfin_home_rails", {}),
            jellyfin_libraries=_expect_dict(src, "jellyfin_libraries", {}),
            jellyfin_livetv=_expect_dict(src, "jellyfin_livetv", {}),
            jellyfin_playback=_expect_dict(src, "jellyfin_playback", {}),
            jellyfin_plugins=_expect_dict(src, "jellyfin_plugins", {}),
            jellyfin_prewarm=_expect_dict(src, "jellyfin_prewarm", {}),
            jellyseerr=_expect_dict(src, "jellyseerr", {}),
            maintainerr=_expect_dict(src, "maintainerr", {}),
            media_hygiene=_expect_dict(src, "media_hygiene", {}),
            prowlarr_auto_add_tested_indexers=_expect_bool(
                src,
                "prowlarr_auto_add_tested_indexers",
                False,
            ),
            prowlarr_auto_indexer_exclude_name_tokens=exclude_tokens,
            prowlarr_indexer_reputation=_expect_dict(src, "prowlarr_indexer_reputation", {}),
            prowlarr_indexers=[dict(item) for item in prowlarr_indexers_raw],
            prowlarr_url=_expect_str(src, "prowlarr_url", ""),
            quality_profiles=_expect_dict(src, "quality_profiles", {}),
            readarr=_expect_dict(src, "readarr", {}),
            refresh_health_after_bootstrap=_expect_bool(
                src, "refresh_health_after_bootstrap", True
            ),
            sonarr_seed_series=_expect_dict(src, "sonarr_seed_series", {}),
            technology_bindings=_expect_dict(src, "technology_bindings", {}),
            trigger_indexer_sync=_expect_bool(src, "trigger_indexer_sync", True),
            arr_indexer_sync=_expect_dict(src, "arr_indexer_sync", {}),
            config_overlays=ConfigOverlaySettings.from_dict(
                _expect_dict(src, "config_overlays", {})
            ),
            unknown=unknown,
            raw=src,
        )

    def to_dict(self) -> dict[str, Any]:
        out = dict(self.raw)
        # Keep normalized list coercion for exclude tokens.
        out["prowlarr_auto_indexer_exclude_name_tokens"] = list(
            self.prowlarr_auto_indexer_exclude_name_tokens
        )
        return out
