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


def _apply_config_policy(args: argparse.Namespace) -> None:
    """Apply runtime config policy to the bootstrap config JSON.

    Reads the profile YAML for routing params and mutates the config file
    with path_prefix_url_base_by_app, Homepage tile URLs, Jellyseerr
    external URLs, etc. — the same transform the host-side CLI does.
    """
    import json

    from bootstrap_services.apps.stack.bootstrap_config_policy import (
        apply_bootstrap_runtime_policy,
    )

    config_path = __import__("pathlib").Path(args.config)
    if not config_path.exists():
        return

    cfg = json.loads(config_path.read_text(encoding="utf-8"))
    if not isinstance(cfg, dict):
        return

    # Read routing params from profile YAML or env vars.
    profile_file = os.environ.get("BOOTSTRAP_PROFILE_FILE", "")
    profile = {}
    if profile_file:
        path = __import__("pathlib").Path(profile_file)
        if path.exists():
            import yaml

            with open(path) as f:
                profile = yaml.safe_load(f) or {}

    routing = profile.get("routing") or {}
    gateway_host = routing.get("gateway_host") or os.environ.get("APP_GATEWAY_HOST", "")
    gateway_port = str(routing.get("gateway_port", "")) or os.environ.get("APP_GATEWAY_PORT", "")
    path_prefix = routing.get("app_path_prefix") or os.environ.get("APP_PATH_PREFIX", "/app")
    route_strategy = routing.get("strategy") or os.environ.get("ROUTE_STRATEGY", "hybrid")
    internet_exposed = bool(routing.get("internet_exposed")) or os.environ.get("INTERNET_EXPOSED", "0") == "1"
    base_domain = routing.get("base_domain") or "local"
    media_server_direct_host = str((routing.get("direct_hosts") or {}).get("media_server", ""))

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

    # Write the transformed config to a writable temp location.
    # The original mount may be read-only, so write next to it or in /tmp.
    import tempfile

    tmp_dir = __import__("pathlib").Path(tempfile.mkdtemp(prefix="bootstrap-"))
    out_path = tmp_dir / "config.json"
    out_path.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
    # Point the args to the transformed config for _build_runner.
    args.config = str(out_path)
    runtime_platform.log(
        f"[PREFLIGHT] Config policy: wrote transformed config to {out_path}"
    )


def _run_preflights(state: object, args: argparse.Namespace) -> None:
    """Run preflight handlers inside the bootstrap runner container.

    These replace the host-side docker-exec-based preflights with HTTP API
    calls and direct file I/O over the shared config mount.
    """
    from bootstrap_api.preflight import api_keys
    from bootstrap_services.apps.jellyfin import http_preflight as jellyfin_preflight
    from bootstrap_services.apps.qbittorrent import http_preflight as qbittorrent_preflight
    from bootstrap_services.apps.sabnzbd import http_preflight as sabnzbd_preflight

    config_root = args.config_root
    admin_user = os.environ.get("STACK_ADMIN_USERNAME", "admin")
    admin_pass = os.environ.get("STACK_ADMIN_PASSWORD", "media-dev")

    # Jellyfin: startup wizard + API key provisioning.
    try:
        runtime_platform.log("[PREFLIGHT] Jellyfin: starting")
        result = jellyfin_preflight.run_preflight(
            admin_username=admin_user,
            admin_password=admin_pass,
            log=runtime_platform.log,
        )
        state.record_preflight("jellyfin", {"status": "ok", **result})
        # Export discovered keys as env vars for the bootstrap runner.
        for key, value in result.items():
            if value:
                os.environ[key] = value
        runtime_platform.log(f"[PREFLIGHT] Jellyfin: complete ({len(result)} keys)")
    except Exception as exc:
        state.record_preflight("jellyfin", {"status": "error", "error": str(exc)})
        runtime_platform.log(f"[PREFLIGHT] Jellyfin: failed ({exc})")

    # qBittorrent: credential sync.
    try:
        runtime_platform.log("[PREFLIGHT] qBittorrent: starting")
        qbittorrent_preflight.run_preflight(
            admin_username=admin_user,
            admin_password=admin_pass,
            config_root=config_root,
            log=runtime_platform.log,
        )
        state.record_preflight("qbittorrent", {"status": "ok"})
        runtime_platform.log("[PREFLIGHT] qBittorrent: complete")
    except Exception as exc:
        state.record_preflight("qbittorrent", {"status": "error", "error": str(exc)})
        runtime_platform.log(f"[PREFLIGHT] qBittorrent: failed ({exc})")

    # SABnzbd: config reconciliation.
    try:
        runtime_platform.log("[PREFLIGHT] SABnzbd: starting")
        # Build whitelist from container hostname + common aliases.
        host_whitelist = "sabnzbd,localhost"
        local_ranges = "172.16.0.0/12,192.168.0.0/16,10.0.0.0/8"
        sabnzbd_preflight.run_preflight(
            config_root=config_root,
            host_whitelist=host_whitelist,
            local_ranges=local_ranges,
            log=runtime_platform.log,
        )
        state.record_preflight("sabnzbd", {"status": "ok"})
        runtime_platform.log("[PREFLIGHT] SABnzbd: complete")
    except Exception as exc:
        state.record_preflight("sabnzbd", {"status": "error", "error": str(exc)})
        runtime_platform.log(f"[PREFLIGHT] SABnzbd: failed ({exc})")

    # API key discovery: read keys from app config files on shared mount.
    try:
        runtime_platform.log("[PREFLIGHT] API keys: discovering from config files")
        keys = api_keys.run_preflight(config_root=config_root, log=runtime_platform.log)
        state.record_preflight("api_keys", {"status": "ok", "count": len(keys)})
        for key, value in keys.items():
            if value and not os.environ.get(key):
                os.environ[key] = value
        runtime_platform.log(f"[PREFLIGHT] API keys: {len(keys)} keys discovered")
    except Exception as exc:
        state.record_preflight("api_keys", {"status": "error", "error": str(exc)})
        runtime_platform.log(f"[PREFLIGHT] API keys: failed ({exc})")

    # Runtime config policy: apply path_prefix_url_base_by_app, Homepage URLs, etc.
    # This transforms the static config.json with routing/URL policy derived from
    # the profile YAML — the same transform the host-side CLI does in
    # ComposeBootstrapService._prepare_runtime_config().
    # TODO: Register as a declarative RunnerEvent.PREFLIGHT handler in the plugin
    # manifest system instead of calling inline. Both host-side CLI and container-side
    # serve mode should resolve this through the event registry, not procedural code.
    try:
        runtime_platform.log("[PREFLIGHT] Config policy: applying runtime transforms")
        _apply_config_policy(args)
        state.record_preflight("config_policy", {"status": "ok"})
        runtime_platform.log("[PREFLIGHT] Config policy: applied")
    except Exception as exc:
        state.record_preflight("config_policy", {"status": "error", "error": str(exc)})
        runtime_platform.log(f"[PREFLIGHT] Config policy: failed ({exc})")


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

        # Run preflights inside the container (HTTP + file I/O, no docker exec).
        run_preflights = os.environ.get("BOOTSTRAP_RUN_PREFLIGHTS", "1") == "1"
        if run_preflights:
            _run_preflights(state, args)

        runner, runtime_state = _build_runner(args)
        runner.run(runtime_state)

        # Post-bootstrap: write Unpackerr config with discovered API keys and restart.
        try:
            from bootstrap_api.preflight.unpackerr import write_config_and_restart

            write_config_and_restart(
                config_root=args.config_root,
                log=runtime_platform.log,
            )
        except Exception as exc:
            runtime_platform.log(f"[WARN] Unpackerr post-bootstrap: {exc}")

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
