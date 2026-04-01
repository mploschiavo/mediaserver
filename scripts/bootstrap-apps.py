#!/usr/bin/env python3
"""Media Automation Stack bootstrap entrypoint.

Project steward: Matthew Loschiavo (https://matthewloschiavo.com)
Contact: mploschiavo@gmail.com | https://www.linkedin.com/in/matthewloschiavo
"""

import argparse
import os
import sys
import traceback

import bootstrap_services.entrypoint_runtime as runtime


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
        default=runtime.BootstrapMode.FULL.value,
        choices=runtime.BootstrapMode.choices(),
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

    runtime_factory = runtime.BootstrapRuntimeFactoryService(
        deps=runtime.BootstrapRuntimeFactoryDependencies(
            load_bootstrap_default_json=runtime.load_bootstrap_default_json,
            deep_merge_objects=runtime.deep_merge_objects,
            bool_cfg=runtime.bool_cfg,
            coerce_list=runtime.coerce_list,
            env_truthy=runtime.env_truthy,
            read_api_key=runtime.read_api_key,
            build_sab_remote_path_mappings=runtime.build_sab_remote_path_mappings,
        )
    )
    build_result = runtime_factory.build_from_cli(
        runtime.BootstrapCliArgs(
            mode=runtime.BootstrapMode.from_cli(args.mode),
            config_path=args.config,
            config_root=args.config_root,
            wait_timeout=args.wait_timeout,
            auto_prowlarr_indexers=args.auto_prowlarr_indexers,
            runtime_env=str(args.env or "prod"),
        )
    )
    runtime_state = build_result.runtime
    runtime.set_runtime_context_cfg(runtime_state.cfg, runtime_state.adapter_hooks_cfg)
    runtime.log(f"[INFO] Bootstrap plan: {build_result.plan.to_log_line()}")
    runner_operations = runtime.build_runner_operation_registry(
        runtime.RunnerOperationHandlers(
            ensure_app_auth_settings=runtime.ensure_app_auth_settings,
            qbit_login=runtime.qbit_login,
            read_sabnzbd_api_key=runtime.read_sabnzbd_api_key,
            ensure_sabnzbd_defaults=runtime.ensure_sabnzbd_defaults,
            ensure_sabnzbd_categories=runtime.ensure_sabnzbd_categories,
            setup_qbit_categories=runtime.setup_qbit_categories,
            run_servarr_pipeline=runtime._servarr_pipeline_service().run,
            ensure_bazarr_arr_integration=runtime.ensure_bazarr_arr_integration,
            configure_jellyseerr=runtime.configure_jellyseerr,
            ensure_jellyfin_livetv=runtime.ensure_jellyfin_livetv,
            ensure_jellyfin_libraries=runtime.ensure_jellyfin_libraries,
            ensure_jellyfin_plugins=runtime.ensure_jellyfin_plugins,
            ensure_jellyfin_playback_defaults=runtime.ensure_jellyfin_playback_defaults,
            ensure_jellyfin_home_rails=runtime.ensure_jellyfin_home_rails,
            ensure_jellyfin_auto_collections_config=runtime.ensure_jellyfin_auto_collections_config,
            enforce_disk_guardrails=runtime.enforce_disk_guardrails,
            run_media_hygiene=runtime.run_media_hygiene,
            ensure_jellyfin_prewarm=runtime.ensure_jellyfin_prewarm,
            ensure_maintainerr_policy=runtime.ensure_maintainerr_policy,
            ensure_maintainerr_integrations=runtime.ensure_maintainerr_integrations,
            ensure_homepage_services_config=runtime.ensure_homepage_services_config,
            ensure_prowlarr_ready=runtime.ensure_prowlarr_ready,
            ensure_prowlarr_flaresolverr_proxy=runtime.ensure_prowlarr_flaresolverr_proxy,
            ensure_prowlarr_indexer=runtime.ensure_prowlarr_indexer,
            auto_add_tested_indexers=runtime.auto_add_tested_indexers,
            trigger_prowlarr_sync=runtime.trigger_prowlarr_sync,
            sync_arr_indexers_from_prowlarr=runtime.sync_arr_indexers_from_prowlarr,
            run_prowlarr_indexer_pipeline=runtime.run_prowlarr_indexer_pipeline,
        ),
        operation_handler_specs=(runtime_state.adapter_hooks_cfg or {}).get("operation_handlers"),
    )

    runner = runtime.BootstrapRunnerService(
        deps=runtime.BootstrapRunnerDependencies(
            log=runtime.log,
            bool_cfg=runtime.bool_cfg,
            normalize_url=runtime.normalize_url,
            wait_for_service=runtime.wait_for_service,
            operations=runner_operations,
        )
    )
    runner.run(runtime_state)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        runtime.log(f"[ERR] {exc}")
        trace = traceback.format_exc().strip()
        if trace:
            for line in trace.splitlines():
                runtime.log(f"[TRACE] {line}")
        sys.exit(1)
