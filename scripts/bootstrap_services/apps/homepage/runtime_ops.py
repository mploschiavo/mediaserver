#!/usr/bin/env python3
"""Homepage runtime operations."""

from __future__ import annotations

from bootstrap_lib.homepage import DEFAULT_HOSTS as _lib_default_homepage_hosts
from bootstrap_lib.homepage import render_services_yaml as _lib_render_homepage_services_yaml

from bootstrap_services.runtime_platform import (
    bool_cfg,
    coerce_list,
    log,
    resolve_app_service_class,
    resolve_path,
)

from .service import HomepageService


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
