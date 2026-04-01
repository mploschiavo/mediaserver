#!/usr/bin/env python3
"""Bazarr runtime orchestration boundary."""

from __future__ import annotations

import bootstrap_services.runtime_core as _core

from .service import BazarrService

log = _core.log
bool_cfg = _core.bool_cfg
coerce_list = _core.coerce_list
normalize_url = _core.normalize_url
wait_for_service = _core.wait_for_service
get_arr_app = _core.get_arr_app
parse_service_url = _core.parse_service_url
resolve_path = _core.resolve_path
resolve_app_service_class = _core.resolve_app_service_class

_lib_bazarr_apply_scalar_updates = _core._lib_bazarr_apply_scalar_updates


def _bazarr_service(cfg=None) -> BazarrService:
    service_cls = resolve_app_service_class("bazarr_service", BazarrService, technology="bazarr")
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


def ensure_bazarr_arr_integration(cfg, config_root, arr_apps, app_keys, wait_timeout):
    return _bazarr_service(cfg).ensure_arr_integration(
        cfg=cfg,
        config_root=config_root,
        arr_apps=arr_apps,
        app_keys=app_keys,
        wait_timeout=wait_timeout,
    )
