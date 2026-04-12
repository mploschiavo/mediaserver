#!/usr/bin/env python3
"""Homepage runtime operations."""

from __future__ import annotations

from .adapters import DEFAULT_HOSTS as _lib_default_homepage_hosts
from .adapters import render_services_yaml as _lib_render_homepage_services_yaml

from media_stack.services.runtime_platform import (
    bool_cfg,
    coerce_list,
    http_request,
    log,
    normalize_url,
    resolve_app_service_class,
    resolve_path,
)

from .service import HomepageService


class HomepageRuntimeOps:

    @staticmethod
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

    def ensure_homepage_services_config(self, cfg, config_root):
        changed = self._homepage_service(cfg).ensure_services_config(cfg, config_root)
        if not changed:
            return False

        homepage_cfg = cfg.get("homepage") or {}
        homepage_url = normalize_url(str(homepage_cfg.get("url") or "http://homepage:3000"))
        try:
            status, _, body = http_request(homepage_url, "/api/revalidate", timeout=15)
        except Exception as exc:
            log(
                "[WARN] Homepage: services config changed but failed to trigger runtime revalidate "
                f"({exc})."
            )
            return True

        if 200 <= int(status) < 300:
            log("[OK] Homepage: runtime cache revalidated after services config update.")
            return True

        log(
            "[WARN] Homepage: services config changed but runtime revalidate returned "
            f"HTTP {status}: {body}"
        )
        return True


_instance = HomepageRuntimeOps()
ensure_homepage_services_config = _instance.ensure_homepage_services_config


__all__ = ["ensure_homepage_services_config"]
_homepage_service = _instance._homepage_service
