"""Typed models for Jellyfin bootstrap config sections."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from media_stack.api.services.registry import service_internal_url


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
            url=str(src.get("url", service_internal_url("jellyfin"))).strip(),
            tuners=tuners,
            guides=guides,
            raw=src,
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
            url=str(src.get("url", service_internal_url("jellyfin"))).strip(),
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
            url=str(src.get("url", service_internal_url("jellyfin"))).strip(),
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
            url=str(src.get("url", service_internal_url("jellyfin"))).strip(),
            user_defaults=dict(src.get("user_defaults") or {}),
            server_defaults=dict(src.get("server_defaults") or {}),
            display_preferences=dict(src.get("display_preferences") or {}),
            raw=src,
        )


@dataclass(frozen=True)
class JellyfinBookSidecarArtworkConfig:
    enabled: bool
    books_root_path: str
    books_root_paths: list[str]
    output_filename: str
    replace_existing: bool
    write_per_book_sidecars: bool
    per_book_output_extension: str
    preferred_filenames: list[str]
    image_extensions: list[str]
    max_books_per_run: int
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "JellyfinBookSidecarArtworkConfig":
        src = dict(data or {})
        roots = src.get("books_root_paths")
        root_list = (
            [str(x).strip() for x in roots if str(x).strip()] if isinstance(roots, list) else []
        )
        if src.get("books_root_path"):
            root_list = [
                str(src.get("books_root_path")).strip(),
                *[x for x in root_list if x != str(src.get("books_root_path")).strip()],
            ]
        return cls(
            enabled=bool(src.get("enabled", True)),
            books_root_path=str(src.get("books_root_path", "/srv-stack/media/books")).strip(),
            books_root_paths=root_list,
            output_filename=str(src.get("output_filename", "folder.jpg")).strip() or "folder.jpg",
            replace_existing=bool(src.get("replace_existing", False)),
            write_per_book_sidecars=bool(src.get("write_per_book_sidecars", True)),
            per_book_output_extension=str(src.get("per_book_output_extension", ".jpg")).strip()
            or ".jpg",
            preferred_filenames=[
                str(x).strip() for x in (src.get("preferred_filenames") or []) if str(x).strip()
            ],
            image_extensions=[
                str(x).strip() for x in (src.get("image_extensions") or []) if str(x).strip()
            ],
            max_books_per_run=int(src.get("max_books_per_run", 500) or 500),
            raw=src,
        )


@dataclass(frozen=True)
class JellyfinMusicSidecarArtworkConfig:
    enabled: bool
    music_root_path: str
    music_root_paths: list[str]
    output_filename: str
    replace_existing: bool
    preferred_filenames: list[str]
    image_extensions: list[str]
    audio_extensions: list[str]
    max_albums_per_run: int
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "JellyfinMusicSidecarArtworkConfig":
        src = dict(data or {})
        roots = src.get("music_root_paths")
        root_list = (
            [str(x).strip() for x in roots if str(x).strip()] if isinstance(roots, list) else []
        )
        if src.get("music_root_path"):
            root_list = [
                str(src.get("music_root_path")).strip(),
                *[x for x in root_list if x != str(src.get("music_root_path")).strip()],
            ]
        return cls(
            enabled=bool(src.get("enabled", True)),
            music_root_path=str(src.get("music_root_path", "/srv-stack/media/music")).strip(),
            music_root_paths=root_list,
            output_filename=str(src.get("output_filename", "folder.jpg")).strip() or "folder.jpg",
            replace_existing=bool(src.get("replace_existing", False)),
            preferred_filenames=[
                str(x).strip() for x in (src.get("preferred_filenames") or []) if str(x).strip()
            ],
            image_extensions=[
                str(x).strip() for x in (src.get("image_extensions") or []) if str(x).strip()
            ],
            audio_extensions=[
                str(x).strip() for x in (src.get("audio_extensions") or []) if str(x).strip()
            ],
            max_albums_per_run=int(src.get("max_albums_per_run", 1000) or 1000),
            raw=src,
        )


@dataclass(frozen=True)
class JellyfinMetadataBackfillConfig:
    enabled: bool
    required: bool
    libraries: list[str]
    refresh_missing_primary_image: bool
    refresh_missing_overview: bool
    refresh_collection_folder_images: bool
    max_refresh_per_library: int
    sample_multiplier: int
    refresh_query: dict[str, Any]
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "JellyfinMetadataBackfillConfig":
        src = dict(data or {})
        return cls(
            enabled=bool(src.get("enabled", True)),
            required=bool(src.get("required", False)),
            libraries=[str(x).strip() for x in (src.get("libraries") or []) if str(x).strip()],
            refresh_missing_primary_image=bool(src.get("refresh_missing_primary_image", True)),
            refresh_missing_overview=bool(src.get("refresh_missing_overview", True)),
            refresh_collection_folder_images=bool(
                src.get("refresh_collection_folder_images", True)
            ),
            max_refresh_per_library=int(src.get("max_refresh_per_library", 80) or 80),
            sample_multiplier=int(src.get("sample_multiplier", 4) or 4),
            refresh_query=dict(src.get("refresh_query") or {}),
            raw=src,
        )


@dataclass(frozen=True)
class JellyfinArtworkHealthCheckConfig:
    enabled: bool
    required: bool
    libraries: list[str]
    max_items_per_library: int
    warn_below_coverage_percent: float
    fail_below_coverage_percent: float
    wait_after_refresh_seconds: int
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "JellyfinArtworkHealthCheckConfig":
        src = dict(data or {})
        return cls(
            enabled=bool(src.get("enabled", True)),
            required=bool(src.get("required", False)),
            libraries=[str(x).strip() for x in (src.get("libraries") or []) if str(x).strip()],
            max_items_per_library=int(src.get("max_items_per_library", 400) or 400),
            warn_below_coverage_percent=float(src.get("warn_below_coverage_percent", 70.0) or 70.0),
            fail_below_coverage_percent=float(src.get("fail_below_coverage_percent", 30.0) or 30.0),
            wait_after_refresh_seconds=int(src.get("wait_after_refresh_seconds", 20) or 20),
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
    book_sidecar_artwork: dict[str, Any] = field(default_factory=dict)
    music_sidecar_artwork: dict[str, Any] = field(default_factory=dict)
    metadata_backfill: dict[str, Any] = field(default_factory=dict)
    artwork_health_check: dict[str, Any] = field(default_factory=dict)
    library_refresh_query: dict[str, Any] = field(default_factory=dict)
    book_sidecar_artwork_typed: JellyfinBookSidecarArtworkConfig = field(
        default_factory=lambda: JellyfinBookSidecarArtworkConfig.from_dict(None)
    )
    music_sidecar_artwork_typed: JellyfinMusicSidecarArtworkConfig = field(
        default_factory=lambda: JellyfinMusicSidecarArtworkConfig.from_dict(None)
    )
    metadata_backfill_typed: JellyfinMetadataBackfillConfig = field(
        default_factory=lambda: JellyfinMetadataBackfillConfig.from_dict(None)
    )
    artwork_health_check_typed: JellyfinArtworkHealthCheckConfig = field(
        default_factory=lambda: JellyfinArtworkHealthCheckConfig.from_dict(None)
    )
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "JellyfinPrewarmConfig":
        src = dict(data or {})
        book_sidecar = dict(src.get("book_sidecar_artwork") or {})
        music_sidecar = dict(src.get("music_sidecar_artwork") or {})
        metadata_backfill = dict(src.get("metadata_backfill") or {})
        artwork_health_check = dict(src.get("artwork_health_check") or {})
        return cls(
            enabled=bool(src.get("enabled", False)),
            required=bool(src.get("required", False)),
            url=str(src.get("url", service_internal_url("jellyfin"))).strip(),
            refresh_library=bool(src.get("refresh_library", True)),
            refresh_channels=bool(src.get("refresh_channels", True)),
            refresh_guide=bool(src.get("refresh_guide", True)),
            book_sidecar_artwork=book_sidecar,
            music_sidecar_artwork=music_sidecar,
            metadata_backfill=metadata_backfill,
            artwork_health_check=artwork_health_check,
            library_refresh_query=dict(src.get("library_refresh_query") or {}),
            book_sidecar_artwork_typed=JellyfinBookSidecarArtworkConfig.from_dict(book_sidecar),
            music_sidecar_artwork_typed=JellyfinMusicSidecarArtworkConfig.from_dict(music_sidecar),
            metadata_backfill_typed=JellyfinMetadataBackfillConfig.from_dict(metadata_backfill),
            artwork_health_check_typed=JellyfinArtworkHealthCheckConfig.from_dict(
                artwork_health_check
            ),
            raw=src,
        )
