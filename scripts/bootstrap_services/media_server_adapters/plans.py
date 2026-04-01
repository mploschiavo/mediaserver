"""Config-driven media-server operation plan execution."""

from __future__ import annotations

from typing import Any

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
    plans = hooks.get("media_server_operation_plans") or {}
    if not isinstance(plans, dict):
        return {}
    key = str(backend or "").strip().lower()
    if not key:
        return {}
    selected = plans.get(key)
    return selected if isinstance(selected, dict) else {}


def _resolve_step_args(runtime: Any, step_cfg: dict[str, Any]) -> tuple[Any, ...]:
    raw_args = step_cfg.get("args", [])
    if raw_args is None:
        return ()
    if not isinstance(raw_args, list):
        raise ValueError("media server step 'args' must be an array when provided.")

    args: list[Any] = []
    for token in raw_args:
        if isinstance(token, str):
            attr = _ARG_TOKEN_ATTRS.get(token)
            if attr is not None:
                args.append(getattr(runtime, attr))
                continue
        args.append(token)
    return tuple(args)


def _resolve_steps_for_phase(
    plan_cfg: dict[str, Any], phase_name: str
) -> tuple[list[dict[str, Any]], str]:
    phase_cfg = plan_cfg.get(phase_name)
    if isinstance(phase_cfg, list):
        return [item for item in phase_cfg if isinstance(item, dict)], ""
    if not isinstance(phase_cfg, dict):
        return [], ""
    steps = phase_cfg.get("steps")
    if not isinstance(steps, list):
        return [], str(phase_cfg.get("complete_message") or "").strip()
    return [item for item in steps if isinstance(item, dict)], str(
        phase_cfg.get("complete_message") or ""
    ).strip()


def run_phase_plan(
    context: MediaServerAdapterContext,
    plan_cfg: dict[str, Any],
    phase_name: str,
) -> bool:
    steps, complete_message = _resolve_steps_for_phase(plan_cfg, phase_name)
    if not steps:
        return False

    rt = context.runtime
    for step in steps:
        operation = str(step.get("operation") or "").strip()
        if not operation:
            continue
        args = _resolve_step_args(rt, step)

        enabled = bool(step.get("enabled", True))
        enabled_attr = str(step.get("enabled_attr") or "").strip()
        if enabled_attr:
            enabled = bool(getattr(rt, enabled_attr, False))

        required = bool(step.get("required", False))
        required_attr = str(step.get("required_attr") or "").strip()
        if required_attr:
            required = bool(getattr(rt, required_attr, False))

        use_optional = bool(step.get("optional", False)) or bool(enabled_attr or required_attr)
        if use_optional:
            warning_message = str(step.get("warning_message") or "").strip()
            if not warning_message:
                warning_message = (
                    f"[WARN] Media server operation '{operation}' skipped. "
                    "Set corresponding *.required=true to fail the bootstrap instead."
                )
            context.run_optional(
                enabled=enabled,
                required=required,
                action=lambda op=operation, op_args=args: context.invoke(op, *op_args),
                warning_message=warning_message,
            )
            continue

        context.invoke(operation, *args)

    if complete_message:
        context.log(complete_message)
    return True
