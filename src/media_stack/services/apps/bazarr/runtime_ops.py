#!/usr/bin/env python3
"""Bazarr runtime orchestration boundary."""

from __future__ import annotations

from .adapters import apply_scalar_updates as _lib_bazarr_apply_scalar_updates

from media_stack.services.apps.servarr.runtime.common import get_arr_app
from media_stack.services.runtime_platform import (
    bool_cfg,
    coerce_list,
    log,
    normalize_url,
    parse_service_url,
    resolve_app_service_class,
    resolve_path,
    wait_for_service,
)

from .service import BazarrService


class BazarrRuntimeOps:

    @staticmethod
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

    def ensure_bazarr_arr_integration(self, cfg, config_root, arr_apps, app_keys, wait_timeout):
        return self._bazarr_service(cfg).ensure_arr_integration(
            cfg=cfg,
            config_root=config_root,
            arr_apps=arr_apps,
            app_keys=app_keys,
            wait_timeout=wait_timeout,
        )


_instance = BazarrRuntimeOps()
ensure_bazarr_arr_integration = _instance.ensure_bazarr_arr_integration
