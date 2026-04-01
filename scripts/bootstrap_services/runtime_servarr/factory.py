#!/usr/bin/env python3
"""Shared runtime service factories for Servarr-related operations."""

from __future__ import annotations

from typing import Any

from bootstrap_services.arr_indexer_sync_service import ArrIndexerSyncService
from bootstrap_services.runtime_core import (
    ArrQueueCleanupService,
    ArrService,
    AuthService,
    HealthService,
    SabnzbdService,
    ServarrPolicyService,
    bool_cfg,
    coerce_list,
    field_list,
    field_map,
    get_arr_quality_profile,
    http_request,
    log,
    normalize_remote_path_mappings,
    normalize_token,
    normalize_url,
    resolve_app_service_class,
    resolve_arr_quality_preferences,
    resolve_path,
    to_int,
)
from bootstrap_services.runtime_service_registry import get_runtime_binding


def _detect_arr_api_base(app_name, app_url, api_key):
    for version in ("v3", "v1"):
        status, _, _ = http_request(app_url, f"/api/{version}/system/status", api_key=api_key)
        if status == 200:
            return f"/api/{version}"

    raise RuntimeError(f"{app_name}: unable to detect API base (tried /api/v3 and /api/v1)")


def _choose_category(app_cfg, client_cfg):
    return _arr_service().choose_category(app_cfg, client_cfg)


def _normalize_mapping_path(path_value):
    return _arr_service().normalize_mapping_path(path_value)


def _arr_service(cfg=None) -> ArrService:
    service_cls = resolve_app_service_class("arr_service", ArrService)
    return service_cls(
        http_request=http_request,
        log=log,
        field_map=field_map,
        field_list=field_list,
        coerce_list=coerce_list,
        to_int=to_int,
        normalize_remote_path_mappings=normalize_remote_path_mappings,
    )


def _normalize_torrent_technology(token: str) -> str:
    raw = str(token or "").strip().lower()
    if not raw:
        return ""
    if raw in {"qbit", "qb"}:
        return "qbittorrent"
    return raw


def _normalize_usenet_technology(token: str) -> str:
    raw = str(token or "").strip().lower()
    if not raw:
        return ""
    if raw in {"sab"}:
        return "sabnzbd"
    if raw in {"nzb"}:
        return "nzbget"
    return raw


def _infer_torrent_client_technology(cfg=None) -> str:
    if isinstance(cfg, dict):
        for key in ("_technology_key", "_technology", "technology", "client_key"):
            value = _normalize_torrent_technology(str(cfg.get(key) or ""))
            if value:
                return value
        impl = _normalize_torrent_technology(str(cfg.get("implementation") or ""))
        if impl:
            return impl
        name = _normalize_torrent_technology(str(cfg.get("name") or ""))
        if name in {"qbittorrent", "transmission"}:
            return name
        url_token = str(cfg.get("url") or "").strip().lower()
        if "qbittorrent" in url_token or "qbit" in url_token:
            return "qbittorrent"
        if "transmission" in url_token:
            return "transmission"
    runtime_bound = _normalize_torrent_technology(get_runtime_binding("torrent_client"))
    if runtime_bound:
        return runtime_bound
    return ""


def _infer_usenet_client_technology(cfg=None) -> str:
    if isinstance(cfg, dict):
        for key in ("_technology_key", "_technology", "technology", "client_key"):
            value = _normalize_usenet_technology(str(cfg.get(key) or ""))
            if value:
                return value
        impl = _normalize_usenet_technology(str(cfg.get("implementation") or ""))
        if impl:
            return impl
        name = _normalize_usenet_technology(str(cfg.get("name") or ""))
        if name in {"sabnzbd", "nzbget", "jdownloader", "grabit"}:
            return name
        url_token = str(cfg.get("url") or "").strip().lower()
        if "sabnzbd" in url_token or "sab" in url_token:
            return "sabnzbd"
        if "nzbget" in url_token:
            return "nzbget"
        if "jdownloader" in url_token:
            return "jdownloader"
        if "grabit" in url_token:
            return "grabit"
    runtime_bound = _normalize_usenet_technology(get_runtime_binding("usenet_client"))
    if runtime_bound:
        return runtime_bound
    return ""


def _torrent_client_service(cfg=None) -> Any:
    technology = _infer_torrent_client_technology(cfg)
    if not technology:
        raise RuntimeError(
            "Unable to resolve active torrent client technology for runtime operation. "
            "Set technology_bindings.torrent_client and ensure runtime context is initialized."
        )
    service_cls = resolve_app_service_class(
        "torrent_client_service",
        object,
        technology=technology,
    )
    return service_cls(
        log=log,
        normalize_url=normalize_url,
        bool_cfg=bool_cfg,
        to_int=to_int,
        coerce_list=coerce_list,
    )


def _usenet_client_service(cfg=None) -> SabnzbdService:
    technology = _infer_usenet_client_technology(cfg) or "sabnzbd"
    service_cls = resolve_app_service_class(
        "usenet_client_service",
        SabnzbdService,
        technology=technology,
    )
    return service_cls(
        http_request=http_request,
        normalize_url=normalize_url,
        normalize_mapping_path=_normalize_mapping_path,
        choose_category=_choose_category,
        coerce_list=coerce_list,
        resolve_path=resolve_path,
        log=log,
    )


def _qbit_service(cfg=None) -> Any:
    """Compatibility alias retained while runtime code migrates to generic naming."""
    return _torrent_client_service(cfg)


def _prowlarr_service(cfg=None) -> Any:
    service_cls = resolve_app_service_class("prowlarr_service", object, technology="prowlarr")
    return service_cls(
        http_request=http_request,
        field_map=field_map,
        field_list=field_list,
        log=log,
    )


def _arr_indexer_sync_service(cfg=None) -> ArrIndexerSyncService:
    service_cls = resolve_app_service_class("arr_indexer_sync_service", ArrIndexerSyncService)
    return service_cls(
        http_request=http_request,
        detect_arr_api_base=_detect_arr_api_base,
        log=log,
    )


def _sabnzbd_service(cfg=None) -> SabnzbdService:
    """Compatibility alias retained while runtime code migrates to generic naming."""
    return _usenet_client_service(cfg)


def _servarr_policy_service(cfg=None) -> ServarrPolicyService:
    service_cls = resolve_app_service_class("servarr_policy_service", ServarrPolicyService)
    return service_cls(
        http_request=http_request,
        bool_cfg=bool_cfg,
        coerce_list=coerce_list,
        normalize_token=normalize_token,
        to_int=to_int,
        resolve_arr_quality_preferences=resolve_arr_quality_preferences,
        get_arr_quality_profile=get_arr_quality_profile,
        log=log,
    )


def _arr_queue_cleanup_service(cfg=None) -> ArrQueueCleanupService:
    service_cls = resolve_app_service_class("arr_queue_cleanup_service", ArrQueueCleanupService)
    return service_cls(
        http_request=http_request,
        bool_cfg=bool_cfg,
        coerce_list=coerce_list,
        to_int=to_int,
        normalize_token=normalize_token,
        resolve_arr_overrides_by_app=(
            lambda cfg_section, app_cfg: _servarr_policy_service().resolve_overrides_by_app(
                cfg_section,
                app_cfg,
            )
        ),
        log=log,
    )


def _health_service(cfg=None) -> HealthService:
    service_cls = resolve_app_service_class("health_service", HealthService)
    return service_cls(
        http_request=http_request,
        log=log,
    )


def _auth_service(cfg=None) -> AuthService:
    service_cls = resolve_app_service_class("auth_service", AuthService)
    return service_cls(
        http_request=http_request,
        log=log,
        bool_cfg=bool_cfg,
    )
