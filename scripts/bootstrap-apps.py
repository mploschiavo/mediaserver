#!/usr/bin/env python3
"""Media Automation Stack bootstrap entrypoint.

Project steward: Matthew Loschiavo (https://matthewloschiavo.com)
Contact: mploschiavo@gmail.com | https://www.linkedin.com/in/matthewloschiavo
"""

import argparse
import os
import sys
import traceback

import bootstrap_services.runtime_core as runtime_core
import bootstrap_services.runtime_media_ops as runtime_media_ops
import bootstrap_services.runtime_servarr.service_ops as runtime_servarr_ops
from bootstrap_services.bootstrap_runner_service import (
    BootstrapRunnerDependencies,
    BootstrapRunnerService,
)
from bootstrap_services.enums import BootstrapMode
from bootstrap_services.operation_wiring import (
    RunnerOperationHandlers,
    build_runner_operation_registry,
)
from bootstrap_services.runtime_factory import (
    BootstrapCliArgs,
    BootstrapRuntimeFactoryDependencies,
    BootstrapRuntimeFactoryService,
)


def _missing_op_handler(operation_name: str):
    def _missing(*_args, **_kwargs):
        raise RuntimeError(
            f"Operation handler for '{operation_name}' is not bound. "
            "Provide adapter_hooks.operation_handlers (plugin manifest) for this operation."
        )

    return _missing


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
            "(canonical modes only)"
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
            load_bootstrap_default_json=runtime_core.load_bootstrap_default_json,
            deep_merge_objects=runtime_media_ops.deep_merge_objects,
            bool_cfg=runtime_core.bool_cfg,
            coerce_list=runtime_core.coerce_list,
            env_truthy=runtime_core.env_truthy,
            read_api_key=runtime_core.read_api_key,
            build_sab_remote_path_mappings=runtime_servarr_ops.build_sab_remote_path_mappings,
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
    runtime_state = build_result.runtime
    runtime_core.set_runtime_context_cfg(runtime_state.adapter_hooks_cfg)
    runtime_core.log(f"[INFO] Bootstrap plan: {build_result.plan.to_log_line()}")
    runner_operations = build_runner_operation_registry(
        RunnerOperationHandlers(
            ensure_app_auth_settings=runtime_servarr_ops.ensure_app_auth_settings,
            qbit_login=runtime_servarr_ops.qbit_login,
            read_sabnzbd_api_key=runtime_servarr_ops.read_sabnzbd_api_key,
            ensure_sabnzbd_defaults=runtime_servarr_ops.ensure_sabnzbd_defaults,
            ensure_sabnzbd_categories=runtime_servarr_ops.ensure_sabnzbd_categories,
            setup_qbit_categories=runtime_servarr_ops.setup_qbit_categories,
            run_servarr_pipeline=runtime_servarr_ops._servarr_pipeline_service().run,
            ensure_bazarr_arr_integration=_missing_op_handler("ensure_bazarr_arr_integration"),
            configure_jellyseerr=_missing_op_handler("configure_jellyseerr"),
            ensure_jellyfin_livetv=_missing_op_handler("ensure_jellyfin_livetv"),
            ensure_jellyfin_libraries=_missing_op_handler("ensure_jellyfin_libraries"),
            ensure_jellyfin_plugins=_missing_op_handler("ensure_jellyfin_plugins"),
            ensure_jellyfin_playback_defaults=_missing_op_handler("ensure_jellyfin_playback_defaults"),
            ensure_jellyfin_home_rails=_missing_op_handler("ensure_jellyfin_home_rails"),
            ensure_jellyfin_auto_collections_config=_missing_op_handler(
                "ensure_jellyfin_auto_collections_config"
            ),
            enforce_disk_guardrails=runtime_servarr_ops.enforce_disk_guardrails,
            run_media_hygiene=runtime_servarr_ops.run_media_hygiene,
            ensure_jellyfin_prewarm=_missing_op_handler("ensure_jellyfin_prewarm"),
            ensure_maintainerr_policy=runtime_media_ops.ensure_maintainerr_policy,
            ensure_maintainerr_integrations=runtime_media_ops.ensure_maintainerr_integrations,
            ensure_homepage_services_config=runtime_media_ops.ensure_homepage_services_config,
            ensure_prowlarr_ready=_missing_op_handler("ensure_prowlarr_ready"),
            ensure_prowlarr_flaresolverr_proxy=_missing_op_handler(
                "ensure_prowlarr_flaresolverr_proxy"
            ),
            ensure_prowlarr_indexer=_missing_op_handler("ensure_prowlarr_indexer"),
            auto_add_tested_indexers=_missing_op_handler("auto_add_tested_indexers"),
            trigger_prowlarr_sync=_missing_op_handler("trigger_prowlarr_sync"),
            sync_arr_indexers_from_prowlarr=_missing_op_handler(
                "sync_arr_indexers_from_prowlarr"
            ),
            run_prowlarr_indexer_pipeline=_missing_op_handler("run_prowlarr_indexer_pipeline"),
        ),
        operation_handler_specs=(runtime_state.adapter_hooks_cfg or {}).get("operation_handlers"),
    )

    runner = BootstrapRunnerService(
        deps=BootstrapRunnerDependencies(
            log=runtime_core.log,
            bool_cfg=runtime_core.bool_cfg,
            normalize_url=runtime_core.normalize_url,
            wait_for_service=runtime_core.wait_for_service,
            operations=runner_operations,
        )
    )
    runner.run(runtime_state)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        runtime_core.log(f"[ERR] {exc}")
        trace = traceback.format_exc().strip()
        if trace:
            for line in trace.splitlines():
                runtime_core.log(f"[TRACE] {line}")
        sys.exit(1)
