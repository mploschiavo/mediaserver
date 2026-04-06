#!/usr/bin/env python3
"""Media Automation Stack bootstrap entrypoint.

Project steward: Matthew Loschiavo (https://matthewloschiavo.com)
Contact: mploschiavo@gmail.com | https://www.linkedin.com/in/matthewloschiavo
"""

import argparse
import importlib
import logging
import os
import queue
import sys
import threading
import traceback

import media_stack.services.runtime_platform as runtime_platform
import media_stack.services.runtime_secrets as runtime_secrets
from media_stack.services.bootstrap_runner_service import (
    BootstrapRunnerDependencies,
    BootstrapRunnerService,
)
from media_stack.services.enums import BootstrapMode
from media_stack.services.operation_wiring import build_runner_event_registry
from media_stack.services.runtime_factory import (
    BootstrapCliArgs,
    BootstrapRuntimeFactoryDependencies,
    BootstrapRuntimeFactoryService,
)

logger = logging.getLogger("bootstrap_service")


# ---------------------------------------------------------------------------
# Handler spec loading and execution
# ---------------------------------------------------------------------------

def _load_handler_specs(key: str) -> list[dict]:
    """Load handler specs from bootstrap config by key name."""
    import json

    main_config = os.environ.get("BOOTSTRAP_CONFIG_FILE", "/contracts/media-stack.config.json")
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
    parallel: bool = True,
) -> None:
    """Run a list of handler specs with standard context injection.

    When parallel=True (default), independent handlers run concurrently.
    Handlers with export_env=True run first (sequentially) since they
    set environment variables needed by subsequent handlers.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    config_root = args.config_root
    admin_user = os.environ.get("STACK_ADMIN_USERNAME", "admin")
    admin_pass = os.environ.get("STACK_ADMIN_PASSWORD", "media-dev")

    context = {
        "config_root": config_root,
        "admin_username": admin_user,
        "admin_password": admin_pass,
        "log": runtime_platform.log,
    }

    def _exec_spec(spec: dict) -> None:
        name = str(spec.get("name", "unknown")).strip()
        handler_path = str(spec.get("handler", "")).strip()
        extra_args = dict(spec.get("args") or {})
        export_env = bool(spec.get("export_env", False))
        optional = bool(spec.get("optional", True))

        if not handler_path:
            return

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

    # Split: env-exporting specs run first (they set vars others need).
    env_specs = [s for s in specs if s.get("export_env")]
    parallel_specs = [s for s in specs if not s.get("export_env")]

    for spec in env_specs:
        _exec_spec(spec)

    if not parallel or len(parallel_specs) <= 1:
        for spec in parallel_specs:
            _exec_spec(spec)
        return

    runtime_platform.log(
        f"[{phase_label}] Running {len(parallel_specs)} handlers in parallel..."
    )
    with ThreadPoolExecutor(max_workers=min(6, len(parallel_specs))) as pool:
        futures = {
            pool.submit(_exec_spec, spec): str(spec.get("name", "?"))
            for spec in parallel_specs
        }
        for future in as_completed(futures):
            try:
                future.result()
            except Exception:
                pass  # Errors already recorded in state by _exec_spec


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
    specs = _load_handler_specs("container_preflight_handlers")
    _run_handler_specs(specs, state, args, phase_label="PREFLIGHT")


def _run_post_bootstrap(state: object, args: argparse.Namespace) -> None:
    specs = _load_handler_specs("container_post_bootstrap_handlers")
    _run_handler_specs(specs, state, args, phase_label="POST-BOOTSTRAP")


# ---------------------------------------------------------------------------
# Config policy from profile YAML
# ---------------------------------------------------------------------------

def _build_config_policy() -> object | None:
    """Build a config policy callable from the profile YAML."""
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

    gateway_host = routing.get("gateway_host") or os.environ.get("APP_GATEWAY_HOST", "")
    if not gateway_host and route_strategy in ("hybrid", "path-prefix") and stack_subdomain:
        parts = [p for p in ["apps", stack_subdomain, base_domain] if p]
        gateway_host = ".".join(parts).lower()
    media_server_direct_host = str((routing.get("direct_hosts") or {}).get("media_server", ""))
    if not media_server_direct_host and stack_subdomain and base_domain:
        parts = [p for p in ["jellyfin", stack_subdomain, base_domain] if p]
        media_server_direct_host = ".".join(parts).lower()

    from media_stack.services.apps.stack.bootstrap_config_policy import (
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


# ---------------------------------------------------------------------------
# Runner build (reusable across actions)
# ---------------------------------------------------------------------------

def _build_runner(args: argparse.Namespace, *, auto_prowlarr_indexers: bool = False) -> tuple:
    """Build the bootstrap runner and runtime state from CLI args."""
    servarr_runtime_arr_ops = importlib.import_module(
        "media_stack.services.apps.servarr.runtime.arr_ops"
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
            auto_prowlarr_indexers=auto_prowlarr_indexers or args.auto_prowlarr_indexers,
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


# ---------------------------------------------------------------------------
# One-shot mode (used by Docker Compose)
# ---------------------------------------------------------------------------

def _run_oneshot(args: argparse.Namespace) -> None:
    """Original one-shot bootstrap mode."""
    runner, runtime_state = _build_runner(args)
    runner.run(runtime_state)


# ---------------------------------------------------------------------------
# Profile env setup
# ---------------------------------------------------------------------------

def _apply_profile_env(profile_file: str | None) -> None:
    """Read the bootstrap profile YAML and set env vars that the runtime factory expects."""
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


# ---------------------------------------------------------------------------
# Action dispatcher — the core of the persistent service
# ---------------------------------------------------------------------------

_OVERRIDE_ENV_MAP = {
    "auto_download_content": "AUTO_DOWNLOAD_CONTENT",
    "preconfigure_api_keys": "PRECONFIGURE_API_KEYS",
    "apply_initial_preferences": "APPLY_INITIAL_PREFERENCES",
}


def _apply_overrides(overrides: dict) -> None:
    """Apply runtime overrides to environment variables."""
    for key, env_var in _OVERRIDE_ENV_MAP.items():
        if key in overrides:
            os.environ[env_var] = "1" if overrides[key] else "0"


def _dispatch_action(
    action_name: str,
    overrides: dict,
    args: argparse.Namespace,
    state: object,
) -> None:
    """Route an action to the appropriate handler."""
    _apply_overrides(overrides)
    runtime_platform.log(f"[ACTION] {action_name}: starting (overrides={overrides})")

    if action_name == "bootstrap":
        _action_bootstrap(args, state)
    elif action_name == "auto-indexers":
        _action_auto_indexers(args, state)
    elif action_name == "restart-apps":
        _action_restart_apps(args, state)
    elif action_name == "sync-indexers":
        _action_sync_indexers(args, state)
    elif action_name == "envoy-config":
        _action_envoy_config(args, state)
    elif action_name == "reconcile":
        _action_reconcile(args, state)
    else:
        raise ValueError(f"Unknown action: {action_name}")

    runtime_platform.log(f"[ACTION] {action_name}: complete")


def _action_bootstrap(args: argparse.Namespace, state: object) -> None:
    """Full bootstrap pipeline: preflights + configure all apps + post-bootstrap."""
    run_preflights = os.environ.get("BOOTSTRAP_RUN_PREFLIGHTS", "1") == "1"
    if run_preflights:
        _run_preflights(state, args)

    runner, runtime_state = _build_runner(args)
    bootstrap_error = None
    try:
        runner.run(runtime_state)
    except Exception as run_exc:
        bootstrap_error = run_exc
        runtime_platform.log(f"[ERR] Bootstrap pipeline: {run_exc}")

    # Post-bootstrap handlers run even on partial success.
    _run_post_bootstrap(state, args)

    if bootstrap_error:
        raise bootstrap_error

    runtime_platform.log("[OK] Bootstrap completed successfully")


def _action_auto_indexers(args: argparse.Namespace, state: object) -> None:
    """Run Prowlarr auto-indexer discovery (indexer phase only, not full pipeline)."""
    runtime_platform.log("[INFO] Auto-indexer: building runner with auto_prowlarr_indexers=True")
    runner, runtime_state = _build_runner(args, auto_prowlarr_indexers=True)
    # Only run the indexer steps — skip prechecks, servarr pipeline, and post-servarr.
    try:
        runner._run_runner_plan_phase(runtime_state, "indexer_steps")
    except Exception:
        # Fall back to full run if indexer_steps phase isn't available.
        runtime_platform.log("[WARN] indexer_steps phase not available, running full pipeline")
        runner.run(runtime_state)
    runtime_platform.log("[OK] Auto-indexer discovery complete")


def _action_restart_apps(args: argparse.Namespace, state: object) -> None:
    """Restart all apps to pick up config changes."""
    specs = _load_handler_specs("container_post_bootstrap_handlers")
    restart_specs = [s for s in specs if s.get("name") == "restart_apps"]
    if restart_specs:
        _run_handler_specs(restart_specs, state, args, phase_label="RESTART")
    else:
        runtime_platform.log("[WARN] No restart_apps handler found in config")


def _action_sync_indexers(args: argparse.Namespace, state: object) -> None:
    """Trigger Prowlarr ApplicationIndexerSync."""
    from media_stack.services.apps.prowlarr import pipeline_service as prowlarr_svc

    prowlarr_url = os.environ.get("PROWLARR_URL", "http://prowlarr:9696")
    api_key = runtime_secrets.read_api_key(args.config_root, "prowlarr")
    runtime_platform.log(f"[INFO] Triggering indexer sync on {prowlarr_url}")
    prowlarr_svc.trigger_indexer_sync(prowlarr_url, api_key, log=runtime_platform.log)
    runtime_platform.log("[OK] Indexer sync triggered")


def _action_envoy_config(args: argparse.Namespace, state: object) -> None:
    """Regenerate Envoy routing config from profile and bootstrap config."""
    from media_stack.cli.commands.generate_envoy_config_main import main as generate_envoy_config

    # Set env vars expected by the envoy config generator.
    # On K8s, Envoy listens on non-privileged 8080; on compose, it listens on 80.
    is_k8s = bool(os.environ.get("K8S_NAMESPACE"))
    default_listener_port = "8080" if is_k8s else "80"
    os.environ.setdefault("COMPOSE_FILE", "/dev/null")
    os.environ.setdefault("CONFIG_ROOT", args.config_root)
    os.environ.setdefault("ENVOY_LISTENER_PORT", default_listener_port)
    runtime_platform.log("[INFO] Generating Envoy config")
    generate_envoy_config()
    runtime_platform.log("[OK] Envoy config written")

    # Restart Envoy to pick up the new config.
    try:
        namespace = os.environ.get("K8S_NAMESPACE", "")
        if namespace:
            from kubernetes import client as k8s_client, config as k8s_config

            try:
                k8s_config.load_incluster_config()
            except Exception:
                k8s_config.load_kube_config()
            apps_v1 = k8s_client.AppsV1Api()
            # Trigger rollout restart by patching the pod template annotation.
            import time as _time

            patch = {
                "spec": {
                    "template": {
                        "metadata": {
                            "annotations": {
                                "bootstrap.media-stack.io/restart-trigger": str(int(_time.time()))
                            }
                        }
                    }
                }
            }
            apps_v1.patch_namespaced_deployment("envoy", namespace, body=patch)
            runtime_platform.log("[OK] Envoy deployment restart triggered")
    except Exception as exc:
        runtime_platform.log(f"[WARN] Could not restart Envoy: {exc}")


def _action_reconcile(args: argparse.Namespace, state: object) -> None:
    """Lightweight reconcile — re-run bootstrap in idempotent mode."""
    runner, runtime_state = _build_runner(args)
    runner.run(runtime_state)
    runtime_platform.log("[OK] Reconcile complete")


# ---------------------------------------------------------------------------
# Serve mode — persistent HTTP API server with action dispatch loop
# ---------------------------------------------------------------------------

def _run_serve(args: argparse.Namespace) -> None:
    """HTTP API server with action dispatch loop.

    The server stays alive indefinitely, processing actions from a queue.
    Actions are triggered via POST /actions/{name} or POST /run.
    """
    from media_stack.api.server import _fire_webhooks, start_api_server
    from media_stack.api.state import BootstrapState

    # Load profile if available (ConfigMap may not be mounted yet on first start).
    profile_file = os.environ.get("BOOTSTRAP_PROFILE_FILE")
    if profile_file:
        profile_path = __import__("pathlib").Path(profile_file)
        if profile_path.exists():
            from media_stack.api.preflight.profile_validation import validate_profile

            validate_profile(profile_file, log=runtime_platform.log)
            _apply_profile_env(profile_file)
        else:
            runtime_platform.log(
                f"[INFO] Profile not yet available at {profile_file} — "
                "will apply from config when action is triggered"
            )

    state = BootstrapState()
    port = int(args.api_port or os.environ.get("BOOTSTRAP_API_PORT", "9100"))
    action_queue: queue.Queue[tuple[str, dict]] = queue.Queue()
    action_timeout = int(os.environ.get("BOOTSTRAP_ACTION_TIMEOUT", "600"))
    max_retries = int(os.environ.get("BOOTSTRAP_ACTION_MAX_RETRIES", "0"))

    def action_trigger(action_name: str, overrides: dict) -> None:
        action_queue.put((action_name, overrides))

    # Wrap runtime_platform.log to also feed the SSE ring buffer.
    _original_log = runtime_platform.log

    def _instrumented_log(msg: str) -> None:
        _original_log(msg)
        state.append_log(msg)

    runtime_platform.log = _instrumented_log

    def reload_config() -> None:
        """Reload profile YAML and re-apply env vars."""
        pf = os.environ.get("BOOTSTRAP_PROFILE_FILE")
        if pf:
            from media_stack.api.preflight.profile_validation import validate_profile

            validate_profile(pf, log=runtime_platform.log)
            _apply_profile_env(pf)
        runtime_platform.log("[OK] Config reloaded from profile")

    server = start_api_server(
        state, port=port, action_trigger=action_trigger, reload_config=reload_config,
    )
    runtime_platform.log(f"[INFO] Bootstrap service listening on :{port}")
    runtime_platform.log(f"[INFO] Dashboard: http://127.0.0.1:{port}/")
    runtime_platform.log(f"[INFO] Actions: POST /actions/{{name}} | GET /status")
    runtime_platform.log(f"[INFO] SSE log stream: GET /logs/stream")

    if args.auto_run:
        runtime_platform.log("[INFO] Auto-run: queuing initial bootstrap action")
        action_queue.put(("bootstrap", {}))

    # Main action dispatch loop — runs forever.
    while True:
        try:
            action_name, overrides = action_queue.get()
        except KeyboardInterrupt:
            runtime_platform.log("[INFO] Shutting down bootstrap service")
            server.shutdown()
            return

        # Retry support: allow per-action retry via override or env default.
        retry_limit = int(overrides.pop("retry", max_retries))
        attempt = 0

        while True:
            attempt += 1
            action_record = state.start_action(
                action_name, overrides=overrides, timeout_seconds=action_timeout
            )
            suffix = f" (attempt {attempt}/{retry_limit + 1})" if retry_limit > 0 else ""
            runtime_platform.log(
                f"[ACTION] {action_name} [{action_record.id}]: dispatching "
                f"(timeout={action_timeout}s){suffix}"
            )

            try:
                _dispatch_action(action_name, overrides, args, state)
                state.finish_action()

                # Fire webhooks on success.
                _fire_webhooks(state, "action_complete", {
                    "action": action_name,
                    "status": "complete",
                    "elapsed_seconds": action_record.elapsed_seconds,
                })

                # Mark initial bootstrap done on first successful bootstrap.
                if action_name == "bootstrap" and not state.initial_bootstrap_done:
                    state.initial_bootstrap_done = True
                    runtime_platform.log("[INFO] Initial bootstrap complete — service is ready")

                    # Auto-generate envoy config after first bootstrap.
                    runtime_platform.log("[INFO] Auto-queuing envoy-config after bootstrap")
                    action_queue.put(("envoy-config", {}))

                break  # Success — exit retry loop.

            except Exception as exc:
                state.finish_action(error=str(exc))
                runtime_platform.log(f"[ERR] Action {action_name} failed: {exc}")
                trace = traceback.format_exc().strip()
                if trace:
                    for line in trace.splitlines():
                        runtime_platform.log(f"[TRACE] {line}")

                # Still mark initial bootstrap done if it was the bootstrap action
                # and the error was in post-bootstrap (apps were configured).
                if action_name == "bootstrap" and not state.initial_bootstrap_done:
                    state.initial_bootstrap_done = True
                    runtime_platform.log(
                        "[WARN] Initial bootstrap had errors but service is marked ready"
                    )

                # Retry if attempts remain.
                if attempt <= retry_limit:
                    delay = min(10.0, 2.0 ** (attempt - 1))
                    runtime_platform.log(
                        f"[RETRY] {action_name}: retrying in {delay:.0f}s "
                        f"(attempt {attempt}/{retry_limit + 1})"
                    )
                    import time as _time

                    _time.sleep(delay)
                    continue

                # Fire webhooks on final failure.
                _fire_webhooks(state, "action_error", {
                    "action": action_name,
                    "status": "error",
                    "error": str(exc),
                    "elapsed_seconds": action_record.elapsed_seconds,
                })
                break  # Exhausted retries.


# ---------------------------------------------------------------------------
# CLI entrypoint
# ---------------------------------------------------------------------------

def main():
    logging.basicConfig(
        level=logging.DEBUG if os.environ.get("BOOTSTRAP_DEBUG") else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S%z",
    )

    parser = argparse.ArgumentParser(
        description="Idempotent bootstrap for Arr + Prowlarr + Jellyseerr integration."
    )
    parser.add_argument(
        "--config", default="/contracts/config.json", help="Bootstrap config JSON path"
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
        help="Start persistent HTTP API service with action dispatch loop",
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
