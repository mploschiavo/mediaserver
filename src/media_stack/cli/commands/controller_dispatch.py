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
    from media_stack.cli.commands.job_framework import run_job

    _apply_overrides(overrides)
    runtime_platform.log(f"[DEBUG] Action dispatch: name={action_name}, overrides={overrides}, "
                         f"config_root={os.environ.get('CONFIG_ROOT','?')}, "
                         f"profile={os.environ.get('BOOTSTRAP_PROFILE_FILE','?')}")
    runtime_platform.log(f"[ACTION] {action_name}: starting (overrides={overrides})")

    # Single dispatch path. Every action is a contract-declared
    # job (or an alias that resolves to one — see
    # ``contracts/services/core.yaml``'s ``job_aliases`` map).
    # ``run_job`` resolves the alias, walks the tree, enforces
    # prereqs, and runs the handler. A ratchet test
    # (``test_no_action_special_cases``) keeps this function from
    # accruing per-action elif branches again.
    result = run_job(action_name)
    if not result:
        raise ValueError(f"Unknown action: {action_name}")
    if result.get("error"):
        raise RuntimeError(result["error"])

    runtime_platform.log(f"[ACTION] {action_name}: complete")
