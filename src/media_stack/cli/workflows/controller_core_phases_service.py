from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from media_stack.core.exceptions import ConfigError

from media_stack.cli.workflows.controller_component_resolver import (
    ControllerComponentPlan,
    ControllerPhasePlanStep,
    evaluate_phase_condition,
    normalize_flag_token,
    resolve_bootstrap_component_plan,
    resolve_pipeline_components,
    resolve_pipeline_phase_plan,
    resolve_runner_phase_script,
)


@dataclass(frozen=True)
class ControllerCorePhasesConfig:
    config_file: Path
    namespace: str
    prepare_host_root: str
    phase_skip_flags: dict[str, bool]


class ControllerCorePhasesService:
    def __init__(self, cfg: ControllerCorePhasesConfig) -> None:
        self.cfg = cfg
        self.plan: ControllerComponentPlan = resolve_bootstrap_component_plan(self.cfg.config_file)

    def _phase_script(self, phase_key: str, technology: str) -> str:
        return resolve_runner_phase_script(
            self.plan.config,
            phase_key=phase_key,
            technology=technology,
            aliases=self.plan.aliases,
        )

    def _skip_phase(self, flag_key: str) -> bool:
        token = normalize_flag_token(flag_key)
        if not token:
            return False
        return bool(self.cfg.phase_skip_flags.get(token, False))

    def _render_template_value(
        self,
        value: object,
        *,
        component_key: str = "",
        component_technology: str = "",
    ) -> str:
        text = str(value or "")
        tokens = {
            "$namespace": self.cfg.namespace,
            "$prepare_host_root": self.cfg.prepare_host_root,
            "$config_file": str(self.cfg.config_file),
            "$component_key": component_key,
            "$component": component_technology,
        }
        for token, token_value in tokens.items():
            text = text.replace(token, str(token_value))
        return text

    def _format_phase_name(
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

    def run(
        self,
        *,
        run_phase: Callable[..., None],
        run_script: Callable[..., None],
        operation_handlers: dict[str, Callable[[], None]],
    ) -> None:
        phase_plan = resolve_pipeline_phase_plan(
            self.plan.config,
            pipeline="bootstrap_job",
        )
        adapter_hooks = self.plan.config.get("adapter_hooks")
        runner_phase_scripts = (
            (adapter_hooks or {}).get("runner_phase_scripts")
            if isinstance(adapter_hooks, dict)
            else {}
        )
        configured_phase_keys = (
            tuple(str(key) for key in runner_phase_scripts.keys())
            if isinstance(runner_phase_scripts, dict)
            else ()
        )

        components: dict[str, str] = resolve_pipeline_components(
            self.plan.config,
            pipeline="bootstrap_job",
            aliases=self.plan.aliases,
            role_bindings=self.plan.role_bindings,
        )

        def _resolve_component_technology(step: ControllerPhasePlanStep) -> tuple[str, str]:
            params = dict(step.params or {})
            component_key = str(params.get("component") or "").strip()
            if component_key:
                token = str(components.get(component_key) or "").strip()
                if token:
                    return component_key, token

            binding_key = str(params.get("binding") or "").strip()
            if binding_key:
                token = str(self.plan.role_bindings.get(binding_key) or "").strip()
                if token:
                    return component_key or binding_key, token

            technology = str(params.get("technology") or "").strip()
            if technology:
                return component_key or technology, technology

            return component_key, ""

        for step in phase_plan:
            if step.operation != "run_component_script":
                continue
            key, technology = _resolve_component_technology(step)
            if key and key not in components:
                components[key] = technology

        component_context: dict[str, dict[str, object]] = {}
        for component_key, technology in components.items():
            script_map: dict[str, str] = {}
            for phase_key in configured_phase_keys:
                script_map[phase_key] = self._phase_script(phase_key, technology)
            selected_client = self.plan.technology_settings.get(technology)
            component_context[component_key] = {
                "technology": str(technology or "").strip(),
                "scripts": script_map,
                "selected": dict(selected_client) if isinstance(selected_client, dict) else {},
            }

        phase_context: dict[str, object] = {
            "config": self.plan.config,
            "bindings": dict(self.plan.role_bindings),
            "components": component_context,
        }

        def _phase_enabled(step: ControllerPhasePlanStep) -> bool:
            enabled = bool(step.enabled) and evaluate_phase_condition(
                step.when, context=phase_context
            )
            if enabled and step.skip_flag and self._skip_phase(step.skip_flag):
                enabled = False
            return enabled

        for step in phase_plan:
            operation = step.operation

            if operation == "run_component_script":
                params = dict(step.params or {})
                component_key, component_technology = _resolve_component_technology(step)
                enabled = _phase_enabled(step)
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

                script_name = self._phase_script(script_phase, component_technology)
                if enabled and not script_name:
                    raise ConfigError(
                        "bootstrap_job phase operation 'run_component_script' could not resolve "
                        f"script for component '{component_key or component_technology}' "
                        f"(technology='{component_technology or 'unbound'}', "
                        f"script_phase='{script_phase}'). "
                        "Declare adapter_hooks.runner_phase_scripts.<script_phase> mapping "
                        "for the bound technology."
                    )

                env: dict[str, str] = {}
                raw_env = params.get("env")
                if raw_env is not None and not isinstance(raw_env, dict):
                    raise ConfigError(
                        "bootstrap_job phase operation 'run_component_script' params.env "
                        "must be an object/map when provided."
                    )
                if isinstance(raw_env, dict):
                    for key, value in raw_env.items():
                        env_key = str(key or "").strip()
                        if not env_key:
                            continue
                        env[env_key] = self._render_template_value(
                            value,
                            component_key=component_key,
                            component_technology=component_technology,
                        )

                args: list[str] = []
                raw_args = params.get("args")
                if raw_args is not None and not isinstance(raw_args, list):
                    raise ConfigError(
                        "bootstrap_job phase operation 'run_component_script' params.args "
                        "must be an array when provided."
                    )
                if isinstance(raw_args, list):
                    for value in raw_args:
                        args.append(
                            self._render_template_value(
                                value,
                                component_key=component_key,
                                component_technology=component_technology,
                            )
                        )

                phase_name = self._format_phase_name(
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
                    lambda script=script_name, script_args=tuple(args), script_env=dict(
                        env
                    ): run_script(
                        script,
                        *script_args,
                        env=script_env,
                    ),
                    enabled=enabled,
                )
                continue

            if operation == "call_handler":
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
                component_key, component_technology = _resolve_component_technology(step)
                phase_name = self._format_phase_name(
                    step.phase_name,
                    component_key=component_key,
                    component_technology=component_technology,
                )
                if not phase_name:
                    raise ConfigError(
                        "bootstrap_job phase operation 'call_handler' requires "
                        "non-empty phase_name."
                    )
                run_phase(
                    phase_name,
                    handler,
                    enabled=_phase_enabled(step),
                )
                continue

            raise ValueError(
                "Unknown bootstrap-job phase operation "
                f"'{operation}' in adapter_hooks.bootstrap_job.phase_plan."
            )
