#!/usr/bin/env python3
"""Homepage runtime operations."""

from __future__ import annotations

from bootstrap_lib.homepage import DEFAULT_HOSTS as _lib_default_homepage_hosts
from bootstrap_lib.homepage import render_services_yaml as _lib_render_homepage_services_yaml

import bootstrap_services.runtime_core as _core

from .service import HomepageService

log = _core.log
bool_cfg = _core.bool_cfg
coerce_list = _core.coerce_list
resolve_path = _core.resolve_path
resolve_app_service_class = _core.resolve_app_service_class


def _homepage_service(_cfg=None) -> HomepageService:
    service_cls = resolve_app_service_class(
        "homepage_service", HomepageService, technology="homepage"
    )
    return service_cls(
        bool_cfg=bool_cfg,
        coerce_list=coerce_list,
        resolve_path=resolve_path,
        log=log,
        default_hosts=list(_lib_default_homepage_hosts),
        render_services_yaml=_lib_render_homepage_services_yaml,
    )


def ensure_homepage_services_config(cfg, config_root):
    return _homepage_service(cfg).ensure_services_config(cfg, config_root)


__all__ = ["ensure_homepage_services_config"]
