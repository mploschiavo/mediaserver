"""Action dispatch, error tracking, and auto-heal for the bootstrap controller."""

from __future__ import annotations

import argparse
import os
import re
import sys

import media_stack.services.runtime_platform as runtime_platform


# ---------------------------------------------------------------------------
# Override env map
# ---------------------------------------------------------------------------

_OVERRIDE_ENV_MAP = {
    "auto_download_content": "AUTO_DOWNLOAD_CONTENT",
    "preconfigure_api_keys": "PRECONFIGURE_API_KEYS",
    "apply_initial_preferences": "APPLY_INITIAL_PREFERENCES",
}


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


class ControllerDispatchCommand:
    def _apply_overrides(self, overrides: dict) -> None:
        """Apply runtime overrides to environment variables."""
        for key, env_var in _OVERRIDE_ENV_MAP.items():
            if key in overrides:
                os.environ[env_var] = "1" if overrides[key] else "0"

    def _track_failed_service(self, state, error_msg: str) -> None:
        """Parse error message to identify failed services and mark them in state."""
        for pattern, group_idx in _SERVICE_ERROR_PATTERNS:
            for match in re.finditer(pattern, error_msg, re.IGNORECASE):
                svc_id = match.group(group_idx).lower()
                if svc_id and len(svc_id) > 2:
                    state.mark_service_failed(svc_id, error_msg)
                    runtime_platform.log(f"[HEAL] Marked {svc_id} as failed for auto-heal")

    def _dispatch_action(
        self,
        action_name: str,
        overrides: dict,
        args: argparse.Namespace,
        state: object,
    ) -> None:
        """Route an action to the appropriate handler."""
        from media_stack.services.jobs.framework import run_job

        # Pull the trigger-source / actor metadata out of overrides
        # before they get logged or passed onward. ``_source`` /
        # ``_actor`` are control-plane fields, not user-set toggles —
        # leaving them in overrides would cause _apply_overrides to
        # log them and the action queue to surface them in the
        # pending-actions UI. The HTTP handler stamps ``_source =
        # "manual"`` and the auto-heal scheduler stamps
        # ``_source = "auto-heal"``; cron entrypoints set
        # ``_source = "cron:<mode>"`` directly via run_job's keyword.
        module = sys.modules[__name__]
        source = overrides.pop("_source", None)
        actor = overrides.pop("_actor_username", None)
        # Backwards-compat: older POSTs only set ``_triggered_by``.
        # Map ``user`` (or any non-system value) to ``manual`` so the
        # history entry is still tagged with intent rather than the
        # default ``unknown``.
        if source is None:
            legacy_trigger = str(overrides.get("_triggered_by", "")).strip().lower()
            if legacy_trigger == "scheduler":
                source = "scheduler"
            elif legacy_trigger and legacy_trigger != "system":
                source = "manual"
                if not actor:
                    actor = legacy_trigger

        module._apply_overrides(overrides)
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
        result = run_job(action_name, source=source, actor=actor)
        if not result:
            raise ValueError(f"Unknown action: {action_name}")
        if result.get("error"):
            raise RuntimeError(result["error"])

        runtime_platform.log(f"[ACTION] {action_name}: complete")


_INSTANCE = ControllerDispatchCommand()
_apply_overrides = _INSTANCE._apply_overrides
_track_failed_service = _INSTANCE._track_failed_service
_dispatch_action = _INSTANCE._dispatch_action
