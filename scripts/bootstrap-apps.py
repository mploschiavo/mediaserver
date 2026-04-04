#!/usr/bin/env python3
"""Media Automation Stack bootstrap entrypoint.

Project steward: Matthew Loschiavo (https://matthewloschiavo.com)
Contact: mploschiavo@gmail.com | https://www.linkedin.com/in/matthewloschiavo
"""

import argparse
import importlib
import os
import sys
import threading
import traceback

import bootstrap_services.runtime_platform as runtime_platform
import bootstrap_services.runtime_secrets as runtime_secrets
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


def _build_runner(args: argparse.Namespace) -> tuple:
    """Build the bootstrap runner and runtime state from CLI args."""
    servarr_runtime_arr_ops = importlib.import_module(
        "bootstrap_services.apps.servarr.runtime.arr_ops"
    )
    build_sab_remote_path_mappings = getattr(
        servarr_runtime_arr_ops,
        "build_sab_remote_path_mappings",
    )

    runtime_factory = BootstrapRuntimeFactoryService(
        deps=BootstrapRuntimeFactoryDependencies(
            load_bootstrap_default_json=runtime_platform.load_bootstrap_default_json,
            deep_merge_objects=runtime_platform.deep_merge_objects,
            bool_cfg=runtime_platform.bool_cfg,
            coerce_list=runtime_platform.coerce_list,
            env_truthy=runtime_platform.env_truthy,
            read_api_key=runtime_secrets.read_api_key,
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
    runtime_state = build_result.runtime
    runtime_platform.log(f"[INFO] Bootstrap plan: {build_result.plan.to_log_line()}")
    runner_operations = build_runner_event_registry(
        event_handler_specs=(runtime_state.adapter_hooks_cfg or {}).get("event_handlers"),
    )

    runner = BootstrapRunnerService(
        deps=BootstrapRunnerDependencies(
            log=runtime_platform.log,
            bool_cfg=runtime_platform.bool_cfg,
            normalize_url=runtime_platform.normalize_url,
            wait_for_service=runtime_platform.wait_for_service,
            operations=runner_operations,
        )
    )
    return runner, runtime_state


def _run_oneshot(args: argparse.Namespace) -> None:
    """Original one-shot bootstrap mode."""
    runner, runtime_state = _build_runner(args)
    runner.run(runtime_state)


def _run_serve(args: argparse.Namespace) -> None:
    """HTTP API server mode with optional auto-run."""
    from bootstrap_api.server import start_api_server
    from bootstrap_api.state import BootstrapState

    state = BootstrapState()
    port = int(args.api_port or os.environ.get("BOOTSTRAP_API_PORT", "9100"))
    run_requested = threading.Event()

    def trigger_run() -> None:
        run_requested.set()

    server = start_api_server(state, port=port, run_trigger=trigger_run)
    runtime_platform.log(f"[INFO] Bootstrap API server listening on :{port}")

    if args.auto_run:
        run_requested.set()

    # Wait for a run trigger (POST /run or --auto-run).
    run_requested.wait()
    runtime_platform.log("[INFO] Bootstrap run triggered")

    try:
        state.start()
        runner, runtime_state = _build_runner(args)
        runner.run(runtime_state)
        state.finish()
        runtime_platform.log("[OK] Bootstrap completed successfully")
    except Exception as exc:
        state.finish(error=str(exc))
        runtime_platform.log(f"[ERR] Bootstrap failed: {exc}")
        trace = traceback.format_exc().strip()
        if trace:
            for line in trace.splitlines():
                runtime_platform.log(f"[TRACE] {line}")

    # Keep serving health/status endpoints until container is stopped.
    shutdown_delay = int(os.environ.get("BOOTSTRAP_SHUTDOWN_DELAY_SECONDS", "0"))
    if shutdown_delay > 0:
        import time

        runtime_platform.log(
            f"[INFO] Bootstrap API server staying alive for {shutdown_delay}s"
        )
        time.sleep(shutdown_delay)
        server.shutdown()
    else:
        runtime_platform.log("[INFO] Bootstrap API server staying alive (send SIGTERM to stop)")
        try:
            threading.Event().wait()  # Block forever until SIGTERM.
        except KeyboardInterrupt:
            pass
        finally:
            server.shutdown()


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
    # API server mode flags.
    parser.add_argument(
        "--serve",
        action="store_true",
        help="Start HTTP API server for telemetry and control instead of one-shot run",
    )
    parser.add_argument(
        "--auto-run",
        action="store_true",
        help="When --serve, automatically begin bootstrap on startup",
    )
    parser.add_argument(
        "--api-port",
        type=int,
        default=int(os.environ.get("BOOTSTRAP_API_PORT", "9100")),
        help="HTTP API listen port (default: 9100)",
    )
    args = parser.parse_args()

    if args.serve:
        _run_serve(args)
    else:
        _run_oneshot(args)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        runtime_platform.log(f"[ERR] {exc}")
        trace = traceback.format_exc().strip()
        if trace:
            for line in trace.splitlines():
                runtime_platform.log(f"[TRACE] {line}")
        sys.exit(1)
