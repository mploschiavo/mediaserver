"""BootstrapJobConfigResolver — Repository for the bootstrap-job config sub-trees.

ADR-0015 Phase 7c. Pre-Phase-7c four config-resolution methods
(``_resolved_cfg``, ``_bootstrap_job_hooks``,
``_resolve_post_job_actions``, ``_resolve_call_handler_specs``)
lived on :class:`RunBootstrapJobRunner` in commands/.

This class owns the resolved-config cache + the parsing logic
for the two declarative hook surfaces (post-job actions +
call-handler specs).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from media_stack.cli.workflows.controller_post_job_actions_service import (
    ControllerPostJobAction,
)
from media_stack.core.exceptions import ConfigError
from media_stack.core.logging_utils import log_swallowed
from media_stack.core.service_registry.registry import _find_services_dir
from media_stack.services.controller_component_resolver import (
    resolve_bootstrap_component_plan,
)


if TYPE_CHECKING:
    from media_stack.cli.workflows.run_controller_job_cli_config_service import (
        RunBootstrapJobConfig,
    )


_DEFAULT_POST_JOB_ACTION_TIMEOUT_SECONDS = 180


class BootstrapJobConfigResolver:
    """Repository: resolve + cache the bootstrap config + hook sub-trees."""

    def __init__(self, cfg: "RunBootstrapJobConfig") -> None:
        self._cfg = cfg
        self._resolved_cfg_cache: dict[str, object] | None = None

    def resolved_cfg(self) -> dict[str, object]:
        if self._resolved_cfg_cache is None:
            self._resolved_cfg_cache = resolve_bootstrap_component_plan(
                self._cfg.config_file,
            ).config
        return self._resolved_cfg_cache

    def bootstrap_job_hooks(self) -> dict[str, object]:
        adapter_hooks = self.resolved_cfg().get("adapter_hooks")
        if not isinstance(adapter_hooks, dict):
            return {}
        bootstrap_job = adapter_hooks.get("bootstrap_job")
        if not isinstance(bootstrap_job, dict):
            return {}
        return bootstrap_job

    def resolve_post_job_actions(self) -> list[ControllerPostJobAction]:
        hooks = self.bootstrap_job_hooks()
        raw_actions = hooks.get("post_job_actions")
        if raw_actions is None:
            return []
        if not isinstance(raw_actions, list):
            raise ConfigError("adapter_hooks.bootstrap_job.post_job_actions must be an array")

        actions: list[ControllerPostJobAction] = []
        for idx, item in enumerate(raw_actions):
            if not isinstance(item, dict):
                raise ConfigError(
                    "adapter_hooks.bootstrap_job.post_job_actions"
                    f"[{idx}] must be an object"
                )
            marker = str(item.get("marker") or "").strip()
            phase_name = str(item.get("phase_name") or "").strip()
            deployment = str(item.get("deployment") or "").strip()
            if not marker or not phase_name or not deployment:
                raise ConfigError(
                    "adapter_hooks.bootstrap_job.post_job_actions"
                    f"[{idx}] requires marker, phase_name, and deployment"
                )
            actions.append(
                ControllerPostJobAction(
                    marker=marker,
                    phase_name=phase_name,
                    deployment=deployment,
                    timeout_seconds=int(
                        item.get("timeout_seconds") or _DEFAULT_POST_JOB_ACTION_TIMEOUT_SECONDS,
                    ),
                    restart_if_exists=bool(item.get("restart_if_exists", True)),
                )
            )
        return actions

    def resolve_call_handler_specs(self) -> dict[str, str]:
        out: dict[str, str] = {}

        # 1. Load from per-service YAML plugin.call_handlers.
        try:
            import yaml

            svc_dir = _find_services_dir()
            if svc_dir:
                for yaml_file in sorted(svc_dir.glob("*.yaml")):
                    if yaml_file.name.startswith("_"):
                        continue
                    try:
                        data = yaml.safe_load(yaml_file.read_text(encoding="utf-8")) or {}
                        call_handlers = (data.get("plugin") or {}).get("call_handlers")
                        if isinstance(call_handlers, dict):
                            for key, spec in call_handlers.items():
                                k = str(key or "").strip()
                                s = str(spec or "").strip()
                                if k and s and ":" in s:
                                    out[k] = s
                    except (yaml.YAMLError, OSError, UnicodeDecodeError) as exc:
                        log_swallowed(exc)
        except (ImportError, OSError) as exc:
            log_swallowed(exc)

        # 2. Fill gaps from config.json (backward compat).
        hooks = self.bootstrap_job_hooks()
        raw_map = hooks.get("call_handlers")
        if isinstance(raw_map, dict):
            for key, spec in raw_map.items():
                handler_key = str(key or "").strip()
                hook_spec = str(spec or "").strip()
                if handler_key and hook_spec and handler_key not in out:
                    if ":" not in hook_spec:
                        raise ConfigError(
                            "adapter_hooks.bootstrap_job.call_handlers"
                            f".{handler_key} must be module.path:Symbol"
                        )
                    out[handler_key] = hook_spec
        return out

    def runtime_config_policy_handler_spec(self) -> str:
        hooks = self.bootstrap_job_hooks()
        spec = str(hooks.get("runtime_config_policy_handler") or "").strip()
        if spec and ":" not in spec:
            raise ConfigError(
                "adapter_hooks.bootstrap_job.runtime_config_policy_handler "
                "must be module.path:Symbol"
            )
        return spec


__all__ = ["BootstrapJobConfigResolver"]
