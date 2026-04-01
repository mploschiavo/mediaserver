#!/usr/bin/env python3
"""Shared runtime service factories for Servarr-related operations."""

from __future__ import annotations

from bootstrap_services.arr_indexer_sync_service import ArrIndexerSyncService
from bootstrap_services.runtime_core import *  # noqa: F401,F403


def _arr_service(cfg=None) -> ArrService:
    service_cls = resolve_app_service_class(cfg, "arr_service", ArrService)
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
    service_cls = resolve_app_service_class(cfg, "qbittorrent_service", QBittorrentService)
    return service_cls(
        log=log,
        normalize_url=normalize_url,
        bool_cfg=bool_cfg,
        to_int=to_int,
        coerce_list=coerce_list,
    )


def _prowlarr_service(cfg=None) -> ProwlarrService:
    service_cls = resolve_app_service_class(cfg, "prowlarr_service", ProwlarrService)
    return service_cls(
        http_request=http_request,
        field_map=field_map,
        field_list=field_list,
        log=log,
    )


def _arr_indexer_sync_service(cfg=None) -> ArrIndexerSyncService:
    service_cls = resolve_app_service_class(cfg, "arr_indexer_sync_service", ArrIndexerSyncService)
    return service_cls(
        http_request=http_request,
        detect_arr_api_base=detect_arr_api_base,
        log=log,
    )


def _sabnzbd_service(cfg=None) -> SabnzbdService:
    service_cls = resolve_app_service_class(cfg, "sabnzbd_service", SabnzbdService)
    return service_cls(
        http_request=http_request,
        normalize_url=normalize_url,
        normalize_mapping_path=normalize_mapping_path,
        choose_category=choose_category,
        coerce_list=coerce_list,
        resolve_path=resolve_path,
        log=log,
    )


def _servarr_policy_service(cfg=None) -> ServarrPolicyService:
    service_cls = resolve_app_service_class(cfg, "servarr_policy_service", ServarrPolicyService)
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
        cfg, "arr_queue_cleanup_service", ArrQueueCleanupService
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
    service_cls = resolve_app_service_class(cfg, "health_service", HealthService)
    return service_cls(
        http_request=http_request,
        log=log,
    )


def _auth_service(cfg=None) -> AuthService:
    service_cls = resolve_app_service_class(cfg, "auth_service", AuthService)
    return service_cls(
        http_request=http_request,
        log=log,
        bool_cfg=bool_cfg,
    )
