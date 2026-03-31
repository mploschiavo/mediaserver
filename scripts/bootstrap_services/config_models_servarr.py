"""Typed models for Servarr app, discovery-list, and policy sections."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from .config_model_utils import coerce_bool_opt, coerce_str_list_opt, normalize_by_app_key


def app_lookup_keys(
    app: "ServarrAppConfig | dict[str, Any] | str",
    canonicalize: Callable[[str], str] | None = None,
) -> tuple[str, ...]:
    if isinstance(app, ServarrAppConfig):
        name = app.name
        impl = app.implementation
    elif isinstance(app, dict):
        name = str(app.get("name") or "").strip()
        impl = str(app.get("implementation") or "").strip()
    else:
        name = str(app or "").strip()
        impl = ""

    candidates: list[str] = []
    for token in (name, impl):
        for variant in (token, token.lower()):
            normalized = normalize_by_app_key(variant, canonicalize=canonicalize)
            if normalized and normalized not in candidates:
                candidates.append(normalized)
    return tuple(candidates)


@dataclass(frozen=True)
class ArrDiscoveryListEntry:
    name: str
    implementation: str
    enabled: bool
    enable_auto: bool
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "ArrDiscoveryListEntry":
        src = dict(data or {})
        return cls(
            name=str(src.get("name", "")).strip(),
            implementation=str(src.get("implementation", "")).strip(),
            enabled=bool(src.get("enabled", True)),
            enable_auto=bool(src.get("enable_auto", src.get("enableAuto", True))),
            raw=src,
        )

    def to_dict(self) -> dict[str, Any]:
        return dict(self.raw)


@dataclass(frozen=True)
class ArrDiscoveryListsConfig:
    enabled: bool
    required: bool
    trigger_initial_sync: bool
    prune_unmanaged: bool
    by_app: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    typed_by_app: dict[str, list[ArrDiscoveryListEntry]] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "ArrDiscoveryListsConfig":
        src = dict(data or {})
        by_app: dict[str, list[dict[str, Any]]] = {}
        typed_by_app: dict[str, list[ArrDiscoveryListEntry]] = {}
        for key, value in src.items():
            if key in {"enabled", "required", "trigger_initial_sync", "prune_unmanaged"}:
                continue
            if isinstance(value, list):
                typed_items = [ArrDiscoveryListEntry.from_dict(item) for item in value if isinstance(item, dict)]
                by_app[str(key)] = [entry.to_dict() for entry in typed_items]
                typed_by_app[str(key)] = typed_items
        return cls(
            enabled=bool(src.get("enabled", False)),
            required=bool(src.get("required", False)),
            trigger_initial_sync=bool(src.get("trigger_initial_sync", False)),
            prune_unmanaged=bool(src.get("prune_unmanaged", False)),
            by_app=by_app,
            typed_by_app=typed_by_app,
            raw=src,
        )


@dataclass(frozen=True)
class ArrMediaManagementOverride:
    enabled: bool | None = None
    copy_using_hardlinks: bool | None = None
    create_empty_series_folders: bool | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "ArrMediaManagementOverride":
        src = dict(data or {})
        return cls(
            enabled=coerce_bool_opt(src.get("enabled")),
            copy_using_hardlinks=coerce_bool_opt(src.get("copy_using_hardlinks")),
            create_empty_series_folders=coerce_bool_opt(src.get("create_empty_series_folders")),
        )


@dataclass(frozen=True)
class ArrMediaManagementResolvedPolicy:
    enabled: bool
    copy_using_hardlinks: bool
    create_empty_series_folders: bool


@dataclass(frozen=True)
class ArrMediaManagementPolicy:
    enabled: bool
    copy_using_hardlinks: bool
    create_empty_series_folders: bool
    by_app: dict[str, ArrMediaManagementOverride] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(
        cls,
        data: dict[str, Any] | None,
        canonicalize: Callable[[str], str] | None = None,
    ) -> "ArrMediaManagementPolicy":
        src = dict(data or {})
        by_app_raw = src.get("by_app") or {}
        by_app: dict[str, ArrMediaManagementOverride] = {}
        if isinstance(by_app_raw, dict):
            for key, value in by_app_raw.items():
                if not isinstance(value, dict):
                    continue
                token = normalize_by_app_key(str(key), canonicalize=canonicalize)
                if not token:
                    continue
                by_app[token] = ArrMediaManagementOverride.from_dict(value)

        return cls(
            enabled=bool(src.get("enabled", True)),
            copy_using_hardlinks=bool(src.get("copy_using_hardlinks", True)),
            create_empty_series_folders=bool(src.get("create_empty_series_folders", True)),
            by_app=by_app,
            raw=src,
        )

    def override_for(
        self,
        app: "ServarrAppConfig | dict[str, Any] | str",
        canonicalize: Callable[[str], str] | None = None,
    ) -> ArrMediaManagementOverride:
        for key in app_lookup_keys(app, canonicalize=canonicalize):
            override = self.by_app.get(key)
            if override:
                return override
        return ArrMediaManagementOverride()

    def resolved_for(
        self,
        app: "ServarrAppConfig | dict[str, Any] | str",
        canonicalize: Callable[[str], str] | None = None,
    ) -> ArrMediaManagementResolvedPolicy:
        override = self.override_for(app, canonicalize=canonicalize)
        return ArrMediaManagementResolvedPolicy(
            enabled=self.enabled if override.enabled is None else bool(override.enabled),
            copy_using_hardlinks=(
                self.copy_using_hardlinks
                if override.copy_using_hardlinks is None
                else bool(override.copy_using_hardlinks)
            ),
            create_empty_series_folders=(
                self.create_empty_series_folders
                if override.create_empty_series_folders is None
                else bool(override.create_empty_series_folders)
            ),
        )


@dataclass(frozen=True)
class ArrDownloadHandlingOverride:
    enabled: bool | None = None
    enable_completed_download_handling: bool | None = None
    remove_completed_downloads: bool | None = None
    remove_failed_downloads: bool | None = None
    auto_redownload_failed: bool | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "ArrDownloadHandlingOverride":
        src = dict(data or {})
        return cls(
            enabled=coerce_bool_opt(src.get("enabled")),
            enable_completed_download_handling=coerce_bool_opt(src.get("enable_completed_download_handling")),
            remove_completed_downloads=coerce_bool_opt(src.get("remove_completed_downloads")),
            remove_failed_downloads=coerce_bool_opt(src.get("remove_failed_downloads")),
            auto_redownload_failed=coerce_bool_opt(src.get("auto_redownload_failed")),
        )


@dataclass(frozen=True)
class ArrDownloadHandlingResolvedPolicy:
    enabled: bool
    enable_completed_download_handling: bool
    remove_completed_downloads: bool
    remove_failed_downloads: bool
    auto_redownload_failed: bool


@dataclass(frozen=True)
class ArrDownloadHandlingPolicy:
    enabled: bool
    enable_completed_download_handling: bool
    remove_completed_downloads: bool
    remove_failed_downloads: bool
    auto_redownload_failed: bool
    by_app: dict[str, ArrDownloadHandlingOverride] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(
        cls,
        data: dict[str, Any] | None,
        canonicalize: Callable[[str], str] | None = None,
    ) -> "ArrDownloadHandlingPolicy":
        src = dict(data or {})
        by_app_raw = src.get("by_app") or {}
        by_app: dict[str, ArrDownloadHandlingOverride] = {}
        if isinstance(by_app_raw, dict):
            for key, value in by_app_raw.items():
                if not isinstance(value, dict):
                    continue
                token = normalize_by_app_key(str(key), canonicalize=canonicalize)
                if not token:
                    continue
                by_app[token] = ArrDownloadHandlingOverride.from_dict(value)

        return cls(
            enabled=bool(src.get("enabled", True)),
            enable_completed_download_handling=bool(src.get("enable_completed_download_handling", True)),
            remove_completed_downloads=bool(src.get("remove_completed_downloads", False)),
            remove_failed_downloads=bool(src.get("remove_failed_downloads", False)),
            auto_redownload_failed=bool(src.get("auto_redownload_failed", False)),
            by_app=by_app,
            raw=src,
        )

    def override_for(
        self,
        app: "ServarrAppConfig | dict[str, Any] | str",
        canonicalize: Callable[[str], str] | None = None,
    ) -> ArrDownloadHandlingOverride:
        for key in app_lookup_keys(app, canonicalize=canonicalize):
            override = self.by_app.get(key)
            if override:
                return override
        return ArrDownloadHandlingOverride()

    def resolved_for(
        self,
        app: "ServarrAppConfig | dict[str, Any] | str",
        canonicalize: Callable[[str], str] | None = None,
    ) -> ArrDownloadHandlingResolvedPolicy:
        override = self.override_for(app, canonicalize=canonicalize)
        return ArrDownloadHandlingResolvedPolicy(
            enabled=self.enabled if override.enabled is None else bool(override.enabled),
            enable_completed_download_handling=(
                self.enable_completed_download_handling
                if override.enable_completed_download_handling is None
                else bool(override.enable_completed_download_handling)
            ),
            remove_completed_downloads=(
                self.remove_completed_downloads
                if override.remove_completed_downloads is None
                else bool(override.remove_completed_downloads)
            ),
            remove_failed_downloads=(
                self.remove_failed_downloads
                if override.remove_failed_downloads is None
                else bool(override.remove_failed_downloads)
            ),
            auto_redownload_failed=(
                self.auto_redownload_failed
                if override.auto_redownload_failed is None
                else bool(override.auto_redownload_failed)
            ),
        )


@dataclass(frozen=True)
class ArrQualityUpgradeOverride:
    enabled: bool | None = None
    allow_upgrades: bool | None = None
    disallow_quality_name_tokens: list[str] | None = None
    cutoff_preferred_name_tokens: list[str] | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "ArrQualityUpgradeOverride":
        src = dict(data or {})
        return cls(
            enabled=coerce_bool_opt(src.get("enabled")),
            allow_upgrades=coerce_bool_opt(src.get("allow_upgrades")),
            disallow_quality_name_tokens=coerce_str_list_opt(src.get("disallow_quality_name_tokens")),
            cutoff_preferred_name_tokens=coerce_str_list_opt(src.get("cutoff_preferred_name_tokens")),
        )


@dataclass(frozen=True)
class ArrQualityUpgradeResolvedPolicy:
    enabled: bool
    allow_upgrades: bool
    disallow_quality_name_tokens: list[str]
    cutoff_preferred_name_tokens: list[str]


@dataclass(frozen=True)
class ArrQualityUpgradePolicy:
    enabled: bool
    allow_upgrades: bool
    disallow_quality_name_tokens: list[str] = field(default_factory=list)
    cutoff_preferred_name_tokens: list[str] = field(default_factory=list)
    by_app: dict[str, ArrQualityUpgradeOverride] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(
        cls,
        data: dict[str, Any] | None,
        canonicalize: Callable[[str], str] | None = None,
    ) -> "ArrQualityUpgradePolicy":
        src = dict(data or {})
        by_app_raw = src.get("by_app") or {}
        by_app: dict[str, ArrQualityUpgradeOverride] = {}
        if isinstance(by_app_raw, dict):
            for key, value in by_app_raw.items():
                if not isinstance(value, dict):
                    continue
                token = normalize_by_app_key(str(key), canonicalize=canonicalize)
                if not token:
                    continue
                by_app[token] = ArrQualityUpgradeOverride.from_dict(value)

        disallow_tokens = coerce_str_list_opt(src.get("disallow_quality_name_tokens")) or ["2160", "4k", "uhd"]
        cutoff_tokens = coerce_str_list_opt(src.get("cutoff_preferred_name_tokens")) or ["1080"]

        return cls(
            enabled=bool(src.get("enabled", False)),
            allow_upgrades=bool(src.get("allow_upgrades", True)),
            disallow_quality_name_tokens=disallow_tokens,
            cutoff_preferred_name_tokens=cutoff_tokens,
            by_app=by_app,
            raw=src,
        )

    def override_for(
        self,
        app: "ServarrAppConfig | dict[str, Any] | str",
        canonicalize: Callable[[str], str] | None = None,
    ) -> ArrQualityUpgradeOverride:
        for key in app_lookup_keys(app, canonicalize=canonicalize):
            override = self.by_app.get(key)
            if override:
                return override
        return ArrQualityUpgradeOverride()

    def resolved_for(
        self,
        app: "ServarrAppConfig | dict[str, Any] | str",
        canonicalize: Callable[[str], str] | None = None,
    ) -> ArrQualityUpgradeResolvedPolicy:
        override = self.override_for(app, canonicalize=canonicalize)
        return ArrQualityUpgradeResolvedPolicy(
            enabled=self.enabled if override.enabled is None else bool(override.enabled),
            allow_upgrades=self.allow_upgrades if override.allow_upgrades is None else bool(override.allow_upgrades),
            disallow_quality_name_tokens=(
                list(self.disallow_quality_name_tokens)
                if override.disallow_quality_name_tokens is None
                else list(override.disallow_quality_name_tokens)
            ),
            cutoff_preferred_name_tokens=(
                list(self.cutoff_preferred_name_tokens)
                if override.cutoff_preferred_name_tokens is None
                else list(override.cutoff_preferred_name_tokens)
            ),
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
    supports_series_folder_management: bool = False

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
            supports_prowlarr_application=bool(merged.get("supports_prowlarr_application", True)),
            supports_download_clients=bool(merged.get("supports_download_clients", True)),
            supports_remote_path_mappings=bool(merged.get("supports_remote_path_mappings", True)),
            supports_discovery_lists=bool(merged.get("supports_discovery_lists", True)),
            supports_health_check=bool(merged.get("supports_health_check", True)),
            supports_series_folder_management=bool(merged.get("supports_series_folder_management", False)),
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

