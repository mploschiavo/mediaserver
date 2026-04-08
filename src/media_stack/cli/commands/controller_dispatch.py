"""Action dispatch, error tracking, and auto-heal for the bootstrap controller."""

from __future__ import annotations

import argparse
import os
import re

import media_stack.services.runtime_platform as runtime_platform


# ---------------------------------------------------------------------------
# Override env map
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


# ---------------------------------------------------------------------------
# Error tracking / auto-heal
# ---------------------------------------------------------------------------

_SERVICE_ERROR_PATTERNS = [
    # (regex_pattern, service_id_group_index)
    (r"(\w+): unable to detect API base", 1),
    (r"Unable to read API key for (\w+)", 1),
    (r"(\w+): failed (?:reading|creating|updating)", 1),
    (r"(\w+): API key unavailable", 1),
    (r"(\w+): (?:connection refused|timeout|unreachable)", 1),
]


def _track_failed_service(state, error_msg: str) -> None:
    """Parse error message to identify failed services and mark them in state."""
    for pattern, group_idx in _SERVICE_ERROR_PATTERNS:
        for match in re.finditer(pattern, error_msg, re.IGNORECASE):
            svc_id = match.group(group_idx).lower()
            if svc_id and len(svc_id) > 2:
                state.mark_service_failed(svc_id, error_msg)
                runtime_platform.log(f"[HEAL] Marked {svc_id} as failed for auto-heal")


# ---------------------------------------------------------------------------
# Action dispatch
# ---------------------------------------------------------------------------

def _dispatch_action(
    action_name: str,
    overrides: dict,
    args: argparse.Namespace,
    state: object,
) -> None:
    """Route an action to the appropriate handler."""
    from media_stack.cli.commands.action_handlers import (
        action_bootstrap, action_finalize, action_auto_indexers,
        action_restart_apps, action_sync_indexers, action_envoy_config,
        action_reconcile,
    )
    from media_stack.cli.commands.controller_handlers import (
        _load_handler_specs,
        _run_handler_specs,
        _run_preflights,
        _run_post_bootstrap,
    )
    from media_stack.cli.commands.controller_runner import (
        _build_runner,
    )
    from media_stack.cli.commands.controller_k8s import (
        _persist_preflight_keys_to_secret,
    )

    _apply_overrides(overrides)
    runtime_platform.log(f"[ACTION] {action_name}: starting (overrides={overrides})")

    if action_name == "bootstrap":
        action_bootstrap(args, state, _run_preflights, _persist_preflight_keys_to_secret, _build_runner)
    elif action_name == "finalize":
        action_finalize(args, state, _build_runner, _run_post_bootstrap)
    elif action_name == "auto-indexers":
        action_auto_indexers(args, _build_runner)
    elif action_name == "restart-apps":
        action_restart_apps(args, state, _load_handler_specs, _run_handler_specs)
    elif action_name == "sync-indexers":
        action_sync_indexers(args, _build_runner)
    elif action_name == "envoy-config":
        action_envoy_config(args)
    elif action_name == "reconcile":
        action_reconcile(args, _build_runner)
    else:
        raise ValueError(f"Unknown action: {action_name}")

    runtime_platform.log(f"[ACTION] {action_name}: complete")
