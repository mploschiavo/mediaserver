"""ControllerAllPipeline — Composition Root + Template Method.

ADR-0015 Phase 7d. Pre-Phase-7d ``ControllerAllRunner`` was a
~480-LoC god class in ``cli/commands/controller_all_main.py``
combining: plan loading, template rendering, manifest overrides,
component deployment, four step executors, the dispatch loop,
and the script-runner.

Phase 7d applies the Phase 4/7c template:

* Shared planning helpers from
  :mod:`cli.workflows.controller_phase_planning` (used by both
  this pipeline AND :class:`ControllerCorePhasesService`).
* Controller-all-specific Strategy + Command-set classes:
  :class:`ComponentDeployer` (manifest apply + rollout) and
  :class:`ControllerAllStepExecutors` (4 phase-plan actions).
* This Composition Root wires the graph + owns the dispatch
  loop + manages the checkpoint store for ``--resume``.
"""

from __future__ import annotations

import os
import shlex
import subprocess
import sys
from typing import Callable

from media_stack.cli.workflows.controller_all_orchestration.component_deployer import (
    ComponentDeployer,
)
from media_stack.cli.workflows.controller_all_orchestration.models import (
    ControllerAllConfig,
)
from media_stack.cli.workflows.controller_all_orchestration.step_executors import (
    ControllerAllStepExecutors,
)
from media_stack.cli.workflows.controller_phase_planning import (
    ArgsEnvResolver,
    ComponentTechnologyResolver,
    ControllerPlanLoader,
    ControllerTemplateRenderer,
    PhaseContextBuilder,
    PhaseEnabledPredicate,
)
from media_stack.core.cli_common import PhaseTracker, info
from media_stack.core.exceptions import ConfigError
from media_stack.core.platforms.kubernetes.kube_client import KubernetesClient
from media_stack.core.state_store import CheckpointStateStore
from media_stack.services.controller_component_resolver import (
    ControllerPhasePlanStep,
    resolve_pipeline_components,
    resolve_pipeline_phase_plan,
)


class ControllerAllPipeline:
    """Composition Root + Template Method for the bootstrap_all pipeline."""

    def __init__(self, cfg: ControllerAllConfig) -> None:
        self.cfg = cfg
        self.kube = KubernetesClient.from_environment()
        self.tracker = PhaseTracker()
        self.state = CheckpointStateStore(cfg.state_file)
        self.state.load()

        # Shared planning helpers (also used by ControllerCorePhasesService).
        self._plan_loader = ControllerPlanLoader(
            cfg.config_file, phase_skip_flags=cfg.phase_skip_flags,
        )
        self._renderer = ControllerTemplateRenderer(
            namespace=cfg.namespace,
            prepare_host_root=cfg.prepare_host_root,
            config_file=cfg.config_file,
            extra_tokens={"$secret_name": cfg.secret_name},
        )
        self._component_resolver = ComponentTechnologyResolver()
        self._args_env_resolver = ArgsEnvResolver(self._renderer)

        # Controller-all-specific collaborators.
        self._deployer = ComponentDeployer(cfg, self.kube, self._plan_loader_view())
        self._step_executors = ControllerAllStepExecutors(
            cfg=cfg,
            kube=self.kube,
            plan_loader=self._plan_loader,
            renderer=self._renderer,
            args_env_resolver=self._args_env_resolver,
            deployer=self._deployer,
            run_phase=self._run_phase,
            run_script=self._run_script,
        )

    def _plan_loader_view(self) -> ControllerPlanLoader:
        """Return the shared loader.

        Wrapping the field access in a method lets a future test fixture
        swap the loader without re-wiring the deployer.
        """
        return self._plan_loader

    # -- orchestration entry point -----------------------------------------

    def run(self) -> int:
        if not self.cfg.config_file.exists():
            raise ConfigError(f"Config file not found: {self.cfg.config_file}")

        plan = self._plan_loader.plan()
        phase_plan = resolve_pipeline_phase_plan(plan.config, pipeline="bootstrap_all")
        configured_phase_keys = self._configured_phase_keys(plan.config)
        components: dict[str, str] = resolve_pipeline_components(
            plan.config,
            pipeline="bootstrap_all",
            aliases=plan.aliases,
            role_bindings=plan.role_bindings,
        )

        def resolve_tech(step: ControllerPhasePlanStep) -> tuple[str, str]:
            return self._component_resolver.resolve(step, plan, components)

        self._seed_components_from_plan(phase_plan, components, resolve_tech)

        context_builder = PhaseContextBuilder(
            self._plan_loader,
            extra_flags={"enable_components": self.cfg.enable_components},
        )
        component_context = context_builder.component_context(
            components, configured_phase_keys, plan,
        )
        phase_context = context_builder.phase_context(plan, component_context)
        phase_enabled = PhaseEnabledPredicate(self._plan_loader, phase_context)

        def phase_name_fn(default_name: str, step: ControllerPhasePlanStep) -> str:
            return step.phase_name or default_name

        action_handlers = self._build_action_handlers(
            plan=plan,
            components=components,
            phase_enabled=phase_enabled,
            resolve_tech=resolve_tech,
            phase_name_fn=phase_name_fn,
        )
        self._dispatch_phase_plan(phase_plan, action_handlers)

        info("Full bootstrap complete.")
        self.tracker.summary()
        return 0

    # -- helpers ---------------------------------------------------------

    def _configured_phase_keys(self, config: dict) -> tuple[str, ...]:
        adapter_hooks = config.get("adapter_hooks")
        runner_phase_scripts = (
            (adapter_hooks or {}).get("runner_phase_scripts")
            if isinstance(adapter_hooks, dict)
            else {}
        )
        if isinstance(runner_phase_scripts, dict):
            return tuple(str(key) for key in runner_phase_scripts.keys())
        return ()

    def _seed_components_from_plan(
        self,
        phase_plan,
        components: dict[str, str],
        resolve_tech: Callable[[ControllerPhasePlanStep], tuple[str, str]],
    ) -> None:
        for step in phase_plan:
            params = dict(step.params or {})
            operation = str(step.operation or "").strip()
            action = str(params.get("action") or "").strip().lower()
            if operation != "run" or action != "component_script":
                continue
            key, technology = resolve_tech(step)
            if key and key not in components:
                components[key] = technology

    def _build_action_handlers(
        self,
        *,
        plan,
        components: dict[str, str],
        phase_enabled: PhaseEnabledPredicate,
        resolve_tech: Callable[[ControllerPhasePlanStep], tuple[str, str]],
        phase_name_fn: Callable[[str, ControllerPhasePlanStep], str],
    ) -> dict[str, Callable[[ControllerPhasePlanStep], None]]:
        return {
            "component_script": lambda step: self._step_executors.execute_component_script(
                step, plan, components, phase_enabled, resolve_tech, phase_name_fn,
            ),
            "script": lambda step: self._step_executors.execute_script(
                step, phase_enabled, phase_name_fn,
            ),
            "enable_components": lambda step: self._step_executors.execute_enable_components(
                step, plan, phase_enabled,
            ),
            "http_action": lambda step: self._step_executors.execute_http_action(
                step, phase_enabled,
            ),
        }

    def _dispatch_phase_plan(
        self,
        phase_plan,
        action_handlers: dict[str, Callable[[ControllerPhasePlanStep], None]],
    ) -> None:
        for step in phase_plan:
            action = self._resolve_step_action(step)
            handler = action_handlers.get(action)
            if handler is None:
                raise ConfigError(
                    "Unknown bootstrap-all run action "
                    f"'{action}' in adapter_hooks.bootstrap_all.phase_plan params.action."
                )
            handler(step)

    def _resolve_step_action(self, step: ControllerPhasePlanStep) -> str:
        operation = str(step.operation or "").strip()
        if operation != "run":
            raise ConfigError(
                "Unknown bootstrap-all phase operation "
                f"'{operation}' in adapter_hooks.bootstrap_all.phase_plan. "
                "Supported operation: 'run'."
            )
        params = dict(step.params or {})
        action = str(params.get("action") or "").strip().lower()
        if not action:
            raise ConfigError(
                "bootstrap_all phase operation 'run' requires params.action "
                "in adapter_hooks.bootstrap_all.phase_plan."
            )
        return action

    def _run_phase(
        self,
        name: str,
        action: Callable[[], None],
        *,
        enabled: bool = True,
    ) -> None:
        self.tracker.start(name)
        if not enabled:
            self.tracker.end("skipped")
            self.state.mark_phase(name, "skipped")
            return
        if self.cfg.resume and self.state.is_phase_done(name):
            info(f"Resume checkpoint: skipping already-completed phase '{name}'.")
            self.tracker.end("skipped")
            return
        try:
            action()
            self.tracker.end("ok")
            self.state.mark_phase(name, "ok")
        except Exception as exc:
            self.tracker.end("failed")
            self.state.mark_phase(name, "failed", error=str(exc))
            raise

    def _run_script(
        self, script_name: str, *args: str, env: dict[str, str] | None = None,
    ) -> None:
        call_env = dict(os.environ)
        if env:
            call_env.update({k: str(v) for k, v in env.items()})

        if "." in script_name and not script_name.endswith(".sh"):
            cmd = [sys.executable, "-m", script_name, *list(args)]
            label = script_name
        else:
            from media_stack.cli.workflows.script_runner_service import _find_script
            script_path = _find_script(self.cfg.root_dir / "bin", script_name)
            cmd = ["bash", str(script_path), *list(args)]
            label = script_name

        proc = subprocess.run(
            cmd,
            cwd=str(self.cfg.root_dir),
            env=call_env,
            check=False,
            text=True,
            capture_output=True,
        )
        if proc.stdout.strip():
            print(proc.stdout.rstrip())
        if proc.stderr.strip():
            print(proc.stderr.rstrip(), file=sys.stderr)
        if proc.returncode != 0:
            raise RuntimeError(
                f"{label} failed ({proc.returncode}): "
                f"{' '.join(shlex.quote(x) for x in cmd)}"
            )

    # -- test-surface compatibility shims --------------------------------
    # Pre-Phase-7d ControllerAllRunner exposed these as public/private
    # methods. Tests + in-tree callers may address them; the shims
    # delegate into the new SRP collaborators.

    def _manifest_overrides(self, text: str) -> str:
        return self._deployer.manifest_overrides(text)

    def _apply_manifest_file(self, manifest_path, *, component: str) -> None:
        self._deployer.apply_manifest_file(manifest_path, component=component)

    def _enable_component_deployment(self, component: str) -> None:
        self._deployer.enable_component_deployment(component)

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
        return self._renderer.format_phase_name(
            template,
            component_key=component_key,
            component_technology=component_technology,
        )


__all__ = ["ControllerAllPipeline"]
