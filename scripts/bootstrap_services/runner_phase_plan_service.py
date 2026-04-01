"""Config-driven runner phase plan execution."""

from __future__ import annotations

from typing import Any, Callable

from .enums import RunnerEvent

RunOptionalStepFn = Callable[..., None]
InvokeEventFn = Callable[..., Any]
LogFn = Callable[[str], None]

DEFAULT_ARG_TOKEN_ATTRS: dict[str, str] = {
    "cfg": "cfg",
    "config_root": "config_root",
    "wait_timeout": "wait_timeout",
    "arr_apps_raw": "arr_apps_raw",
    "app_keys": "app_keys",
    "torrent_client_cfg": "torrent_client_cfg",
    "torrent_client_username": "torrent_client_username",
    "torrent_client_password": "torrent_client_password",
    "qbit_cfg": "qbit_cfg",
    "app_auth_cfg": "app_auth_cfg",
    "qb_user": "qb_user",
    "qb_pass": "qb_pass",
    "prowlarr_url": "prowlarr_url",
    "prowlarr_key": "prowlarr_key",
    "prowlarr_indexers": "prowlarr_indexers",
    "auto_indexers": "auto_indexers",
    "trigger_sync": "trigger_sync",
}


def _resolve_runtime_bool_attr(runtime: Any, attr: str, default: bool) -> bool:
    if hasattr(runtime, attr):
        return bool(getattr(runtime, attr))
    feature_flags = getattr(runtime, "feature_flags", None)
    if isinstance(feature_flags, dict):
        return bool(feature_flags.get(attr, default))
    return default


def _has_runtime_value(runtime: Any, attr: str) -> bool:
    if hasattr(runtime, attr):
        value = getattr(runtime, attr)
    else:
        runtime_values = getattr(runtime, "runtime_values", None)
        value = runtime_values.get(attr) if isinstance(runtime_values, dict) else None
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, set, dict)):
        return len(value) > 0
    return bool(value)


def _resolve_step_args(
    runtime: Any,
    step_cfg: dict[str, Any],
    *,
    arg_token_attrs: dict[str, str],
) -> tuple[Any, ...]:
    raw_args = step_cfg.get("args", [])
    if raw_args is None:
        return ()
    if not isinstance(raw_args, list):
        raise ValueError("runner phase step 'args' must be an array when provided.")

    args: list[Any] = []
    for token in raw_args:
        if isinstance(token, str):
            attr = arg_token_attrs.get(token)
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


def _resolve_step_event_and_handler(step_cfg: dict[str, Any]) -> tuple[str, str]:
    event_raw = str(step_cfg.get("event") or "").strip()
    handler_raw = str(step_cfg.get("handler") or "").strip()
    operation_raw = str(step_cfg.get("operation") or "").strip()

    if not event_raw and operation_raw:
        event_raw = RunnerEvent.RUN.value
    if not handler_raw:
        handler_raw = operation_raw

    if not event_raw or not handler_raw:
        return "", ""

    event_key = RunnerEvent.from_value(event_raw).value
    return event_key, handler_raw


def run_phase_plan(
    *,
    runtime: Any,
    plan_cfg: dict[str, Any],
    phase_name: str,
    invoke_event: InvokeEventFn | None = None,
    invoke_operation: InvokeEventFn | None = None,
    run_optional_step: RunOptionalStepFn,
    log: LogFn,
    arg_token_attrs: dict[str, str] | None = None,
) -> bool:
    """Run one configured runner phase plan.

    The plan format mirrors media-server operation plans so behavior remains
    declarative and technology-specific choices stay in config.
    """

    steps, complete_message = _resolve_steps_for_phase(plan_cfg, phase_name)
    if not steps:
        return False

    if callable(invoke_event):
        invoke = invoke_event
    elif callable(invoke_operation):

        def invoke(_event: str, handler: str, *op_args: Any) -> Any:
            return invoke_operation(handler, *op_args)

    else:
        raise ValueError("run_phase_plan requires invoke_event (or legacy invoke_operation).")

    resolved_tokens = arg_token_attrs or DEFAULT_ARG_TOKEN_ATTRS

    for step in steps:
        event_name, handler_name = _resolve_step_event_and_handler(step)
        if not event_name or not handler_name:
            continue
        args = _resolve_step_args(runtime, step, arg_token_attrs=resolved_tokens)

        enabled = bool(step.get("enabled", True))
        enabled_attr = str(step.get("enabled_attr") or "").strip()
        if enabled_attr:
            enabled = _resolve_runtime_bool_attr(runtime, enabled_attr, False)
        enabled_when_attr = str(step.get("enabled_when_attr") or "").strip()
        if enabled_when_attr:
            enabled = enabled and _has_runtime_value(runtime, enabled_when_attr)

        required = bool(step.get("required", False))
        required_attr = str(step.get("required_attr") or "").strip()
        if required_attr:
            required = _resolve_runtime_bool_attr(runtime, required_attr, False)

        use_optional = bool(step.get("optional", False)) or bool(enabled_attr or required_attr)
        if use_optional:
            warning_message = str(step.get("warning_message") or "").strip()
            if not warning_message:
                warning_message = (
                    f"[WARN] Runner handler '{handler_name}' skipped. "
                    "Set corresponding *.required=true to fail the bootstrap instead."
                )
            run_optional_step(
                enabled=enabled,
                required=required,
                action=lambda evt=event_name, key=handler_name, op_args=args: invoke(
                    evt,
                    key,
                    *op_args,
                ),
                warning_message=warning_message,
            )
            continue

        if not enabled:
            continue

        invoke(event_name, handler_name, *args)

    if complete_message:
        log(complete_message)
    return True
