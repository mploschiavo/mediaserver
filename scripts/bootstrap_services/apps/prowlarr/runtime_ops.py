#!/usr/bin/env python3
"""Prowlarr runtime operations and cross-app indexer sync."""

from __future__ import annotations

from bootstrap_services.apps.servarr.runtime.arr_ops import (
    detect_arr_api_base,
    ensure_app_auth_settings,
)
from bootstrap_services.apps.servarr.runtime.factory import (
    _arr_indexer_sync_service,
    _prowlarr_service,
)
from bootstrap_services.runtime_platform import (
    bool_cfg,
    log,
    normalize_url,
    resolve_app_service_class,
    wait_for_service,
)

from .flaresolverr_service import ProwlarrFlareSolverrService
from .pipeline_service import ProwlarrIndexerPipelineService
from .precheck_service import ProwlarrPrecheckService


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


def run_prowlarr_indexer_pipeline(
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
    return _prowlarr_indexer_pipeline_service().run(
        cfg=cfg,
        prowlarr_url=prowlarr_url,
        prowlarr_key=prowlarr_key,
        wait_timeout=wait_timeout,
        prowlarr_indexers=prowlarr_indexers,
        auto_indexers=bool(auto_indexers),
        trigger_sync=bool(trigger_sync),
        arr_apps_raw=arr_apps_raw,
        app_keys=app_keys,
    )


def ensure_prowlarr_ready(cfg, prowlarr_url, prowlarr_key, app_auth_cfg, wait_timeout):
    del cfg
    return _prowlarr_precheck_service().ensure_ready(
        prowlarr_url=prowlarr_url,
        prowlarr_key=prowlarr_key,
        app_auth_cfg=app_auth_cfg,
        wait_timeout=wait_timeout,
    )


def resolve_schema_contract(prowlarr_url, prowlarr_key, implementation):
    return _prowlarr_service().resolve_schema_contract(
        prowlarr_url=prowlarr_url,
        prowlarr_key=prowlarr_key,
        implementation=implementation,
    )


def find_existing_application(prowlarr_url, prowlarr_key, implementation, base_url):
    return _prowlarr_service().find_existing_application(
        prowlarr_url=prowlarr_url,
        prowlarr_key=prowlarr_key,
        implementation=implementation,
        base_url=base_url,
    )


def ensure_prowlarr_application(
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


def trigger_prowlarr_sync(prowlarr_url, prowlarr_key):
    _prowlarr_service().trigger_sync(
        prowlarr_url=prowlarr_url,
        prowlarr_key=prowlarr_key,
    )


def ensure_prowlarr_flaresolverr_proxy(cfg, prowlarr_url, prowlarr_key, wait_timeout):
    _prowlarr_flaresolverr_service().ensure_from_config(
        cfg=cfg,
        prowlarr_url=prowlarr_url,
        prowlarr_key=prowlarr_key,
        wait_timeout=wait_timeout,
    )


def ensure_prowlarr_indexer(prowlarr_url, prowlarr_key, indexer_cfg):
    _prowlarr_service(indexer_cfg if isinstance(indexer_cfg, dict) else None).ensure_indexer(
        prowlarr_url=prowlarr_url,
        prowlarr_key=prowlarr_key,
        indexer_cfg=indexer_cfg,
    )


def build_indexer_payload(template):
    return _prowlarr_service(
        template if isinstance(template, dict) else None
    ).build_indexer_payload(template)


def auto_add_tested_indexers(
    prowlarr_url,
    prowlarr_key,
    exclude_name_tokens=None,
    reputation_cfg=None,
):
    _prowlarr_service().auto_add_tested_indexers(
        prowlarr_url=prowlarr_url,
        prowlarr_key=prowlarr_key,
        exclude_name_tokens=exclude_name_tokens,
        reputation_cfg=reputation_cfg,
    )


def sync_arr_indexers_from_prowlarr(
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
