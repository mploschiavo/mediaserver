#!/usr/bin/env python3
"""Media Automation Stack bootstrap entrypoint.

Project steward: Matthew Loschiavo (https://matthewloschiavo.com)
Contact: mploschiavo@gmail.com | https://www.linkedin.com/in/matthewloschiavo
"""

import argparse
import os
import sys
import traceback

import bootstrap_services.entrypoint_runtime as _rt
import bootstrap_services.runtime_core as _rt_core
import bootstrap_services.runtime_media_ops as _rt_media
import bootstrap_services.runtime_servarr_ops as _rt_servarr
import bootstrap_services.runtime_servarr.service_ops as _rt_servarr_service_ops
from bootstrap_services.entrypoint_runtime import *  # noqa: F401,F403

_disk_usage_percent = _rt._disk_usage_percent
_fmt_bytes = _rt._fmt_bytes
_to_float = _rt._to_float
_servarr_pipeline_service = _rt._servarr_pipeline_service

_RUNTIME_SYNC_MODULES = [
    _rt,
    _rt_core,
    _rt_media,
    _rt_servarr,
    _rt_servarr_service_ops,
]


_RUNTIME_SYNC_SYMBOLS = [
    "_disk_usage_percent",
    "_fmt_bytes",
    "_to_float",
    "http_request",
    "wait_for_service",
    "qbit_login",
    "qbit_set_preferences",
    "qbit_list_completed_torrents",
    "qbit_delete_torrents",
    "qbit_list_torrents",
    "resolve_jellyfin_api_key",
    "load_jellyfin_livetv_state",
    "resolve_jellyfin_tuner_type_id",
    "jellyfin_request",
    "resolve_arr_quality_preferences",
    "get_arr_quality_profile",
    "log",
]


def _sync_runtime_symbols() -> None:
    for symbol in _RUNTIME_SYNC_SYMBOLS:
        if symbol not in globals():
            continue
        value = globals()[symbol]
        for module in _RUNTIME_SYNC_MODULES:
            if hasattr(module, symbol):
                setattr(module, symbol, value)


def _delegate_runtime(name):
    runtime_fn = getattr(_rt, name)

    def _wrapper(*args, **kwargs):
        _sync_runtime_symbols()
        return runtime_fn(*args, **kwargs)

    _wrapper.__name__ = name
    _wrapper.__doc__ = getattr(runtime_fn, "__doc__", None)
    return _wrapper


for _fn_name in [
    "read_api_key",
    "read_jellyseerr_api_key",
    "build_arr_import_list_payload",
    "ensure_arr_download_client",
    "ensure_arr_quality_upgrade_policy",
    "ensure_readarr_metadata_source",
    "setup_qbit_storage_defaults",
    "enforce_disk_guardrails",
    "run_qbit_duplicate_prune",
    "run_qbit_ipfilter_refresh",
    "run_qbit_queue_guardrails",
    "run_filesystem_hygiene",
    "ensure_jellyfin_livetv",
    "run_jellyfin_rail_query",
    "collection_item_ids",
    "ensure_jellyfin_collection_membership",
]:
    globals()[_fn_name] = _delegate_runtime(_fn_name)

del _fn_name


def main():
    parser = argparse.ArgumentParser(
        description="Idempotent bootstrap for Arr + Prowlarr + Jellyseerr integration."
    )
    parser.add_argument(
        "--config", default="/bootstrap/config.json", help="Bootstrap config JSON path"
    )
    parser.add_argument(
        "--config-root",
        default="/srv-config",
        help="Root path containing app config folders",
    )
    parser.add_argument(
        "--wait-timeout",
        type=int,
        default=600,
        help="Service readiness timeout (seconds)",
    )
    parser.add_argument(
        "--auto-prowlarr-indexers",
        action="store_true",
        help="Iterate indexer templates/presets and add any that pass connection test",
    )
    parser.add_argument(
        "--mode",
        default=BootstrapMode.FULL.value,
        choices=BootstrapMode.choices(),
        help=(
            "Execution mode: full bootstrap, media-server prewarm-only, "
            "media-server home-rails-only, or media-hygiene-only "
            "(legacy jellyfin-* aliases still supported)"
        ),
    )
    parser.add_argument(
        "--env",
        default=(os.environ.get("MEDIA_STACK_ENV", "prod") or "prod"),
        help=(
            "Runtime environment overlay key (used when config_overlays.enabled=true), "
            "for example: dev|stage|prod"
        ),
    )
    args = parser.parse_args()

    runtime_factory = BootstrapRuntimeFactoryService(
        deps=BootstrapRuntimeFactoryDependencies(
            load_bootstrap_default_json=load_bootstrap_default_json,
            deep_merge_objects=deep_merge_objects,
            bool_cfg=bool_cfg,
            coerce_list=coerce_list,
            env_truthy=env_truthy,
            read_api_key=read_api_key,
            build_sab_remote_path_mappings=build_sab_remote_path_mappings,
        )
    )
    build_result = runtime_factory.build_from_cli(
        BootstrapCliArgs(
            mode=BootstrapMode.from_cli(args.mode),
            config_path=args.config,
            config_root=args.config_root,
            wait_timeout=args.wait_timeout,
            auto_prowlarr_indexers=args.auto_prowlarr_indexers,
            runtime_env=str(args.env or "prod"),
        )
    )
    runtime = build_result.runtime
    set_runtime_context_cfg(runtime.cfg, runtime.adapter_hooks_cfg)
    log(f"[INFO] Bootstrap plan: {build_result.plan.to_log_line()}")
    runner_operations = build_runner_operation_registry(
        RunnerOperationHandlers(
            ensure_app_auth_settings=ensure_app_auth_settings,
            qbit_login=qbit_login,
            read_sabnzbd_api_key=read_sabnzbd_api_key,
            ensure_sabnzbd_defaults=ensure_sabnzbd_defaults,
            ensure_sabnzbd_categories=ensure_sabnzbd_categories,
            setup_qbit_categories=setup_qbit_categories,
            run_servarr_pipeline=_servarr_pipeline_service().run,
            ensure_bazarr_arr_integration=ensure_bazarr_arr_integration,
            configure_jellyseerr=configure_jellyseerr,
            ensure_jellyfin_livetv=ensure_jellyfin_livetv,
            ensure_jellyfin_libraries=ensure_jellyfin_libraries,
            ensure_jellyfin_plugins=ensure_jellyfin_plugins,
            ensure_jellyfin_playback_defaults=ensure_jellyfin_playback_defaults,
            ensure_jellyfin_home_rails=ensure_jellyfin_home_rails,
            ensure_jellyfin_auto_collections_config=ensure_jellyfin_auto_collections_config,
            enforce_disk_guardrails=enforce_disk_guardrails,
            run_media_hygiene=run_media_hygiene,
            ensure_jellyfin_prewarm=ensure_jellyfin_prewarm,
            ensure_maintainerr_policy=ensure_maintainerr_policy,
            ensure_maintainerr_integrations=ensure_maintainerr_integrations,
            ensure_homepage_services_config=ensure_homepage_services_config,
            ensure_prowlarr_ready=ensure_prowlarr_ready,
            ensure_prowlarr_flaresolverr_proxy=ensure_prowlarr_flaresolverr_proxy,
            ensure_prowlarr_indexer=ensure_prowlarr_indexer,
            auto_add_tested_indexers=auto_add_tested_indexers,
            trigger_prowlarr_sync=trigger_prowlarr_sync,
            sync_arr_indexers_from_prowlarr=sync_arr_indexers_from_prowlarr,
            run_prowlarr_indexer_pipeline=run_prowlarr_indexer_pipeline,
        ),
        operation_handler_specs=(runtime.adapter_hooks_cfg or {}).get("operation_handlers"),
    )

    runner = BootstrapRunnerService(
        deps=BootstrapRunnerDependencies(
            log=log,
            bool_cfg=bool_cfg,
            normalize_url=normalize_url,
            wait_for_service=wait_for_service,
            operations=runner_operations,
        )
    )
    runner.run(runtime)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        log(f"[ERR] {exc}")
        trace = traceback.format_exc().strip()
        if trace:
            for line in trace.splitlines():
                log(f"[TRACE] {line}")
        sys.exit(1)
