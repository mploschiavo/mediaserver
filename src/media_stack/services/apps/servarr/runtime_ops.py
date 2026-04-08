"""Servarr runtime pipeline and discovery operation handlers."""

from __future__ import annotations

from media_stack.services.apps.prowlarr.runtime_ops import ensure_prowlarr_application
from media_stack.services.apps.servarr.pipeline_service import ServarrPipelineService
from media_stack.services.apps.servarr.runtime.arr_ops import (
    detect_arr_api_base,
    ensure_app_auth_settings,
    ensure_arr_download_client,
    ensure_arr_download_handling,
    ensure_arr_media_management,
    ensure_arr_quality_upgrade_policy,
    ensure_arr_remote_path_mappings,
    ensure_readarr_metadata_source,
    ensure_root_folder,
    pick_first_profile_id,
    trigger_health_check,
)
from media_stack.services.apps.servarr.runtime.common import (
    get_arr_quality_profile,
    resolve_arr_quality_preferences,
)
from media_stack.services.apps.servarr.runtime.factory import _health_service
from media_stack.services.discovery_lists_service import DiscoveryListsService
from media_stack.services.runtime_platform import (
    bool_cfg,
    coerce_list,
    env_truthy,
    field_list,
    field_map,
    http_request,
    log,
    normalize_token,
    normalize_url,
    resolve_app_service_class,
    resolve_env_placeholder,
    to_int,
)
from media_stack.services.apps.servarr.servarr_adapters import AdapterDependencies


def _discovery_service() -> DiscoveryListsService:
    service_cls = resolve_app_service_class("discovery_lists_service", DiscoveryListsService)
    return service_cls(
        bool_cfg=bool_cfg,
        coerce_list=coerce_list,
        log=log,
        http_request=http_request,
        resolve_env_placeholder=resolve_env_placeholder,
        field_map=field_map,
        field_list=field_list,
        to_int=to_int,
        normalize_token=normalize_token,
        resolve_arr_quality_preferences=resolve_arr_quality_preferences,
        get_arr_quality_profile=get_arr_quality_profile,
        pick_first_profile_id=pick_first_profile_id,
        env_truthy=env_truthy,
        trigger_arr_command=_health_service().trigger_arr_command,
    )


def _trigger_arr_discovery_kickoff(cfg, app_cfg, app_url, api_base, api_key):
    return _discovery_service().trigger_arr_discovery_kickoff(
        cfg,
        app_cfg,
        app_url,
        api_base,
        api_key,
    )


def _ensure_arr_discovery_lists_for_app(cfg, app_cfg, app_url, api_base, api_key):
    return _discovery_service().ensure_arr_discovery_lists_for_app(
        cfg,
        app_cfg,
        app_url,
        api_base,
        api_key,
    )


def _servarr_pipeline_service() -> ServarrPipelineService:
    adapter_deps = AdapterDependencies(
        bool_cfg=bool_cfg,
        log=log,
        ensure_readarr_metadata_source=ensure_readarr_metadata_source,
    )
    service_cls = resolve_app_service_class("servarr_pipeline_service", ServarrPipelineService)
    return service_cls(
        log=log,
        normalize_url=normalize_url,
        detect_arr_api_base=detect_arr_api_base,
        ensure_app_auth_settings=ensure_app_auth_settings,
        ensure_arr_media_management=ensure_arr_media_management,
        ensure_root_folder=ensure_root_folder,
        ensure_arr_download_handling=ensure_arr_download_handling,
        ensure_arr_quality_upgrade_policy=ensure_arr_quality_upgrade_policy,
        ensure_prowlarr_application=ensure_prowlarr_application,
        ensure_arr_download_client=ensure_arr_download_client,
        ensure_arr_remote_path_mappings=ensure_arr_remote_path_mappings,
        ensure_arr_discovery_lists_for_app=_ensure_arr_discovery_lists_for_app,
        trigger_arr_discovery_kickoff=_trigger_arr_discovery_kickoff,
        trigger_health_check=trigger_health_check,
        adapter_deps=adapter_deps,
    )


def run_servarr_pipeline(inputs):
    return _servarr_pipeline_service().run(inputs)


def coerce_for_example(value, example):
    return DiscoveryListsService._coerce_for_example(value, example)


def resolve_import_list_definitions(arr_discovery_cfg, app_cfg):
    return _discovery_service().resolve_import_list_definitions(arr_discovery_cfg, app_cfg)


def build_arr_import_list_payload(
    app_cfg,
    schema,
    list_cfg,
    default_quality_profile_id,
    default_metadata_profile_id=None,
):
    return _discovery_service().build_arr_import_list_payload(
        app_cfg,
        schema,
        list_cfg,
        default_quality_profile_id,
        default_metadata_profile_id,
    )


def ensure_arr_discovery_lists_for_app(cfg, app_cfg, app_url, api_base, api_key):
    return _ensure_arr_discovery_lists_for_app(cfg, app_cfg, app_url, api_base, api_key)


def trigger_arr_discovery_kickoff(cfg, app_cfg, app_url, api_base, api_key):
    return _trigger_arr_discovery_kickoff(cfg, app_cfg, app_url, api_base, api_key)
