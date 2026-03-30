"""Typed config models for bootstrap sections."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class DownloadClientConfig:
    url: str
    host: str
    port: int | None
    implementation: str
    name: str
    use_ssl: bool = False
    url_base: str = ""
    priority: int = 1
    categories: dict[str, str] = field(default_factory=dict)
    completed_paths: dict[str, str] = field(default_factory=dict)
    default_save_path: str = "/data/torrents/completed"
    temp_path: str = "/data/torrents/incomplete"
    temp_path_enabled: bool = True
    auto_tmm_enabled: bool = True
    auth_bypass: dict[str, Any] = field(default_factory=dict)
    seeding_policy: dict[str, Any] = field(default_factory=dict)
    queue_guardrails: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "DownloadClientConfig":
        src = dict(data or {})
        return cls(
            url=str(src.get("url", "")).strip(),
            host=str(src.get("host", "")).strip(),
            port=_to_int(src.get("port")),
            implementation=str(src.get("implementation", "")).strip(),
            name=str(src.get("name", "")).strip(),
            use_ssl=bool(src.get("use_ssl", False)),
            url_base=str(src.get("url_base", "")).strip(),
            priority=_to_int(src.get("priority"), 1) or 1,
            categories=dict(src.get("categories") or {}),
            completed_paths=dict(src.get("completed_paths") or {}),
            default_save_path=str(src.get("default_save_path", "/data/torrents/completed")),
            temp_path=str(src.get("temp_path", "/data/torrents/incomplete")),
            temp_path_enabled=bool(src.get("temp_path_enabled", True)),
            auto_tmm_enabled=bool(src.get("auto_tmm_enabled", True)),
            auth_bypass=dict(src.get("auth_bypass") or {}),
            seeding_policy=dict(src.get("seeding_policy") or {}),
            queue_guardrails=dict(src.get("queue_guardrails") or {}),
            raw=src,
        )


@dataclass(frozen=True)
class JellyfinLiveTvTunerConfig:
    name: str
    type: str
    url: str
    normalize_tvg_id_suffix: bool = False
    filter_to_guide_channels: bool = False
    filter_guide_path: str = ""
    materialized_output_path: str = ""
    allow_hw_transcoding: bool = True
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "JellyfinLiveTvTunerConfig":
        src = dict(data or {})
        return cls(
            name=str(src.get("name", "")).strip(),
            type=str(src.get("type", "m3u")).strip(),
            url=str(src.get("url", "")).strip(),
            normalize_tvg_id_suffix=bool(src.get("normalize_tvg_id_suffix", False)),
            filter_to_guide_channels=bool(src.get("filter_to_guide_channels", False)),
            filter_guide_path=str(src.get("filter_guide_path", "")).strip(),
            materialized_output_path=str(src.get("materialized_output_path", "")).strip(),
            allow_hw_transcoding=bool(src.get("allow_hw_transcoding", True)),
            raw=src,
        )


@dataclass(frozen=True)
class JellyfinLiveTvGuideConfig:
    type: str
    path: str
    enable_all_tuners: bool = True
    enabled_tuners: list[str] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "JellyfinLiveTvGuideConfig":
        src = dict(data or {})
        enabled = src.get("enabled_tuners")
        enabled_values: list[str] = []
        if isinstance(enabled, list):
            enabled_values = [str(x).strip() for x in enabled if str(x).strip()]
        elif enabled not in (None, ""):
            enabled_values = [str(enabled).strip()]
        return cls(
            type=str(src.get("type", "xmltv")).strip(),
            path=str(src.get("path", "")).strip(),
            enable_all_tuners=bool(src.get("enable_all_tuners", True)),
            enabled_tuners=enabled_values,
            raw=src,
        )


@dataclass(frozen=True)
class JellyfinLiveTvConfig:
    enabled: bool
    required: bool
    refresh_on_bootstrap: bool
    cleanup_duplicates: bool
    recreate_managed_guides: bool
    prune_unmanaged_tuners: bool
    prune_unmanaged_guides: bool
    fallback_enable_all_tuners_when_mapping_missing: bool
    url: str
    tuners: list[JellyfinLiveTvTunerConfig]
    guides: list[JellyfinLiveTvGuideConfig]
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "JellyfinLiveTvConfig":
        src = dict(data or {})
        tuners_raw = src.get("tuners") or []
        guides_raw = src.get("guides") or []
        tuners = [
            JellyfinLiveTvTunerConfig.from_dict(item)
            for item in tuners_raw
            if isinstance(item, dict)
        ]
        guides = [
            JellyfinLiveTvGuideConfig.from_dict(item)
            for item in guides_raw
            if isinstance(item, dict)
        ]
        return cls(
            enabled=bool(src.get("enabled", False)),
            required=bool(src.get("required", False)),
            refresh_on_bootstrap=bool(src.get("refresh_on_bootstrap", True)),
            cleanup_duplicates=bool(src.get("cleanup_duplicates", True)),
            recreate_managed_guides=bool(src.get("recreate_managed_guides", True)),
            prune_unmanaged_tuners=bool(src.get("prune_unmanaged_tuners", True)),
            prune_unmanaged_guides=bool(src.get("prune_unmanaged_guides", True)),
            fallback_enable_all_tuners_when_mapping_missing=bool(
                src.get("fallback_enable_all_tuners_when_mapping_missing", True)
            ),
            url=str(src.get("url", "http://jellyfin:8096")).strip(),
            tuners=tuners,
            guides=guides,
            raw=src,
        )


@dataclass(frozen=True)
class ArrDiscoveryListsConfig:
    enabled: bool
    required: bool
    trigger_initial_sync: bool
    prune_unmanaged: bool
    by_app: dict[str, list[dict[str, Any]]] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "ArrDiscoveryListsConfig":
        src = dict(data or {})
        by_app: dict[str, list[dict[str, Any]]] = {}
        for key, value in src.items():
            if key in {"enabled", "required", "trigger_initial_sync", "prune_unmanaged"}:
                continue
            if isinstance(value, list):
                by_app[str(key)] = [x for x in value if isinstance(x, dict)]
        return cls(
            enabled=bool(src.get("enabled", False)),
            required=bool(src.get("required", False)),
            trigger_initial_sync=bool(src.get("trigger_initial_sync", False)),
            prune_unmanaged=bool(src.get("prune_unmanaged", False)),
            by_app=by_app,
        )


@dataclass(frozen=True)
class AppCapabilities:
    supports_auth: bool = True
    supports_media_management: bool = True
    supports_root_folder: bool = True
    supports_download_handling: bool = True
    supports_quality_upgrade: bool = True
    supports_prowlarr_application: bool = True
    supports_download_clients: bool = True
    supports_remote_path_mappings: bool = True
    supports_discovery_lists: bool = True
    supports_health_check: bool = True

    @classmethod
    def from_dict(
        cls,
        data: dict[str, Any] | None,
        defaults: dict[str, Any] | None = None,
    ) -> "AppCapabilities":
        merged = dict(defaults or {})
        merged.update(dict(data or {}))
        return cls(
            supports_auth=bool(merged.get("supports_auth", True)),
            supports_media_management=bool(merged.get("supports_media_management", True)),
            supports_root_folder=bool(merged.get("supports_root_folder", True)),
            supports_download_handling=bool(merged.get("supports_download_handling", True)),
            supports_quality_upgrade=bool(merged.get("supports_quality_upgrade", True)),
            supports_prowlarr_application=bool(
                merged.get("supports_prowlarr_application", True)
            ),
            supports_download_clients=bool(merged.get("supports_download_clients", True)),
            supports_remote_path_mappings=bool(
                merged.get("supports_remote_path_mappings", True)
            ),
            supports_discovery_lists=bool(merged.get("supports_discovery_lists", True)),
            supports_health_check=bool(merged.get("supports_health_check", True)),
        )


@dataclass(frozen=True)
class ServarrAppConfig:
    name: str
    implementation: str
    url: str
    root_folder: str
    category: str = ""
    capabilities: AppCapabilities = field(default_factory=AppCapabilities)
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(
        cls,
        data: dict[str, Any] | None,
        capability_defaults: dict[str, Any] | None = None,
    ) -> "ServarrAppConfig":
        src = dict(data or {})
        impl = str(src.get("implementation", "")).strip()
        impl_key = impl.lower()
        default_caps = dict((capability_defaults or {}).get(impl_key) or {})
        default_caps.update(dict((capability_defaults or {}).get(impl) or {}))
        caps = AppCapabilities.from_dict(src.get("capabilities"), defaults=default_caps)
        return cls(
            name=str(src.get("name", "")).strip(),
            implementation=impl,
            url=str(src.get("url", "")).strip(),
            root_folder=str(src.get("root_folder", "")).strip(),
            category=str(src.get("category", "")).strip(),
            capabilities=caps,
            raw=src,
        )

    @classmethod
    def from_list(
        cls,
        data: list[dict[str, Any]] | None,
        capability_defaults: dict[str, Any] | None = None,
    ) -> list["ServarrAppConfig"]:
        items: list[ServarrAppConfig] = []
        for item in data or []:
            if not isinstance(item, dict):
                continue
            items.append(cls.from_dict(item, capability_defaults=capability_defaults))
        return items


def _to_int(value: Any, fallback: int | None = None) -> int | None:
    try:
        if value is None:
            return fallback
        return int(value)
    except (TypeError, ValueError):
        return fallback
