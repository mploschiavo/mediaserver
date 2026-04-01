#!/usr/bin/env python3
"""Shared runtime service factories for Servarr-related operations."""

from __future__ import annotations

from bootstrap_services.apps.prowlarr.service import ProwlarrService
from bootstrap_services.arr_indexer_sync_service import ArrIndexerSyncService
from bootstrap_services.runtime_core import (
    ArrQueueCleanupService,
    ArrService,
    AuthService,
    HealthService,
    QBittorrentService,
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


def _qbit_service(cfg=None) -> QBittorrentService:
    service_cls = resolve_app_service_class("qbittorrent_service", QBittorrentService)
    return service_cls(
        log=log,
        normalize_url=normalize_url,
        bool_cfg=bool_cfg,
        to_int=to_int,
        coerce_list=coerce_list,
    )


def _prowlarr_service(cfg=None) -> ProwlarrService:
    service_cls = resolve_app_service_class("prowlarr_service", ProwlarrService)
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
    service_cls = resolve_app_service_class("sabnzbd_service", SabnzbdService)
    return service_cls(
        http_request=http_request,
        normalize_url=normalize_url,
        normalize_mapping_path=_normalize_mapping_path,
        choose_category=_choose_category,
        coerce_list=coerce_list,
        resolve_path=resolve_path,
        log=log,
    )


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
    service_cls = resolve_app_service_class(
        "arr_queue_cleanup_service", ArrQueueCleanupService
    )
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
