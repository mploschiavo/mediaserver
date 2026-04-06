"""Config-driven media-server event plan execution."""

from __future__ import annotations

from typing import Any

from ..runner_phase_plan_service import run_phase_plan as run_event_phase_plan
from .base import MediaServerAdapterContext

_ARG_TOKEN_ATTRS: dict[str, str] = {
    "cfg": "cfg",
    "config_root": "config_root",
    "wait_timeout": "wait_timeout",
    "arr_apps_raw": "arr_apps_raw",
    "app_keys": "app_keys",
    "torrent_client_cfg": "torrent_client_cfg",
    "torrent_client_username": "torrent_client_username",
    "torrent_client_password": "torrent_client_password",
    "qbit_cfg": "qbit_cfg",
    "qb_user": "qb_user",
    "qb_pass": "qb_pass",
    "prowlarr_url": "prowlarr_url",
    "prowlarr_key": "prowlarr_key",
}


def resolve_backend_plan(adapter_hooks_cfg: dict[str, Any] | None, backend: str) -> dict[str, Any]:
    hooks = adapter_hooks_cfg or {}
    plans = hooks.get("media_server_event_plans") or hooks.get("media_server_operation_plans") or {}
    if not isinstance(plans, dict):
        return {}
    key = str(backend or "").strip().lower()
    if not key:
        return {}
    selected = plans.get(key)
    return selected if isinstance(selected, dict) else {}


def run_phase_plan(
    context: MediaServerAdapterContext,
    plan_cfg: dict[str, Any],
    phase_name: str,
) -> bool:
    return run_event_phase_plan(
        runtime=context.runtime,
        plan_cfg=plan_cfg,
        phase_name=phase_name,
        invoke_event=context.invoke,
        run_optional_step=context.run_optional,
        log=context.log,
        arg_token_attrs=_ARG_TOKEN_ATTRS,
    )
