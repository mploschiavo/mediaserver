"""Config-driven media-server event plan execution.

Application-layer orchestration: resolves the backend-keyed plan from
the runtime ``adapter_hooks_cfg`` and dispatches each step against
the runner phase-plan service. Knows nothing about HTTP — the actual
operations are looked up via ``invoke_event`` on the context.
"""

from __future__ import annotations

import importlib as _importlib
from typing import Any

from media_stack.domain.media_server_adapters.protocols import (
    MediaServerAdapterContext,
)
from media_stack.services.runner_phase_plan_service import (
    run_phase_plan as run_event_phase_plan,
)


class MediaServerPlanService:
    @staticmethod
    def _load_indexer_token_aliases():
        from media_stack.core.service_registry.registry import SERVICES
        for svc in SERVICES:
            if not svc.indexer_path:
                continue
            try:
                mod = _importlib.import_module(f"media_stack.services.apps.{svc.id}.runtime_compat")
                return getattr(mod, "LEGACY_ARG_TOKEN_ALIASES", {})
            except (ImportError, ModuleNotFoundError):
                continue
        return {}

    _PROWLARR_TOKEN_ALIASES = _load_indexer_token_aliases()

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
        "indexer_manager_url": "prowlarr_url",
        "indexer_manager_key": "prowlarr_key",
        # Legacy token aliases from app-layer compat modules.
        **_PROWLARR_TOKEN_ALIASES,
    }

    def resolve_backend_plan(
        self,
        adapter_hooks_cfg: dict[str, Any] | None,
        backend: str,
    ) -> dict[str, Any]:
        hooks = adapter_hooks_cfg or {}
        plans = (
            hooks.get("media_server_event_plans")
            or hooks.get("media_server_operation_plans")
            or {}
        )
        if not isinstance(plans, dict):
            return {}
        key = str(backend or "").strip().lower()
        if not key:
            return {}
        selected = plans.get(key)
        return selected if isinstance(selected, dict) else {}

    def run_phase_plan(
        self,
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
            arg_token_attrs=self._ARG_TOKEN_ATTRS,
        )


_instance = MediaServerPlanService()
resolve_backend_plan = _instance.resolve_backend_plan
run_phase_plan = _instance.run_phase_plan
_load_indexer_token_aliases = _instance._load_indexer_token_aliases
_ARG_TOKEN_ATTRS = _instance._ARG_TOKEN_ATTRS
_PROWLARR_TOKEN_ALIASES = _instance._PROWLARR_TOKEN_ALIASES
