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
from bootstrap_services.operation_wiring import build_runner_event_registry
from bootstrap_services.runtime_factory import (
    BootstrapCliArgs,
    BootstrapRuntimeFactoryDependencies,
    BootstrapRuntimeFactoryService,
)


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
    runtime_context_cfg = dict(runtime_state.adapter_hooks_cfg or {})
    runtime_context_cfg["runtime_bindings"] = {
        "torrent_client": runtime_state.torrent_client_key,
        "usenet_client": runtime_state.usenet_client_key,
        "media_server": runtime_state.media_server_backend,
        "request_manager": runtime_state.request_manager_backend,
    }
    runtime_core.set_runtime_context_cfg(runtime_context_cfg)
    runtime_core.log(f"[INFO] Bootstrap plan: {build_result.plan.to_log_line()}")
    runner_operations = build_runner_event_registry(
        base_handlers={
            "ensure_app_auth_settings": runtime_servarr_ops.ensure_app_auth_settings,
            "run_servarr_pipeline": runtime_servarr_ops._servarr_pipeline_service().run,
            "enforce_disk_guardrails": runtime_servarr_ops.enforce_disk_guardrails,
            "run_media_hygiene": runtime_servarr_ops.run_media_hygiene,
        },
        event_handler_specs=(runtime_state.adapter_hooks_cfg or {}).get("event_handlers"),
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
