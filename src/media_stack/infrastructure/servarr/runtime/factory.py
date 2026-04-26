#!/usr/bin/env python3
"""Shared runtime service factories for Servarr-related operations."""

from __future__ import annotations

from typing import Any

from media_stack.infrastructure.servarr.runtime.common import (
    get_arr_quality_profile,
    normalize_remote_path_mappings,
    resolve_arr_quality_preferences,
)
from media_stack.services.apps.prowlarr.indexer_sync_service import ArrIndexerSyncService
from media_stack.application.servarr.arr_queue_cleanup_service import ArrQueueCleanupService
from media_stack.application.servarr.arr_service import ArrService
from media_stack.services.auth_service import AuthService
from media_stack.services.health_service import HealthService
from media_stack.services.runtime_platform import (
    bool_cfg,
    coerce_list,
    field_list,
    field_map,
    http_request,
    log,
    normalize_token,
    normalize_url,
    resolve_path,
    to_int,
)
from media_stack.services.runtime_service_registry import (
    get_runtime_binding,
    get_runtime_context_cfg,
    resolve_app_service_class,
)


def _detect_arr_api_base(app_name, app_url, api_key):
    """Detect API base with retry — delegates to arr_ops.detect_arr_api_base."""
    from .arr_ops import detect_arr_api_base
    return detect_arr_api_base(app_name, app_url, api_key)


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


def _canonicalize_technology(token: str) -> str:
    raw = str(token or "").strip().lower()
    if not raw:
        return ""
    runtime_context = get_runtime_context_cfg()
    aliases = runtime_context.get("technology_aliases") if isinstance(runtime_context, dict) else {}
    if isinstance(aliases, dict):
        alias_value = aliases.get(raw)
        alias_token = str(alias_value or "").strip().lower()
        if alias_token:
            return alias_token
    return raw


def _infer_torrent_client_technology(cfg=None) -> str:
    if isinstance(cfg, dict):
        for key in ("_technology_key", "_technology", "technology", "client_key"):
            value = _canonicalize_technology(str(cfg.get(key) or ""))
            if value:
                return value
    runtime_bound = _canonicalize_technology(get_runtime_binding("torrent_client"))
    if runtime_bound:
        return runtime_bound
    return ""


def _infer_usenet_client_technology(cfg=None) -> str:
    if isinstance(cfg, dict):
        for key in ("_technology_key", "_technology", "technology", "client_key"):
            value = _canonicalize_technology(str(cfg.get(key) or ""))
            if value:
                return value
    runtime_bound = _canonicalize_technology(get_runtime_binding("usenet_client"))
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


def _usenet_client_service(cfg=None) -> Any:
    technology = _infer_usenet_client_technology(cfg)
    if not technology:
        raise RuntimeError(
            "Unable to resolve active usenet client technology for runtime operation. "
            "Set technology_bindings.usenet_client and ensure runtime context is initialized."
        )
    service_cls = resolve_app_service_class(
        "usenet_client_service",
        object,
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


def _servarr_policy_service(cfg=None) -> Any:
    service_cls = resolve_app_service_class("servarr_policy_service", object)
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
