#!/usr/bin/env python3
"""Media-server and UI-facing runtime operations (Jellyfin/Homepage/Maintainerr)."""

import importlib

from bootstrap_lib.homepage import DEFAULT_HOSTS as _lib_default_homepage_hosts
from bootstrap_lib.homepage import render_services_yaml as _lib_render_homepage_services_yaml

from bootstrap_services.config_artifacts_service import ConfigArtifactsService
from bootstrap_services.maintainerr_service import MaintainerrService
from bootstrap_services.runtime_platform import (
    bool_cfg,
    coerce_list,
    http_request,
    load_bootstrap_default_json,
    log,
    normalize_url,
    resolve_app_service_class,
    resolve_path,
    wait_for_service,
)
from bootstrap_services.runtime_secrets import api_keys_service, read_api_key


def _get_arr_app(arr_apps, implementation):
    target = str(implementation or "").strip()
    for app in arr_apps or []:
        if str((app or {}).get("implementation") or "").strip() == target:
            return app
    return None


def _read_jellyseerr_api_key(config_root, timeout_seconds=120):
    return api_keys_service().read_jellyseerr_api_key(config_root, timeout_seconds=timeout_seconds)


def _jellyfin_runtime_ops():
    """Lazy import to keep shared runtime modules technology-pluggable at import time."""
    return importlib.import_module("bootstrap_services.apps.jellyfin.runtime_ops")


def _config_artifacts_service(cfg=None) -> ConfigArtifactsService:
    jellyfin_runtime_ops = _jellyfin_runtime_ops()
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
        read_jellyseerr_api_key=_read_jellyseerr_api_key,
        get_arr_app=_get_arr_app,
        resolve_path=resolve_path,
    )


def yaml_scalar(value):
    return _config_artifacts_service().yaml_scalar(value)


def render_yaml(value, indent=0):
    return _config_artifacts_service().render_yaml(value, indent=indent)


def ensure_homepage_services_config(cfg, config_root):
    return _config_artifacts_service(cfg).ensure_homepage_services_config(cfg, config_root)


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
