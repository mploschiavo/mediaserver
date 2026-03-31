"""Jellyfin prewarm bootstrap service."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

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
            or "http://jellyfin:8096"
        )

        jellyfin_url = d.normalize_url(api_cfg.get("url"))
        d.wait_for_service("Jellyfin", jellyfin_url, "/System/Info/Public", wait_timeout)
        jellyfin_api_key = d.resolve_api_key(api_cfg, config_root)

        refresh_params = prewarm_cfg.get("library_refresh_query")
        if not isinstance(refresh_params, dict):
            refresh_params = {
                "metadataRefreshMode": "FullRefresh",
                "imageRefreshMode": "FullRefresh",
                "replaceAllMetadata": "false",
                "replaceAllImages": "false",
            }

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

        d.log("[OK] Jellyfin prewarm: reconcile complete")
