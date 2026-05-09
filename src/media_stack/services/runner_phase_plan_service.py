"""Config-driven runner phase plan execution."""

from __future__ import annotations

import importlib as _importlib
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable

from .enums import RunnerEvent


class _IndexerTokenAliasesLoader:
    """Bootstrap-time helper that resolves ``LEGACY_ARG_TOKEN_ALIASES``
    from the first registered indexer's runtime_compat module.

    Lives at module scope so the class-level
    ``DEFAULT_ARG_TOKEN_ATTRS`` literal can reference it before the
    primary service singleton is built.
    """

    def load(self) -> dict[str, str]:
        from media_stack.core.service_registry.registry import SERVICES
        for svc in SERVICES:
            if not svc.indexer_path:
                continue
            try:
                mod = _importlib.import_module(
                    f"media_stack.services.apps.{svc.id}.runtime_compat"
                )
                return getattr(mod, "LEGACY_ARG_TOKEN_ALIASES", {})
            except (ImportError, ModuleNotFoundError):
                continue
        return {}


_LOADER_INSTANCE = _IndexerTokenAliasesLoader()
_PROWLARR_TOKEN_ALIASES = _LOADER_INSTANCE.load()


class RunnerPhasePlanService:
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
        "indexer_manager_url": "prowlarr_url",
        "indexer_manager_key": "prowlarr_key",
        "indexer_manager_indexers": "indexer_entries",
        "auto_indexers": "auto_indexers",
        "trigger_sync": "trigger_sync",
        # Legacy arg-token aliases from app-layer compat modules.
        **_PROWLARR_TOKEN_ALIASES,
    }


    def _load_indexer_token_aliases(self) -> dict[str, str]:
        return _LOADER_INSTANCE.load()

    def _resolve_runtime_bool_attr(self, runtime: Any, attr: str, default: bool) -> bool:
        if hasattr(runtime, attr):
            return bool(getattr(runtime, attr))
        feature_flags = getattr(runtime, "feature_flags", None)
        if isinstance(feature_flags, dict):
            return bool(feature_flags.get(attr, default))
        return default
    
    
    def _has_runtime_value(self, runtime: Any, attr: str) -> bool:
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
        self,
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
        self, plan_cfg: dict[str, Any], phase_name: str
    ) -> tuple[list[dict[str, Any]], str, bool]:
        phase_cfg = plan_cfg.get(phase_name)
        if isinstance(phase_cfg, list):
            return [item for item in phase_cfg if isinstance(item, dict)], "", False
        if not isinstance(phase_cfg, dict):
            return [], "", False
        steps = phase_cfg.get("steps")
        parallel = bool(phase_cfg.get("parallel", False))
        if not isinstance(steps, list):
            return [], str(phase_cfg.get("complete_message") or "").strip(), parallel
        return (
            [item for item in steps if isinstance(item, dict)],
            str(phase_cfg.get("complete_message") or "").strip(),
            parallel,
        )
    
    
    def _resolve_step_event_and_handler(self, step_cfg: dict[str, Any]) -> tuple[str, str]:
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
    
    
    def _resolve_step_callable(
        self,
        step: dict[str, Any],
        *,
        runtime: Any,
        invoke: InvokeEventFn,
        run_optional_step: RunOptionalStepFn,
        resolved_tokens: dict[str, str],
    ) -> Callable[[], None] | None:
        """Resolve a single step config into a callable (or None if disabled/skipped)."""
        event_name, handler_name = self._resolve_step_event_and_handler(step)
        if not event_name or not handler_name:
            return None
        args = self._resolve_step_args(runtime, step, arg_token_attrs=resolved_tokens)
    
        enabled = bool(step.get("enabled", True))
        enabled_attr = str(step.get("enabled_attr") or "").strip()
        if enabled_attr:
            enabled = self._resolve_runtime_bool_attr(runtime, enabled_attr, False)
        enabled_when_attr = str(step.get("enabled_when_attr") or "").strip()
        if enabled_when_attr:
            enabled = enabled and self._has_runtime_value(runtime, enabled_when_attr)
    
        required = bool(step.get("required", False))
        required_attr = str(step.get("required_attr") or "").strip()
        if required_attr:
            required = self._resolve_runtime_bool_attr(runtime, required_attr, False)
    
        use_optional = bool(step.get("optional", False)) or bool(enabled_attr or required_attr)
        if use_optional:
            warning_message = str(step.get("warning_message") or "").strip()
            if not warning_message:
                warning_message = (
                    f"[WARN] Runner handler '{handler_name}' skipped. "
                    "Set corresponding *.required=true to fail the bootstrap instead."
                )
    
            def _run_optional(
                evt=event_name, key=handler_name, op_args=args,
                _en=enabled, _req=required, _msg=warning_message,
            ) -> None:
                run_optional_step(
                    enabled=_en,
                    required=_req,
                    action=lambda: invoke(evt, key, *op_args),
                    warning_message=_msg,
                )
    
            return _run_optional
    
        if not enabled:
            return None
    
        def _run_step(evt=event_name, key=handler_name, op_args=args) -> None:
            invoke(evt, key, *op_args)
    
        return _run_step
    
    
    def run_phase_plan(self, 
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
    
        When the phase config sets ``"parallel": true``, all resolved steps
        execute concurrently via ThreadPoolExecutor.  Otherwise they run
        sequentially (the original behaviour).
        """
    
        steps, complete_message, parallel = self._resolve_steps_for_phase(plan_cfg, phase_name)
        if not steps:
            return False
    
        if callable(invoke_event):
            invoke = invoke_event
        elif callable(invoke_operation):
    
            def invoke(_event: str, handler: str, *op_args: Any) -> Any:
                return invoke_operation(handler, *op_args)
    
        else:
            raise ValueError("run_phase_plan requires invoke_event (or legacy invoke_operation).")
    
        resolved_tokens = arg_token_attrs or self.DEFAULT_ARG_TOKEN_ATTRS
    
        # Resolve all steps into callables.
        callables: list[tuple[str, Callable[[], None]]] = []
        for step in steps:
            handler_name = str(step.get("handler") or step.get("operation") or "?")
            fn = self._resolve_step_callable(
                step,
                runtime=runtime,
                invoke=invoke,
                run_optional_step=run_optional_step,
                resolved_tokens=resolved_tokens,
            )
            if fn is not None:
                callables.append((handler_name, fn))
    
        if parallel and len(callables) > 1:
            log(f"[INFO] Phase '{phase_name}': running {len(callables)} steps in parallel")
            errors: list[str] = []
            with ThreadPoolExecutor(max_workers=min(6, len(callables))) as pool:
                futures = {pool.submit(fn): name for name, fn in callables}
                for future in as_completed(futures):
                    name = futures[future]
                    try:
                        future.result()
                    except Exception as exc:
                        errors.append(f"{name}: {exc}")
            if errors:
                raise RuntimeError(
                    f"Phase '{phase_name}' had parallel step failures: {'; '.join(errors)}"
                )
        else:
            for _, fn in callables:
                fn()
    
        if complete_message:
            log(complete_message)
        return True


_INSTANCE = RunnerPhasePlanService()
run_phase_plan = _INSTANCE.run_phase_plan
_has_runtime_value = _INSTANCE._has_runtime_value
_load_indexer_token_aliases = _INSTANCE._load_indexer_token_aliases
_resolve_runtime_bool_attr = _INSTANCE._resolve_runtime_bool_attr
_resolve_step_args = _INSTANCE._resolve_step_args
_resolve_step_callable = _INSTANCE._resolve_step_callable
_resolve_step_event_and_handler = _INSTANCE._resolve_step_event_and_handler
_resolve_steps_for_phase = _INSTANCE._resolve_steps_for_phase
DEFAULT_ARG_TOKEN_ATTRS = _INSTANCE.DEFAULT_ARG_TOKEN_ATTRS
