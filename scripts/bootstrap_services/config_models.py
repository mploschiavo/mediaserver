"""Typed config models for bootstrap sections."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


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
class DownloadClientsConfig:
    clients: dict[str, DownloadClientConfig] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "DownloadClientsConfig":
        if data is not None and not isinstance(data, dict):
            raise ValueError("download_clients must be an object")
        src = dict(data or {})
        clients: dict[str, DownloadClientConfig] = {}
        for key, value in src.items():
            token = str(key or "").strip().lower()
            if not token or not isinstance(value, dict):
                continue
            clients[token] = DownloadClientConfig.from_dict(value)
        return cls(clients=clients, raw=src)

    def get(self, key: str) -> DownloadClientConfig | None:
        token = str(key or "").strip().lower()
        if not token:
            return None
        return self.clients.get(token)

    def configured_keys(self) -> list[str]:
        return sorted(self.clients.keys())


@dataclass(frozen=True)
class TechnologyBindingsConfig:
    torrent_client: str = "qbittorrent"
    usenet_client: str = "sabnzbd"
    media_server: str = "jellyfin"
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(
        cls,
        data: dict[str, Any] | None,
        defaults: dict[str, Any] | None = None,
    ) -> "TechnologyBindingsConfig":
        src = dict(data or {})
        default_src = dict(defaults or {})
        default_torrent = (
            str(default_src.get("torrent_client", "qbittorrent")).strip().lower() or "qbittorrent"
        )
        default_usenet = (
            str(default_src.get("usenet_client", "sabnzbd")).strip().lower() or "sabnzbd"
        )
        default_media_server = (
            str(default_src.get("media_server", "jellyfin")).strip().lower() or "jellyfin"
        )
        return cls(
            torrent_client=str(src.get("torrent_client", default_torrent)).strip().lower()
            or default_torrent,
            usenet_client=str(src.get("usenet_client", default_usenet)).strip().lower()
            or default_usenet,
            media_server=str(src.get("media_server", default_media_server)).strip().lower()
            or default_media_server,
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
                typed_items = [
                    ArrDiscoveryListEntry.from_dict(item)
                    for item in value
                    if isinstance(item, dict)
                ]
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


def _coerce_bool_opt(value: Any) -> bool | None:
    if value is None:
        return None
    return bool(value)


def _coerce_str_list_opt(value: Any) -> list[str] | None:
    if value is None:
        return None
    if isinstance(value, list):
        return [str(x).strip() for x in value if str(x).strip()]
    token = str(value).strip()
    return [token] if token else []


def _normalize_by_app_key(
    key: str,
    canonicalize: Callable[[str], str] | None = None,
) -> str:
    raw = str(key or "").strip()
    if not raw:
        return ""
    if canonicalize:
        candidate = str(canonicalize(raw)).strip()
        if candidate:
            return candidate.lower()
    return raw.lower()


def _app_lookup_keys(
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
            normalized = _normalize_by_app_key(variant, canonicalize=canonicalize)
            if normalized and normalized not in candidates:
                candidates.append(normalized)
    return tuple(candidates)


@dataclass(frozen=True)
class ArrMediaManagementOverride:
    enabled: bool | None = None
    copy_using_hardlinks: bool | None = None
    create_empty_series_folders: bool | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "ArrMediaManagementOverride":
        src = dict(data or {})
        return cls(
            enabled=_coerce_bool_opt(src.get("enabled")),
            copy_using_hardlinks=_coerce_bool_opt(src.get("copy_using_hardlinks")),
            create_empty_series_folders=_coerce_bool_opt(src.get("create_empty_series_folders")),
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
                token = _normalize_by_app_key(str(key), canonicalize=canonicalize)
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
        for key in _app_lookup_keys(app, canonicalize=canonicalize):
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
            enabled=_coerce_bool_opt(src.get("enabled")),
            enable_completed_download_handling=_coerce_bool_opt(
                src.get("enable_completed_download_handling")
            ),
            remove_completed_downloads=_coerce_bool_opt(src.get("remove_completed_downloads")),
            remove_failed_downloads=_coerce_bool_opt(src.get("remove_failed_downloads")),
            auto_redownload_failed=_coerce_bool_opt(src.get("auto_redownload_failed")),
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
                token = _normalize_by_app_key(str(key), canonicalize=canonicalize)
                if not token:
                    continue
                by_app[token] = ArrDownloadHandlingOverride.from_dict(value)

        return cls(
            enabled=bool(src.get("enabled", True)),
            enable_completed_download_handling=bool(
                src.get("enable_completed_download_handling", True)
            ),
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
        for key in _app_lookup_keys(app, canonicalize=canonicalize):
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
            enabled=_coerce_bool_opt(src.get("enabled")),
            allow_upgrades=_coerce_bool_opt(src.get("allow_upgrades")),
            disallow_quality_name_tokens=_coerce_str_list_opt(
                src.get("disallow_quality_name_tokens")
            ),
            cutoff_preferred_name_tokens=_coerce_str_list_opt(
                src.get("cutoff_preferred_name_tokens")
            ),
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
                token = _normalize_by_app_key(str(key), canonicalize=canonicalize)
                if not token:
                    continue
                by_app[token] = ArrQualityUpgradeOverride.from_dict(value)

        disallow_tokens = _coerce_str_list_opt(src.get("disallow_quality_name_tokens")) or [
            "2160",
            "4k",
            "uhd",
        ]
        cutoff_tokens = _coerce_str_list_opt(src.get("cutoff_preferred_name_tokens")) or ["1080"]

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
        for key in _app_lookup_keys(app, canonicalize=canonicalize):
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
            allow_upgrades=(
                self.allow_upgrades
                if override.allow_upgrades is None
                else bool(override.allow_upgrades)
            ),
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
class JellyfinLibrariesConfig:
    enabled: bool
    required: bool
    url: str
    libraries: list[dict[str, Any]] = field(default_factory=list)
    tuning: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "JellyfinLibrariesConfig":
        src = dict(data or {})
        libraries = [x for x in (src.get("libraries") or []) if isinstance(x, dict)]
        return cls(
            enabled=bool(src.get("enabled", False)),
            required=bool(src.get("required", False)),
            url=str(src.get("url", "http://jellyfin:8096")).strip(),
            libraries=libraries,
            tuning=dict(src.get("tuning") or {}),
            raw=src,
        )


@dataclass(frozen=True)
class JellyfinPluginsConfig:
    enabled: bool
    required: bool
    url: str
    repositories: list[dict[str, Any]] = field(default_factory=list)
    install: list[Any] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "JellyfinPluginsConfig":
        src = dict(data or {})
        repositories = [x for x in (src.get("repositories") or []) if isinstance(x, dict)]
        install = list(src.get("install") or [])
        return cls(
            enabled=bool(src.get("enabled", False)),
            required=bool(src.get("required", False)),
            url=str(src.get("url", "http://jellyfin:8096")).strip(),
            repositories=repositories,
            install=install,
            raw=src,
        )


@dataclass(frozen=True)
class JellyfinPlaybackConfig:
    enabled: bool
    required: bool
    url: str
    user_defaults: dict[str, Any] = field(default_factory=dict)
    server_defaults: dict[str, Any] = field(default_factory=dict)
    display_preferences: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "JellyfinPlaybackConfig":
        src = dict(data or {})
        return cls(
            enabled=bool(src.get("enabled", False)),
            required=bool(src.get("required", False)),
            url=str(src.get("url", "http://jellyfin:8096")).strip(),
            user_defaults=dict(src.get("user_defaults") or {}),
            server_defaults=dict(src.get("server_defaults") or {}),
            display_preferences=dict(src.get("display_preferences") or {}),
            raw=src,
        )


@dataclass(frozen=True)
class JellyfinPrewarmConfig:
    enabled: bool
    required: bool
    url: str
    refresh_library: bool
    refresh_channels: bool
    refresh_guide: bool
    library_refresh_query: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "JellyfinPrewarmConfig":
        src = dict(data or {})
        return cls(
            enabled=bool(src.get("enabled", False)),
            required=bool(src.get("required", False)),
            url=str(src.get("url", "http://jellyfin:8096")).strip(),
            refresh_library=bool(src.get("refresh_library", True)),
            refresh_channels=bool(src.get("refresh_channels", True)),
            refresh_guide=bool(src.get("refresh_guide", True)),
            library_refresh_query=dict(src.get("library_refresh_query") or {}),
            raw=src,
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
            supports_series_folder_management=bool(
                merged.get("supports_series_folder_management", False)
            ),
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
