#!/usr/bin/env python3
"""Maintainerr runtime operations."""

from __future__ import annotations

from bootstrap_services.apps.maintainerr.service import MaintainerrService
from bootstrap_services.runtime_platform import (
    bool_cfg,
    coerce_list,
    deep_merge_objects,
    find_component_by_implementation,
    http_request,
    load_bootstrap_default_json,
    log,
    normalize_url,
    resolve_app_service_class,
    resolve_path,
    wait_for_service,
)
from bootstrap_services.runtime_secrets import api_keys_service, read_api_key

from .policy_service import MaintainerrPolicyService


def _read_jellyseerr_api_key(config_root, timeout_seconds=120):
    return api_keys_service().read_jellyseerr_api_key(config_root, timeout_seconds=timeout_seconds)


def _maintainerr_policy_service(_cfg=None) -> MaintainerrPolicyService:
    service_cls = resolve_app_service_class(
        "maintainerr_policy_service",
        MaintainerrPolicyService,
        technology="maintainerr",
    )
    return service_cls(
        bool_cfg=bool_cfg,
        coerce_list=coerce_list,
        resolve_path=resolve_path,
        log=log,
        load_bootstrap_default_json=load_bootstrap_default_json,
        deep_merge_objects=deep_merge_objects,
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
        get_arr_app=find_component_by_implementation,
        resolve_path=resolve_path,
    )


def ensure_maintainerr_policy(cfg, config_root):
    _maintainerr_policy_service(cfg).ensure_policy(cfg, config_root)


def ensure_maintainerr_integrations(cfg, config_root, arr_apps, wait_timeout):
    _maintainerr_service(cfg).ensure_integrations(
        cfg=cfg,
        config_root=config_root,
        arr_apps=arr_apps,
        wait_timeout=wait_timeout,
    )


__all__ = [
    "_maintainerr_service",
    "ensure_maintainerr_policy",
    "ensure_maintainerr_integrations",
]
