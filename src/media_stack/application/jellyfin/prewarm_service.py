"""Jellyfin prewarm bootstrap service."""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from media_stack.domain.jellyfin.prewarm.metadata_ops import (
    item_has_artwork,
    item_has_overview,
    run_artwork_health_check,
    run_metadata_backfill,
)
from media_stack.domain.jellyfin.prewarm.sidecar_ops import (
    candidate_image_paths,
    ensure_book_sidecar_artwork,
    ensure_music_sidecar_artwork,
    extract_epub_cover_bytes,
    normalize_text_list,
    resolve_books_root_path,
    resolve_music_root_path,
)
from media_stack.api.services.registry import service_internal_url

LogFn = Callable[[str], None]
BoolCfgFn = Callable[[dict[str, Any], str, bool], bool]
NormalizeUrlFn = Callable[[str], str]
WaitForServiceFn = Callable[[str, str, str, int], None]
ResolveApiKeyFn = Callable[[dict[str, Any], str], str]
JellyfinRequestFn = Callable[..., tuple[int, Any, str]]
BuildQueryPathFn = Callable[[str, dict[str, Any]], str]
TriggerLiveTvRefreshFn = Callable[[str, str, str, str], tuple[bool, str]]


@dataclass
class JellyfinPrewarmDependencies:
    log: LogFn
    bool_cfg: BoolCfgFn
    normalize_url: NormalizeUrlFn
    wait_for_service: WaitForServiceFn
    resolve_api_key: ResolveApiKeyFn
    jellyfin_request: JellyfinRequestFn
    build_query_path: BuildQueryPathFn
    trigger_livetv_refresh: TriggerLiveTvRefreshFn


@dataclass
class JellyfinPrewarmService:
    deps: JellyfinPrewarmDependencies

    @staticmethod
    def _normalize_text_list(values: Any, fallback: list[str] | None = None) -> list[str]:
        return normalize_text_list(values, fallback)

    @staticmethod
    def _candidate_image_paths(
        directory: Path,
        preferred_names: list[str],
        allowed_extensions: set[str],
    ) -> list[Path]:
        return candidate_image_paths(directory, preferred_names, allowed_extensions)

    @staticmethod
    def _extract_epub_cover_bytes(epub_path: Path) -> bytes | None:
        return extract_epub_cover_bytes(epub_path)

    def _resolve_books_root_path(
        self, sidecar_cfg: dict[str, Any]
    ) -> tuple[Path | None, list[Path]]:
        return resolve_books_root_path(self, sidecar_cfg)

    def _resolve_music_root_path(
        self, sidecar_cfg: dict[str, Any]
    ) -> tuple[Path | None, list[Path]]:
        return resolve_music_root_path(self, sidecar_cfg)

    def _ensure_book_sidecar_artwork(self, prewarm_cfg: dict[str, Any]) -> None:
        ensure_book_sidecar_artwork(self, prewarm_cfg)

    def _ensure_music_sidecar_artwork(self, prewarm_cfg: dict[str, Any]) -> None:
        ensure_music_sidecar_artwork(self, prewarm_cfg)

    @staticmethod
    def _item_has_artwork(item: dict[str, Any]) -> bool:
        return item_has_artwork(item)

    @staticmethod
    def _item_has_overview(item: dict[str, Any]) -> bool:
        return item_has_overview(item)

    def _run_metadata_backfill(
        self,
        prewarm_cfg: dict[str, Any],
        jellyfin_url: str,
        jellyfin_api_key: str,
    ) -> None:
        run_metadata_backfill(self, prewarm_cfg, jellyfin_url, jellyfin_api_key)

    def _run_artwork_health_check(
        self,
        prewarm_cfg: dict[str, Any],
        jellyfin_url: str,
        jellyfin_api_key: str,
    ) -> None:
        run_artwork_health_check(self, prewarm_cfg, jellyfin_url, jellyfin_api_key)

    def ensure(self, cfg: dict[str, Any], config_root: str, wait_timeout: int) -> None:
        d = self.deps
        prewarm_cfg = cfg.get("jellyfin_prewarm") or {}
        if not d.bool_cfg(prewarm_cfg, "enabled", False):
            return

        libraries_cfg = cfg.get("jellyfin_libraries") or {}
        livetv_cfg = cfg.get("jellyfin_livetv") or {}
        api_cfg = dict(libraries_cfg)
        if not isinstance(api_cfg, dict):
            api_cfg = {}
        for key in (
            "api_key_env",
            "auto_discover_api_key_from_db",
            "api_key_db_path",
            "api_key_name_preference",
        ):
            if key in prewarm_cfg:
                api_cfg[key] = prewarm_cfg.get(key)
        api_cfg["url"] = (
            prewarm_cfg.get("url")
            or libraries_cfg.get("url")
            or livetv_cfg.get("url")
            or service_internal_url("jellyfin")
        )

        jellyfin_url = d.normalize_url(api_cfg.get("url"))
        d.wait_for_service("Jellyfin", jellyfin_url, "/System/Info/Public", wait_timeout)
        jellyfin_api_key = d.resolve_api_key(api_cfg, config_root)

        self._ensure_book_sidecar_artwork(prewarm_cfg)
        self._ensure_music_sidecar_artwork(prewarm_cfg)

        refresh_params = prewarm_cfg.get("library_refresh_query")
        if not isinstance(refresh_params, dict):
            refresh_params = {
                "metadataRefreshMode": "FullRefresh",
                "imageRefreshMode": "FullRefresh",
                "replaceAllMetadata": "false",
                "replaceAllImages": "false",
            }

        refresh_requested = False
        if d.bool_cfg(prewarm_cfg, "refresh_library", True):
            refresh_path = d.build_query_path("/Library/Refresh", refresh_params)
            status, _, body = d.jellyfin_request(
                jellyfin_url,
                refresh_path,
                jellyfin_api_key,
                method="POST",
            )
            if status in (200, 201, 202, 204):
                d.log("[OK] Jellyfin prewarm: requested library metadata/artwork refresh")
                refresh_requested = True
            else:
                raise RuntimeError(
                    f"Jellyfin prewarm: failed requesting library refresh (HTTP {status}): {body}"
                )

        if d.bool_cfg(prewarm_cfg, "refresh_channels", True):
            ok, detail = d.trigger_livetv_refresh(
                jellyfin_url,
                jellyfin_api_key,
                "/LiveTv/RefreshChannels",
                "Live TV channel refresh",
            )
            if ok:
                d.log(f"[OK] Jellyfin prewarm: {detail}")
            else:
                d.log(f"[WARN] Jellyfin prewarm: {detail}")

        if d.bool_cfg(prewarm_cfg, "refresh_guide", True):
            ok, detail = d.trigger_livetv_refresh(
                jellyfin_url,
                jellyfin_api_key,
                "/LiveTv/RefreshGuide",
                "Live TV guide refresh",
            )
            if ok:
                d.log(f"[OK] Jellyfin prewarm: {detail}")
            else:
                d.log(f"[WARN] Jellyfin prewarm: {detail}")

        health_cfg = prewarm_cfg.get("artwork_health_check")
        if not isinstance(health_cfg, dict):
            health_cfg = {}
        if refresh_requested and d.bool_cfg(health_cfg, "enabled", True):
            try:
                wait_seconds = int(health_cfg.get("wait_after_refresh_seconds") or 20)
            except Exception:
                wait_seconds = 20
            if wait_seconds > 0:
                d.log(
                    "[INFO] Jellyfin prewarm: waiting for refresh settle before artwork health check "
                    f"({wait_seconds}s)"
                )
                time.sleep(wait_seconds)

        self._run_metadata_backfill(prewarm_cfg, jellyfin_url, jellyfin_api_key)
        self._run_artwork_health_check(prewarm_cfg, jellyfin_url, jellyfin_api_key)

        d.log("[OK] Jellyfin prewarm: reconcile complete")
