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



def _load_handler_specs(key: str) -> list[dict]:
    """Load handler specs from bootstrap config by key name.

    Used for both container_preflight_handlers and container_post_bootstrap_handlers.
    Each spec declares a module:function to import and call.
    """
    import json

    main_config = os.environ.get("BOOTSTRAP_CONFIG_FILE", "/bootstrap/media-stack.bootstrap.json")
    path = __import__("pathlib").Path(main_config)
    if not path.exists():
        return []
    try:
        cfg = json.loads(path.read_text(encoding="utf-8"))
        return cfg.get(key) or []
    except Exception:
        return []


def _run_handler_specs(
    specs: list[dict],
    state: object,
    args: argparse.Namespace,
    *,
    phase_label: str = "HANDLER",
) -> None:
    """Run a list of handler specs with standard context injection."""
    config_root = args.config_root
    admin_user = os.environ.get("STACK_ADMIN_USERNAME", "admin")
    admin_pass = os.environ.get("STACK_ADMIN_PASSWORD", "media-dev")

    context = {
        "config_root": config_root,
        "admin_username": admin_user,
        "admin_password": admin_pass,
        "log": runtime_platform.log,
    }

    for spec in specs:
        name = str(spec.get("name", "unknown")).strip()
        handler_path = str(spec.get("handler", "")).strip()
        extra_args = dict(spec.get("args") or {})
        export_env = bool(spec.get("export_env", False))
        optional = bool(spec.get("optional", True))

        if not handler_path:
            continue

        try:
            runtime_platform.log(f"[{phase_label}] {name}: starting")
            handler_fn = _resolve_handler(handler_path)
            call_args = {**context, **extra_args}
            result = handler_fn(**call_args)
            result_dict = dict(result) if isinstance(result, dict) else {}
            state.record_preflight(name, {"status": "ok", **result_dict})
            if export_env and result_dict:
                for key, value in result_dict.items():
                    if value and not os.environ.get(key):
                        os.environ[key] = str(value)
            runtime_platform.log(f"[{phase_label}] {name}: complete")
        except Exception as exc:
            state.record_preflight(name, {"status": "error", "error": str(exc)})
            runtime_platform.log(f"[{phase_label}] {name}: failed ({exc})")
            if not optional:
                raise


def _resolve_handler(spec: str):
    """Import a handler from 'module.path:function_name' spec."""
    if ":" in spec:
        module_path, _, attr_name = spec.partition(":")
    else:
        module_path = spec.rsplit(".", 1)[0]
        attr_name = spec.rsplit(".", 1)[1] if "." in spec else spec
    module = importlib.import_module(module_path)
    return getattr(module, attr_name)


def _run_preflights(state: object, args: argparse.Namespace) -> None:
    """Run preflight handlers declared in container_preflight_handlers config."""
    specs = _load_handler_specs("container_preflight_handlers")
    _run_handler_specs(specs, state, args, phase_label="PREFLIGHT")


def _run_post_bootstrap(state: object, args: argparse.Namespace) -> None:
    """Run post-bootstrap handlers declared in container_post_bootstrap_handlers config."""
    specs = _load_handler_specs("container_post_bootstrap_handlers")
    _run_handler_specs(specs, state, args, phase_label="POST-BOOTSTRAP")


def _build_config_policy() -> object | None:
    """Build a config policy callable from the profile YAML.

    Returns a function that mutates a config dict with routing/URL policy,
    or None if no profile is configured. Injected into the runtime factory
    as a pluggable transform between config loading and runtime building.
    """
    profile_file = os.environ.get("BOOTSTRAP_PROFILE_FILE", "")
    if not profile_file:
        return None

    path = __import__("pathlib").Path(profile_file)
    if not path.exists():
        return None

    try:
        import yaml

        with open(path) as f:
            profile = yaml.safe_load(f) or {}
    except Exception:
        return None

    routing = profile.get("routing") or {}
    route_strategy = routing.get("strategy") or os.environ.get("ROUTE_STRATEGY", "hybrid")
    base_domain = routing.get("base_domain") or "local"
    path_prefix = routing.get("app_path_prefix") or os.environ.get("APP_PATH_PREFIX", "/app")
    gateway_port = str(routing.get("gateway_port", "")) or os.environ.get("APP_GATEWAY_PORT", "")
    internet_exposed = bool(routing.get("internet_exposed"))
    stack_name = str((profile.get("metadata") or {}).get("name", "")).strip()
    stack_subdomain = routing.get("stack_subdomain") or stack_name

    # Derive gateway_host and media_server_direct_host from profile if not explicit.
    gateway_host = routing.get("gateway_host") or os.environ.get("APP_GATEWAY_HOST", "")
    if not gateway_host and route_strategy in ("hybrid", "path-prefix") and stack_subdomain:
        parts = [p for p in ["apps", stack_subdomain, base_domain] if p]
        gateway_host = ".".join(parts).lower()
    media_server_direct_host = str((routing.get("direct_hosts") or {}).get("media_server", ""))
    if not media_server_direct_host and stack_subdomain and base_domain:
        parts = [p for p in ["jellyfin", stack_subdomain, base_domain] if p]
        media_server_direct_host = ".".join(parts).lower()

    from bootstrap_services.apps.stack.bootstrap_config_policy import (
        apply_bootstrap_runtime_policy,
    )

    def policy(cfg: dict) -> None:
        apply_bootstrap_runtime_policy(
            cfg,
            selected_apps_csv="",
            preconfigure_api_keys=os.environ.get("PRECONFIGURE_API_KEYS", "1") == "1",
            auto_download_content=os.environ.get("AUTO_DOWNLOAD_CONTENT", "0") == "1",
            internet_exposed=internet_exposed,
            route_strategy=route_strategy,
            ingress_domain=base_domain,
            app_gateway_host=gateway_host,
            app_gateway_port=gateway_port,
            app_path_prefix=path_prefix,
            media_server_direct_host=media_server_direct_host,
        )
        runtime_platform.log("[OK] Config policy applied via runtime factory")

    return policy


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
        ),
        config_policy=_build_config_policy(),
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


def _apply_profile_env(profile_file: str | None) -> None:
    """Read the bootstrap profile YAML and set env vars that the runtime factory expects.

    The profile is the single source of truth. Env vars are only set if not
    already present, so explicit overrides still win.
    """
    if not profile_file:
        return
    path = __import__("pathlib").Path(profile_file)
    if not path.exists():
        return
    try:
        import yaml

        with open(path) as f:
            profile = yaml.safe_load(f) or {}
    except Exception:
        return

    bootstrap_cfg = profile.get("bootstrap") or {}
    routing_cfg = profile.get("routing") or {}
    metadata = profile.get("metadata") or {}

    env_map = {
        "FULLY_PRECONFIGURED": "1" if bootstrap_cfg.get("apply_initial_preferences") else "0",
        "PRECONFIGURE_API_KEYS": "1" if bootstrap_cfg.get("preconfigure_api_keys") else "0",
        "APPLY_INITIAL_PREFERENCES": "1" if bootstrap_cfg.get("apply_initial_preferences") else "0",
        "AUTO_DOWNLOAD_CONTENT": "1" if bootstrap_cfg.get("auto_download_content") else "0",
        "MEDIA_STACK_ENV": str(metadata.get("purpose", "prod")),
        "APP_GATEWAY_HOST": str(routing_cfg.get("gateway_host", "")),
        "APP_GATEWAY_PORT": str(routing_cfg.get("gateway_port", "")),
        "APP_PATH_PREFIX": str(routing_cfg.get("app_path_prefix", "/app")),
        "ROUTE_STRATEGY": str(routing_cfg.get("strategy", "hybrid")),
    }
    for key, value in env_map.items():
        if not os.environ.get(key):
            os.environ[key] = value


def _run_serve(args: argparse.Namespace) -> None:
    """HTTP API server mode with optional auto-run."""
    from bootstrap_api.server import start_api_server
    from bootstrap_api.state import BootstrapState

    # Validate and load profile before anything else.
    profile_file = os.environ.get("BOOTSTRAP_PROFILE_FILE")
    if profile_file:
        from bootstrap_api.preflight.profile_validation import validate_profile

        validate_profile(profile_file, log=runtime_platform.log)
    _apply_profile_env(profile_file)

    state = BootstrapState()
    port = int(args.api_port or os.environ.get("BOOTSTRAP_API_PORT", "9100"))
    run_requested = threading.Event()
    run_overrides: dict = {}

    # Supported runtime overrides — mapped to env vars the pipeline reads.
    _OVERRIDE_ENV_MAP = {
        "auto_download_content": "AUTO_DOWNLOAD_CONTENT",
        "preconfigure_api_keys": "PRECONFIGURE_API_KEYS",
        "apply_initial_preferences": "APPLY_INITIAL_PREFERENCES",
        "auto_prowlarr_indexers": "AUTO_PROWLARR_INDEXERS",
    }

    def trigger_run(overrides: dict | None = None) -> None:
        nonlocal run_overrides
        run_overrides = dict(overrides or {})
        state.run_overrides = dict(run_overrides)
        for key, env_var in _OVERRIDE_ENV_MAP.items():
            if key in run_overrides:
                os.environ[env_var] = "1" if run_overrides[key] else "0"
        run_requested.set()

    server = start_api_server(state, port=port, run_trigger=trigger_run)
    runtime_platform.log(f"[INFO] Bootstrap API server listening on :{port}")
    runtime_platform.log(f"[INFO] Dashboard: http://127.0.0.1:{port}/")

    if args.auto_run:
        run_requested.set()

    # Wait for a run trigger (POST /run or --auto-run).
    run_requested.wait()
    runtime_platform.log(f"[INFO] Bootstrap run triggered (overrides={run_overrides})")

    try:
        state.start()

        # Run preflights inside the container (HTTP + file I/O, no docker exec).
        run_preflights = os.environ.get("BOOTSTRAP_RUN_PREFLIGHTS", "1") == "1"
        if run_preflights:
            _run_preflights(state, args)

        runner, runtime_state = _build_runner(args)
        runner.run(runtime_state)

        # Post-bootstrap handlers: config-driven, same pattern as preflights.
        _run_post_bootstrap(state, args)

        state.finish()
        runtime_platform.log("[OK] Bootstrap completed successfully")
    except Exception as exc:
        state.finish(error=str(exc))
        runtime_platform.log(f"[ERR] Bootstrap failed: {exc}")
        trace = traceback.format_exc().strip()
        if trace:
            for line in trace.splitlines():
                runtime_platform.log(f"[TRACE] {line}")

    # Clean up temp config files created by config policy.
    import glob
    import shutil

    for tmp_dir in glob.glob("/tmp/bootstrap-*"):
        try:
            shutil.rmtree(tmp_dir)
        except Exception:
            pass

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
