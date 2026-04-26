"""Runner build and config policy for the bootstrap controller."""

from __future__ import annotations

import argparse
import importlib
import os

import media_stack.services.runtime_platform as runtime_platform
import media_stack.services.runtime_secrets as runtime_secrets
from media_stack.services.controller_service import (
    ControllerDependencies,
    ControllerService,
)
from media_stack.services.enums import BootstrapMode
from media_stack.services.operation_wiring import build_runner_event_registry
from media_stack.services.runtime_factory import (
    ControllerCliArgs,
    ControllerRuntimeFactoryDependencies,
    ControllerRuntimeFactoryService,
)

from media_stack.application.jobs.controller_handlers import _resolve_config_path


# ---------------------------------------------------------------------------
# Config policy from profile YAML
# ---------------------------------------------------------------------------

def _build_config_policy() -> object | None:
    """Build a config policy callable from the profile YAML."""
    profile_file = os.environ.get("BOOTSTRAP_PROFILE_FILE", "")
    if not profile_file:
        return None

    path = __import__("pathlib").Path(profile_file)
    if not path.is_file():
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
        # Use the primary media server from the profile; derive default from registry.
        from media_stack.api.services.registry import SERVICES as _reg_services
        _default_ms_id = next((s.id for s in _reg_services if s.category == "media" and s.host), "media")
        media_server_id = str((routing.get("direct_hosts") or {}).get("media_server_id", _default_ms_id))
        parts = [p for p in [media_server_id, stack_subdomain, base_domain] if p]
        media_server_direct_host = ".".join(parts).lower()

    from media_stack.services.apps.stack.controller_config_policy import (
        apply_bootstrap_runtime_policy,
    )

    # Resolve auto_download_content: env var wins (operator can flip
    # at runtime via the dashboard "Auto-Downloads" toggle, which sets
    # the env). Otherwise fall back to the profile's bootstrap section.
    # Without the profile fallback, a fresh compose deploy with no env
    # set would default the policy to False, which silently disabled
    # enableAuto on every *arr import list — the lists existed but
    # never auto-added new content. (v1.0.141 root-cause for the
    # "we used to get more content" symptom on clean install.)
    profile_bootstrap = profile.get("bootstrap") or {}
    profile_auto_download = bool(profile_bootstrap.get("auto_download_content", False))
    env_auto_download_raw = os.environ.get("AUTO_DOWNLOAD_CONTENT", "").strip()
    if env_auto_download_raw:
        auto_download_content = env_auto_download_raw == "1"
    else:
        auto_download_content = profile_auto_download

    def policy(cfg: dict) -> None:
        apply_bootstrap_runtime_policy(
            cfg,
            selected_apps_csv="",
            preconfigure_api_keys=os.environ.get("PRECONFIGURE_API_KEYS", "1") == "1",
            auto_download_content=auto_download_content,
            internet_exposed=internet_exposed,
            route_strategy=route_strategy,
            ingress_domain=base_domain,
            app_gateway_host=gateway_host,
            app_gateway_port=gateway_port,
            app_path_prefix=path_prefix,
            media_server_direct_host=media_server_direct_host,
        )
        runtime_platform.log(
            f"[OK] Config policy applied (auto_download_content={auto_download_content})"
        )

    return policy


# ---------------------------------------------------------------------------
# Runner build (reusable across actions)
# ---------------------------------------------------------------------------

def _build_runner(args: argparse.Namespace, *, auto_prowlarr_indexers: bool = False) -> tuple:
    """Build the bootstrap runner and runtime state from CLI args."""
    # Dynamically load SABnzbd path mapping -- optional dependency.
    # If SABnzbd app code is removed, this gracefully returns an empty list.
    def _noop_sab_mappings(cfg: dict) -> list:
        return []

    build_sab_remote_path_mappings = _noop_sab_mappings
    try:
        servarr_runtime_arr_ops = importlib.import_module(
            "media_stack.services.apps.servarr.runtime.arr_ops"
        )
        build_sab_remote_path_mappings = getattr(
            servarr_runtime_arr_ops, "build_sab_remote_path_mappings",
            _noop_sab_mappings,
        )
    except ImportError:
        runtime_platform.log("[INFO] Usenet client path mappings not available -- skipping")

    runtime_factory = ControllerRuntimeFactoryService(
        deps=ControllerRuntimeFactoryDependencies(
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
    resolved_config = _resolve_config_path(args.config) or args.config
    build_result = runtime_factory.build_from_cli(
        ControllerCliArgs(
            mode=BootstrapMode.from_cli(args.mode),
            config_path=resolved_config,
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

    runner = ControllerService(
        deps=ControllerDependencies(
            log=runtime_platform.log,
            bool_cfg=runtime_platform.bool_cfg,
            normalize_url=runtime_platform.normalize_url,
            wait_for_service=runtime_platform.wait_for_service,
            operations=runner_operations,
        )
    )
    return runner, runtime_state
