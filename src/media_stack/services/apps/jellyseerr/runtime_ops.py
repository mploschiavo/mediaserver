#!/usr/bin/env python3
"""Jellyseerr runtime orchestration boundary."""

from __future__ import annotations

from typing import Any

import media_stack.services.apps.servarr.runtime.arr_ops as _servarr_arr_ops
from media_stack.services.apps.servarr.runtime.common import (
    choose_profile,
    choose_root_folder,
    find_existing_servarr,
    get_arr_app,
    get_arr_quality_profile,
)
from media_stack.services.runtime_platform import (
    bool_cfg,
    coerce_list,
    http_request,
    log,
    normalize_base_path,
    normalize_url,
    parse_service_url,
    resolve_app_service_class,
    resolve_path,
    to_int,
    wait_for_service,
)
from media_stack.services.apps.jellyfin.api_key_db import (
    resolve_jellyfin_api_key as _resolve_jellyfin_api_key,
)
from media_stack.services.runtime_secrets import read_api_key, read_json_file

from .service import JellyseerrService

detect_arr_api_base = _servarr_arr_ops.detect_arr_api_base


def read_jellyseerr_api_key(config_root, timeout_seconds=120):
    return read_api_key(config_root, "jellyseerr")


def resolve_jellyfin_api_key(jellyfin_cfg, config_root):
    return _resolve_jellyfin_api_key(
        jellyfin_cfg,
        config_root,
        log=log,
        bool_cfg=bool_cfg,
        coerce_list=coerce_list,
        resolve_path=resolve_path,
    )


def _get_arr_root_folder_path(
    app_name: str,
    app_url: str,
    api_base: str,
    api_key: str,
    preferred_root: str,
) -> str:
    status, root_folders, body = http_request(app_url, f"{api_base}/rootfolder", api_key=api_key)
    if status != 200 or not isinstance(root_folders, list):
        raise RuntimeError(f"{app_name}: failed to list root folders (HTTP {status}): {body}")
    chosen = choose_root_folder(root_folders, preferred_root)
    if chosen:
        return chosen
    preferred = str(preferred_root or "").rstrip("/")
    if preferred:
        return preferred
    raise RuntimeError(f"{app_name}: no root folder could be resolved.")


def _get_sonarr_language_profile_id(
    sonarr_url: str,
    sonarr_api_base: str,
    sonarr_api_key: str,
) -> int:
    status, language_profiles, _ = http_request(
        sonarr_url, f"{sonarr_api_base}/languageprofile", api_key=sonarr_api_key
    )
    if status == 200 and isinstance(language_profiles, list) and language_profiles:
        return to_int(language_profiles[0].get("id"), 1)
    return 1


def _jellyseerr_service(cfg=None) -> JellyseerrService:
    bindings = (cfg or {}).get("technology_bindings") if isinstance(cfg, dict) else {}
    request_manager = ""
    if isinstance(bindings, dict):
        request_manager = str(bindings.get("request_manager") or "").strip().lower()
    if not request_manager:
        request_manager = "jellyseerr"
    service_cls = resolve_app_service_class(
        "request_manager_service",
        JellyseerrService,
        technology=request_manager,
    )
    return service_cls(
        log=log,
        bool_cfg=bool_cfg,
        normalize_url=normalize_url,
        wait_for_service=wait_for_service,
        resolve_jellyfin_api_key=resolve_jellyfin_api_key,
        parse_service_url=parse_service_url,
        to_int=to_int,
        coerce_list=coerce_list,
        choose_profile=choose_profile,
        choose_root_folder=choose_root_folder,
        normalize_base_path=normalize_base_path,
        find_existing_servarr=find_existing_servarr,
        read_json_file=read_json_file,
        get_arr_app=get_arr_app,
        detect_arr_api_base=detect_arr_api_base,
        get_arr_quality_profile=get_arr_quality_profile,
        get_arr_root_folder_path=_get_arr_root_folder_path,
        get_sonarr_language_profile_id=_get_sonarr_language_profile_id,
        read_jellyseerr_api_key=read_jellyseerr_api_key,
        http_request=http_request,
    )


def configure_jellyseerr(
    cfg: dict[str, Any],
    arr_apps: list[dict[str, Any]],
    app_keys: dict[str, str],
    config_root: str,
    wait_timeout: int,
) -> None:
    _jellyseerr_service(cfg).configure(
        cfg=cfg,
        arr_apps=arr_apps,
        app_keys=app_keys,
        config_root=config_root,
        wait_timeout=wait_timeout,
    )
