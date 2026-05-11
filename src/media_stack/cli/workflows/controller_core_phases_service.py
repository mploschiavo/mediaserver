"""ControllerCorePhasesService — bootstrap_job pipeline dispatch loop.

ADR-0015 Phase 7d. Pre-Phase-7d this class held its own copies of
``_render_template_value`` / ``_phase_script`` / ``_skip_phase``
+ inline closures for component resolution, phase context,
``_phase_enabled``, and args/env rendering — all duplicated
verbatim with the legacy ``ControllerAllRunner`` god class.

Phase 7d collapses those onto the shared sub-package
:mod:`cli.workflows.controller_phase_planning` so both pipelines
share one source of truth for the planning helpers.

:class:`PhaseNameFormatter` stays here because the module-level
``format_phase_name`` alias is imported by name from external
callers; the formatter is now also reachable through
:class:`ControllerTemplateRenderer.format_phase_name`.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from media_stack.cli.workflows.controller_phase_planning import (
    ArgsEnvResolver,
    ComponentTechnologyResolver,
    ControllerPlanLoader,
    ControllerTemplateRenderer,
    PhaseContextBuilder,
    PhaseEnabledPredicate,
)
from media_stack.core.exceptions import ConfigError
from media_stack.services.controller_component_resolver import (
    ControllerComponentPlan,
    ControllerPhasePlanStep,
    resolve_pipeline_components,
    resolve_pipeline_phase_plan,
)


@dataclass(frozen=True)
class ControllerCorePhasesConfig:
    config_file: Path
    namespace: str
    prepare_host_root: str
    phase_skip_flags: dict[str, bool]


class PhaseNameFormatter:
    """Render ``{component_key}``/``{component}``/``{component|unbound}``
    tokens in a phase-name template. Shared helper so the two callers
    (``ControllerCorePhasesService`` and ``ControllerAllPipeline``) can't
    drift on the substitution rules."""

    def format_phase_name(
        self,
        template: str,
        *,
        component_key: str = "",
        component_technology: str = "",
    ) -> str:
        raw = str(template or "").strip()
        if not raw:
            return ""
        out = raw.replace("{component_key}", component_key)
        out = out.replace("{component|unbound}", component_technology or "unbound")
        out = out.replace("{component}", component_technology)
        return out


_PHASE_NAME_FORMATTER = PhaseNameFormatter()
# Public module-level alias bound to the singleton's instance method,
# preserving the import API for external callers
# (controller_phase_planning.template_renderer + the shared module
# imports this by name).
format_phase_name = _PHASE_NAME_FORMATTER.format_phase_name


class ControllerCorePhasesService:
    """Composition Root + Template Method for the bootstrap_job pipeline."""

    def __init__(self, cfg: ControllerCorePhasesConfig) -> None:
        self.cfg = cfg
        self._plan_loader = ControllerPlanLoader(
            cfg.config_file, phase_skip_flags=cfg.phase_skip_flags,
        )
        # Eagerly resolve to preserve the pre-Phase-7d behaviour where
        # ``ControllerCorePhasesService.__init__`` would surface a
        # malformed config file as a constructor-time error.
        self.plan: ControllerComponentPlan = self._plan_loader.plan()
        self._renderer = ControllerTemplateRenderer(
            namespace=cfg.namespace,
            prepare_host_root=cfg.prepare_host_root,
            config_file=cfg.config_file,
        )
        self._component_resolver = ComponentTechnologyResolver()
        self._args_env_resolver = ArgsEnvResolver(self._renderer)

    # -- backward-compat shims (kept for in-tree callers) ---------------

    def _phase_script(self, phase_key: str, technology: str) -> str:
        return self._plan_loader.phase_script(phase_key, technology)

    def _skip_phase(self, flag_key: str) -> bool:
        return self._plan_loader.skip_phase(flag_key)

    def _render_template_value(
        self,
        value: object,
        *,
        component_key: str = "",
        component_technology: str = "",
    ) -> str:
        return self._renderer.render(
            value,
            component_key=component_key,
            component_technology=component_technology,
        )

    def _format_phase_name(
        self,
        template: str,
        *,
        component_key: str = "",
        component_technology: str = "",
    ) -> str:
        return _PHASE_NAME_FORMATTER.format_phase_name(
            template,
            component_key=component_key,
            component_technology=component_technology,
        )

    # -- run dispatch loop ----------------------------------------------

    def run(
        self,
        *,
        run_phase: Callable[..., None],
        run_script: Callable[..., None],
        operation_handlers: dict[str, Callable[[], None]],
    ) -> None:
        phase_plan = resolve_pipeline_phase_plan(
            self.plan.config, pipeline="bootstrap_job",
        )
        configured_phase_keys = self._configured_phase_keys()

        components: dict[str, str] = resolve_pipeline_components(
            self.plan.config,
            pipeline="bootstrap_job",
            aliases=self.plan.aliases,
            role_bindings=self.plan.role_bindings,
        )
        self._seed_components_from_plan(phase_plan, components)

        context_builder = PhaseContextBuilder(self._plan_loader)
        component_context = context_builder.component_context(
            components, configured_phase_keys, self.plan,
        )
        phase_context = context_builder.phase_context(self.plan, component_context)
        phase_enabled = PhaseEnabledPredicate(self._plan_loader, phase_context)

        for step in phase_plan:
            self._dispatch_step(
                step, components, phase_enabled, run_phase, run_script,
                operation_handlers,
            )

    def _configured_phase_keys(self) -> tuple[str, ...]:
        adapter_hooks = self.plan.config.get("adapter_hooks")
        runner_phase_scripts = (
            (adapter_hooks or {}).get("runner_phase_scripts")
            if isinstance(adapter_hooks, dict)
            else {}
        )
        if isinstance(runner_phase_scripts, dict):
            return tuple(str(key) for key in runner_phase_scripts.keys())
        return ()

    def _seed_components_from_plan(
        self, phase_plan, components: dict[str, str],
    ) -> None:
        for step in phase_plan:
            if step.operation != "run_component_script":
                continue
            key, technology = self._component_resolver.resolve(
                step, self.plan, components,
            )
            if key and key not in components:
                components[key] = technology

    def _dispatch_step(
        self,
        step: ControllerPhasePlanStep,
        components: dict[str, str],
        phase_enabled: PhaseEnabledPredicate,
        run_phase: Callable[..., None],
        run_script: Callable[..., None],
        operation_handlers: dict[str, Callable[[], None]],
    ) -> None:
        operation = step.operation
        if operation == "run_component_script":
            self._dispatch_run_component_script(
                step, components, phase_enabled, run_phase, run_script,
            )
            return
        if operation == "call_handler":
            self._dispatch_call_handler(
                step, components, phase_enabled, run_phase, operation_handlers,
            )
            return
        raise ValueError(
            "Unknown bootstrap-job phase operation "
            f"'{operation}' in adapter_hooks.bootstrap_job.phase_plan."
        )

    def _dispatch_run_component_script(
        self,
        step: ControllerPhasePlanStep,
        components: dict[str, str],
        phase_enabled: PhaseEnabledPredicate,
        run_phase: Callable[..., None],
        run_script: Callable[..., None],
    ) -> None:
        params = dict(step.params or {})
        component_key, component_technology = self._component_resolver.resolve(
            step, self.plan, components,
        )
        enabled = phase_enabled.is_enabled(step)
        script_name = self._validate_run_component_script(
            step, params, component_key, component_technology, enabled,
        )
        env = self._args_env_resolver.resolve_env(
            params.get("env"),
            component_key=component_key,
            component_technology=component_technology,
        )
        args = self._args_env_resolver.resolve_args(
            params.get("args"),
            component_key=component_key,
            component_technology=component_technology,
        )
        phase_name = _PHASE_NAME_FORMATTER.format_phase_name(
            step.phase_name,
            component_key=component_key,
            component_technology=component_technology,
        )
        if not phase_name:
            raise ConfigError(
                "bootstrap_job phase operation 'run_component_script' requires "
                "non-empty phase_name."
            )
        run_phase(
            phase_name,
            lambda script=script_name, script_args=tuple(args), script_env=dict(env): run_script(
                script, *script_args, env=script_env,
            ),
            enabled=enabled,
        )

    def _validate_run_component_script(
        self,
        step: ControllerPhasePlanStep,
        params: dict,
        component_key: str,
        component_technology: str,
        enabled: bool,
    ) -> str:
        """Validate the run_component_script step + return the resolved script."""
        if enabled and not component_key:
            raise ConfigError(
                "bootstrap_job phase operation 'run_component_script' requires "
                "params.component, params.binding, or params.technology."
            )
        if enabled and not component_technology:
            raise ConfigError(
                "bootstrap_job phase operation 'run_component_script' could not resolve "
                f"technology for component '{component_key}'. Check "
                "adapter_hooks.bootstrap_job.components and technology_bindings."
            )
        script_phase = str(params.get("script_phase") or "").strip()
        if not script_phase:
            raise ConfigError(
                "bootstrap_job phase operation 'run_component_script' requires "
                "params.script_phase."
            )
        script_name = self._plan_loader.phase_script(script_phase, component_technology)
        if enabled and not script_name:
            raise ConfigError(
                "bootstrap_job phase operation 'run_component_script' could not resolve "
                f"script for component '{component_key or component_technology}' "
                f"(technology='{component_technology or 'unbound'}', "
                f"script_phase='{script_phase}'). "
                "Declare adapter_hooks.runner_phase_scripts.<script_phase> mapping "
                "for the bound technology."
            )
        return script_name

    def _dispatch_call_handler(
        self,
        step: ControllerPhasePlanStep,
        components: dict[str, str],
        phase_enabled: PhaseEnabledPredicate,
        run_phase: Callable[..., None],
        operation_handlers: dict[str, Callable[[], None]],
    ) -> None:
        params = dict(step.params or {})
        handler_key = str(params.get("handler") or "").strip()
        if not handler_key:
            raise ConfigError(
                "bootstrap_job phase operation 'call_handler' requires params.handler."
            )
        handler = operation_handlers.get(handler_key)
        if not callable(handler):
            raise ConfigError(
                "bootstrap_job phase operation 'call_handler' references unknown handler "
                f"'{handler_key}'."
            )
        component_key, component_technology = self._component_resolver.resolve(
            step, self.plan, components,
        )
        phase_name = _PHASE_NAME_FORMATTER.format_phase_name(
            step.phase_name,
            component_key=component_key,
            component_technology=component_technology,
        )
        if not phase_name:
            raise ConfigError(
                "bootstrap_job phase operation 'call_handler' requires "
                "non-empty phase_name."
            )
        run_phase(phase_name, handler, enabled=phase_enabled.is_enabled(step))


__all__ = [
    "ControllerCorePhasesConfig",
    "ControllerCorePhasesService",
    "PhaseNameFormatter",
    "format_phase_name",
]
