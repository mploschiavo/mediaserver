#!/usr/bin/env python3
"""Media-server and UI-facing runtime operations (Jellyfin/Bazarr/Homepage)."""

import bootstrap_services.apps.jellyfin.runtime_ops as jellyfin_runtime_ops
import bootstrap_services.runtime_core as _core

log = _core.log
bool_cfg = _core.bool_cfg
coerce_list = _core.coerce_list
normalize_url = _core.normalize_url
wait_for_service = _core.wait_for_service
get_arr_app = _core.get_arr_app
parse_service_url = _core.parse_service_url
resolve_path = _core.resolve_path
read_api_key = _core.read_api_key
load_bootstrap_default_json = _core.load_bootstrap_default_json
read_jellyseerr_api_key = _core.read_jellyseerr_api_key
http_request = _core.http_request
resolve_app_service_class = _core.resolve_app_service_class
BazarrService = _core.BazarrService
ConfigArtifactsService = _core.ConfigArtifactsService
MaintainerrService = _core.MaintainerrService

_lib_bazarr_apply_scalar_updates = _core._lib_bazarr_apply_scalar_updates
_lib_default_homepage_hosts = _core._lib_default_homepage_hosts
_lib_render_homepage_services_yaml = _core._lib_render_homepage_services_yaml


def _bazarr_service(cfg=None) -> BazarrService:
    service_cls = resolve_app_service_class("bazarr_service", BazarrService)
    return service_cls(
        log=log,
        bool_cfg=bool_cfg,
        normalize_url=normalize_url,
        wait_for_service=wait_for_service,
        get_arr_app=get_arr_app,
        parse_service_url=parse_service_url,
        coerce_list=coerce_list,
        resolve_path=resolve_path,
        apply_scalar_updates=_lib_bazarr_apply_scalar_updates,
    )


def _config_artifacts_service(cfg=None) -> ConfigArtifactsService:
    service_cls = resolve_app_service_class("config_artifacts_service", ConfigArtifactsService)
    return service_cls(
        bool_cfg=bool_cfg,
        coerce_list=coerce_list,
        resolve_path=resolve_path,
        normalize_url=normalize_url,
        wait_for_service=wait_for_service,
        resolve_jellyfin_api_key=jellyfin_runtime_ops.resolve_jellyfin_api_key,
        jellyfin_request=jellyfin_runtime_ops.jellyfin_request,
        log=log,
        load_bootstrap_default_json=load_bootstrap_default_json,
        default_homepage_hosts=list(_lib_default_homepage_hosts),
        render_homepage_services_yaml=_lib_render_homepage_services_yaml,
    )


def _maintainerr_service(cfg=None) -> MaintainerrService:
    service_cls = resolve_app_service_class("maintainerr_service", MaintainerrService)
    return service_cls(
        log=log,
        bool_cfg=bool_cfg,
        normalize_url=normalize_url,
        wait_for_service=wait_for_service,
        http_request=http_request,
        read_api_key=read_api_key,
        read_jellyseerr_api_key=read_jellyseerr_api_key,
        get_arr_app=get_arr_app,
        resolve_path=resolve_path,
    )


def yaml_scalar(value):
    return _config_artifacts_service().yaml_scalar(value)


def render_yaml(value, indent=0):
    return _config_artifacts_service().render_yaml(value, indent=indent)


def ensure_homepage_services_config(cfg, config_root):
    return _config_artifacts_service(cfg).ensure_homepage_services_config(cfg, config_root)


def ensure_bazarr_arr_integration(cfg, config_root, arr_apps, app_keys, wait_timeout):
    return _bazarr_service(cfg).ensure_arr_integration(
        cfg=cfg,
        config_root=config_root,
        arr_apps=arr_apps,
        app_keys=app_keys,
        wait_timeout=wait_timeout,
    )


def default_auto_collections_plugins():
    return _config_artifacts_service().default_auto_collections_plugins()


def deep_merge_objects(base_obj, override_obj):
    return _config_artifacts_service().deep_merge_objects(base_obj, override_obj)


def ensure_maintainerr_policy(cfg, config_root):
    _config_artifacts_service(cfg).ensure_maintainerr_policy(cfg, config_root)


def ensure_maintainerr_integrations(cfg, config_root, arr_apps, wait_timeout):
    _maintainerr_service(cfg).ensure_integrations(
        cfg=cfg,
        config_root=config_root,
        arr_apps=arr_apps,
        wait_timeout=wait_timeout,
    )
