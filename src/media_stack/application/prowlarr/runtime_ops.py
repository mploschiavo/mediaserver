#!/usr/bin/env python3
"""Prowlarr runtime operations and cross-app indexer sync."""

from __future__ import annotations

from typing import Any

from media_stack.application.prowlarr.pipeline_service import (
    ProwlarrIndexerPipelineService,
)
from media_stack.application.prowlarr.precheck_service import ProwlarrPrecheckService
from media_stack.infrastructure.prowlarr.flaresolverr_service import (
    ProwlarrFlareSolverrService,
)
from media_stack.services.apps.servarr.runtime.arr_ops import (
    detect_arr_api_base,
    ensure_app_auth_settings,
)
from media_stack.services.apps.servarr.runtime.factory import (
    _arr_indexer_sync_service,
    _prowlarr_service,
)
from media_stack.services.runtime_platform import (
    bool_cfg,
    log,
    normalize_url,
    resolve_app_service_class,
    wait_for_service,
)


class ProwlarrRuntimeOps:

    @staticmethod
    def _normalize_url_base(value: object) -> str:
        token = str(value or "").strip()
        if not token:
            return ""
        if not token.startswith("/"):
            token = f"/{token}"
        if token != "/":
            token = token.rstrip("/")
        return token

    @staticmethod
    def _join_url_base(base_url: str, url_base: str) -> str:
        root = str(base_url or "").rstrip("/")
        base = _normalize_url_base(url_base)
        if not base:
            return root
        return f"{root}{base}"

    @staticmethod
    def _lookup_url_base(mapping: dict[str, Any], keys: tuple[str, ...]) -> str:
        if not isinstance(mapping, dict):
            return ""
        lowered = {
            str(raw_key or "").strip().lower(): raw_value for raw_key, raw_value in mapping.items()
        }
        for key in keys:
            token = str(key or "").strip().lower()
            if not token:
                continue
            candidate = lowered.get(token)
            if candidate is None:
                continue
            value = _normalize_url_base(candidate)
            if value:
                return value
        return ""

    @staticmethod
    def _path_aware_prowlarr_url(cfg: dict[str, Any] | None, prowlarr_url: str) -> str:
        app_auth = cfg.get("app_auth") if isinstance(cfg, dict) else None
        if not isinstance(app_auth, dict):
            return str(prowlarr_url or "").strip()
        path_base = _lookup_url_base(
            app_auth.get("path_prefix_url_base_by_app") or {},
            ("prowlarr",),
        ) or _lookup_url_base(
            app_auth.get("url_base_by_app") or {},
            ("prowlarr",),
        )
        if not path_base:
            return str(prowlarr_url or "").strip()
        return _join_url_base(str(prowlarr_url or "").strip(), path_base)

    @staticmethod
    def _prowlarr_precheck_service(cfg=None) -> ProwlarrPrecheckService:
        service_cls = resolve_app_service_class(
            "prowlarr_precheck_service", ProwlarrPrecheckService, technology="prowlarr"
        )
        return service_cls(
            log=log,
            bool_cfg=bool_cfg,
            wait_for_service=wait_for_service,
            detect_arr_api_base=detect_arr_api_base,
            ensure_app_auth_settings=ensure_app_auth_settings,
        )

    @staticmethod
    def _prowlarr_flaresolverr_service(cfg=None) -> ProwlarrFlareSolverrService:
        service_cls = resolve_app_service_class(
            "prowlarr_flaresolverr_service",
            ProwlarrFlareSolverrService,
            technology="prowlarr",
        )
        return service_cls(
            bool_cfg=bool_cfg,
            normalize_url=normalize_url,
            wait_for_service=wait_for_service,
            ensure_proxy=lambda prowlarr_url, prowlarr_key, flaresolverr_cfg: _prowlarr_service(
                flaresolverr_cfg if isinstance(flaresolverr_cfg, dict) else None
            ).ensure_flaresolverr_proxy(
                prowlarr_url=prowlarr_url,
                prowlarr_key=prowlarr_key,
                flaresolverr_cfg=flaresolverr_cfg,
            ),
        )

    @staticmethod
    def _prowlarr_indexer_pipeline_service(cfg=None) -> ProwlarrIndexerPipelineService:
        service_cls = resolve_app_service_class(
            "prowlarr_indexer_pipeline_service",
            ProwlarrIndexerPipelineService,
            technology="prowlarr",
        )
        return service_cls(
            log=log,
            bool_cfg=bool_cfg,
            ensure_flaresolverr_proxy=ensure_prowlarr_flaresolverr_proxy,
            ensure_indexer=ensure_prowlarr_indexer,
            auto_add_tested_indexers=auto_add_tested_indexers,
            trigger_sync=trigger_prowlarr_sync,
            sync_arr_indexers_from_prowlarr=sync_arr_indexers_from_prowlarr,
        )

    def run_prowlarr_indexer_pipeline(self,
        cfg,
        prowlarr_url,
        prowlarr_key,
        wait_timeout,
        prowlarr_indexers,
        auto_indexers,
        trigger_sync,
        arr_apps_raw,
        app_keys,
    ):
        # Use path-aware URL if Prowlarr has urlBase active; fall back to direct URL
        # if the path-aware URL doesn't respond (fresh install, not yet restarted).
        direct_url = str(prowlarr_url or "").strip()
        path_aware_url = _path_aware_prowlarr_url(cfg, direct_url)
        effective_url = path_aware_url
        if path_aware_url and path_aware_url != direct_url:
            try:
                from media_stack.services.runtime_platform import http_request as _hr

                status, parsed, _ = _hr(path_aware_url, "/ping")
                if status != 200:
                    effective_url = direct_url
            except Exception:
                effective_url = direct_url

        return _prowlarr_indexer_pipeline_service().run(
            cfg=cfg,
            prowlarr_url=effective_url,
            prowlarr_key=prowlarr_key,
            wait_timeout=wait_timeout,
            prowlarr_indexers=prowlarr_indexers,
            auto_indexers=bool(auto_indexers),
            trigger_sync=bool(trigger_sync),
            arr_apps_raw=arr_apps_raw,
            app_keys=app_keys,
        )

    def ensure_prowlarr_ready(self, cfg, prowlarr_url, prowlarr_key, app_auth_cfg, wait_timeout):
        direct_url = str(prowlarr_url or "").strip()
        # Ping/readiness probe uses the direct service URL; path-prefix routes are for browsers.
        # API mutation calls (auth settings) use the path-aware URL so that Prowlarr instances
        # with urlBase already set don't return HTTP 307 on bootstrap retries.
        path_aware_url = _path_aware_prowlarr_url(cfg, direct_url)
        return _prowlarr_precheck_service().ensure_ready(
            prowlarr_url=direct_url,
            prowlarr_key=prowlarr_key,
            app_auth_cfg=app_auth_cfg,
            wait_timeout=wait_timeout,
            api_url=path_aware_url,
        )

    def resolve_schema_contract(self, prowlarr_url, prowlarr_key, implementation):
        return _prowlarr_service().resolve_schema_contract(
            prowlarr_url=prowlarr_url,
            prowlarr_key=prowlarr_key,
            implementation=implementation,
        )

    def find_existing_application(self, prowlarr_url, prowlarr_key, implementation, base_url):
        return _prowlarr_service().find_existing_application(
            prowlarr_url=prowlarr_url,
            prowlarr_key=prowlarr_key,
            implementation=implementation,
            base_url=base_url,
        )

    def ensure_prowlarr_application(self,
        prowlarr_url,
        prowlarr_key,
        app_name,
        implementation,
        app_url,
        app_key,
    ):
        _prowlarr_service().ensure_application(
            prowlarr_url=prowlarr_url,
            prowlarr_key=prowlarr_key,
            app_name=app_name,
            implementation=implementation,
            app_url=app_url,
            app_key=app_key,
        )

    def trigger_prowlarr_sync(self, prowlarr_url, prowlarr_key):
        _prowlarr_service().trigger_sync(
            prowlarr_url=prowlarr_url,
            prowlarr_key=prowlarr_key,
        )

    def ensure_prowlarr_flaresolverr_proxy(self, cfg, prowlarr_url, prowlarr_key, wait_timeout):
        _prowlarr_flaresolverr_service().ensure_from_config(
            cfg=cfg,
            prowlarr_url=prowlarr_url,
            prowlarr_key=prowlarr_key,
            wait_timeout=wait_timeout,
        )

    def ensure_prowlarr_indexer(self, prowlarr_url, prowlarr_key, indexer_cfg):
        _prowlarr_service(indexer_cfg if isinstance(indexer_cfg, dict) else None).ensure_indexer(
            prowlarr_url=prowlarr_url,
            prowlarr_key=prowlarr_key,
            indexer_cfg=indexer_cfg,
        )

    def build_indexer_payload(self, template):
        return _prowlarr_service(
            template if isinstance(template, dict) else None
        ).build_indexer_payload(template)

    def auto_add_tested_indexers(self,
        prowlarr_url,
        prowlarr_key,
        exclude_name_tokens=None,
        reputation_cfg=None,
        flaresolverr_proxy_id=None,
    ):
        _prowlarr_service().auto_add_tested_indexers(
            prowlarr_url=prowlarr_url,
            prowlarr_key=prowlarr_key,
            exclude_name_tokens=exclude_name_tokens,
            reputation_cfg=reputation_cfg,
            flaresolverr_proxy_id=flaresolverr_proxy_id,
        )

    def sync_arr_indexers_from_prowlarr(self,
        prowlarr_url,
        prowlarr_key,
        arr_apps,
        app_keys,
        prune_stale=True,
    ):
        return _arr_indexer_sync_service().reconcile(
            prowlarr_url=prowlarr_url,
            prowlarr_key=prowlarr_key,
            arr_apps=arr_apps,
            app_keys=app_keys,
            prune_stale=bool(prune_stale),
        )


_instance = ProwlarrRuntimeOps()
run_prowlarr_indexer_pipeline = _instance.run_prowlarr_indexer_pipeline
ensure_prowlarr_ready = _instance.ensure_prowlarr_ready
resolve_schema_contract = _instance.resolve_schema_contract
find_existing_application = _instance.find_existing_application
ensure_prowlarr_application = _instance.ensure_prowlarr_application
trigger_prowlarr_sync = _instance.trigger_prowlarr_sync
ensure_prowlarr_flaresolverr_proxy = _instance.ensure_prowlarr_flaresolverr_proxy
ensure_prowlarr_indexer = _instance.ensure_prowlarr_indexer
build_indexer_payload = _instance.build_indexer_payload
auto_add_tested_indexers = _instance.auto_add_tested_indexers
sync_arr_indexers_from_prowlarr = _instance.sync_arr_indexers_from_prowlarr
_join_url_base = _instance._join_url_base
_lookup_url_base = _instance._lookup_url_base
_normalize_url_base = _instance._normalize_url_base
_path_aware_prowlarr_url = _instance._path_aware_prowlarr_url
_prowlarr_flaresolverr_service = _instance._prowlarr_flaresolverr_service
_prowlarr_indexer_pipeline_service = _instance._prowlarr_indexer_pipeline_service
_prowlarr_precheck_service = _instance._prowlarr_precheck_service
