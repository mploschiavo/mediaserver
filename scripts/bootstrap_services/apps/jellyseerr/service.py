"""Jellyseerr bootstrap orchestration service."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from . import operations as _ops

HttpRequestFn = Callable[..., tuple[int, Any, str]]
LogFn = Callable[[str], None]
BoolCfgFn = Callable[[dict[str, Any], str, bool], bool]
NormalizeUrlFn = Callable[[str], str]
WaitForServiceFn = Callable[[str, str, str, int], None]
ResolveJellyfinApiKeyFn = Callable[[dict[str, Any], str], str]
ParseServiceUrlFn = Callable[[str, int], dict[str, Any]]
ToIntFn = Callable[[Any, Any], Any]
CoerceListFn = Callable[[Any], list[Any]]
ChooseProfileFn = Callable[..., dict[str, Any] | None]
ChooseRootFolderFn = Callable[[list[dict[str, Any]], str], str]
NormalizeBasePathFn = Callable[[str], str]
FindExistingServarrFn = Callable[..., dict[str, Any] | None]
ReadJsonFileFn = Callable[[Path], Any]
GetArrAppFn = Callable[[list[dict[str, Any]], str], dict[str, Any] | None]
DetectArrApiBaseFn = Callable[[str, str, str], str]
GetArrQualityProfileFn = Callable[..., dict[str, Any]]
GetArrRootFolderPathFn = Callable[..., str]
GetSonarrLanguageProfileIdFn = Callable[[str, str, str], int]
ReadJellyseerrApiKeyFn = Callable[[str, int], str]


@dataclass
class JellyseerrService:
    log: LogFn
    bool_cfg: BoolCfgFn
    normalize_url: NormalizeUrlFn
    wait_for_service: WaitForServiceFn
    resolve_jellyfin_api_key: ResolveJellyfinApiKeyFn
    parse_service_url: ParseServiceUrlFn
    to_int: ToIntFn
    coerce_list: CoerceListFn
    choose_profile: ChooseProfileFn
    choose_root_folder: ChooseRootFolderFn
    normalize_base_path: NormalizeBasePathFn
    find_existing_servarr: FindExistingServarrFn
    read_json_file: ReadJsonFileFn
    get_arr_app: GetArrAppFn
    detect_arr_api_base: DetectArrApiBaseFn
    get_arr_quality_profile: GetArrQualityProfileFn
    get_arr_root_folder_path: GetArrRootFolderPathFn
    get_sonarr_language_profile_id: GetSonarrLanguageProfileIdFn
    read_jellyseerr_api_key: ReadJellyseerrApiKeyFn
    http_request: HttpRequestFn

    def ensure_main_settings(
        self,
        jellyseerr_url: str,
        jellyseerr_key: str,
        jelly_cfg: dict[str, Any],
    ) -> None:
        _ops.ensure_main_settings(self, jellyseerr_url, jellyseerr_key, jelly_cfg)

    def ensure_jellyfin_settings(
        self,
        jellyseerr_url: str,
        jellyseerr_key: str,
        jelly_cfg: dict[str, Any],
        config_root: str,
    ) -> None:
        _ops.ensure_jellyfin_settings(self, jellyseerr_url, jellyseerr_key, jelly_cfg, config_root)

    def ensure_radarr(
        self,
        jellyseerr_url: str,
        jellyseerr_key: str,
        radarr_app_cfg: dict[str, Any],
        radarr_api_key: str,
        jelly_cfg: dict[str, Any],
    ) -> None:
        _ops.ensure_radarr(self, jellyseerr_url, jellyseerr_key, radarr_app_cfg, radarr_api_key, jelly_cfg)

    def ensure_sonarr(
        self,
        jellyseerr_url: str,
        jellyseerr_key: str,
        sonarr_app_cfg: dict[str, Any],
        sonarr_api_key: str,
        jelly_cfg: dict[str, Any],
    ) -> None:
        _ops.ensure_sonarr(self, jellyseerr_url, jellyseerr_key, sonarr_app_cfg, sonarr_api_key, jelly_cfg)

    def configure_via_settings_file(
        self,
        cfg: dict[str, Any],
        arr_apps: list[dict[str, Any]],
        app_keys: dict[str, str],
        config_root: str,
    ) -> None:
        _ops.configure_via_settings_file(self, cfg, arr_apps, app_keys, config_root)

    @staticmethod
    def permission_error(exc: Exception) -> bool:
        return _ops.permission_error(exc)

    def configure(
        self,
        cfg: dict[str, Any],
        arr_apps: list[dict[str, Any]],
        app_keys: dict[str, str],
        config_root: str,
        wait_timeout: int,
    ) -> None:
        _ops.configure(self, cfg, arr_apps, app_keys, config_root, wait_timeout)
