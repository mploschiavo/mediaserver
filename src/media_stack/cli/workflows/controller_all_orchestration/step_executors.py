"""ControllerAllStepExecutors — Command set for the four phase-plan actions.

ADR-0015 Phase 7d. Pre-Phase-7d the four ``execute_*`` methods
lived on ``ControllerAllCommand`` in commands/ and took a
``runner`` reference plus a half-dozen closures as parameters
(a smell that pointed at the underlying god-class shape).

Phase 7d collapses the closure parameters onto constructor-
injected collaborators from
:mod:`cli.workflows.controller_phase_planning` (shared with the
bootstrap_job pipeline) + a few controller_all-specific
collaborators. Each executor is a pure Command: given a phase
step + the per-step ``phase_enabled`` predicate, do one thing.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable

from media_stack.core.cli_common import info, warn
from media_stack.core.exceptions import ConfigError
from media_stack.services.controller_component_resolver import (
    ControllerComponentPlan,
    ControllerPhasePlanStep,
    resolve_bootstrap_enable_components,
)

if TYPE_CHECKING:
    from media_stack.cli.workflows.controller_all_orchestration.component_deployer import (
        ComponentDeployer,
    )
    from media_stack.cli.workflows.controller_all_orchestration.models import (
        ControllerAllConfig,
    )
    from media_stack.cli.workflows.controller_phase_planning import (
        ArgsEnvResolver,
        ControllerPlanLoader,
        ControllerTemplateRenderer,
        PhaseEnabledPredicate,
    )
    from media_stack.core.platforms.kubernetes.kube_client import KubernetesClient


_DEFAULT_HTTP_SERVICE_PORT = 9100
_DEFAULT_HTTP_TIMEOUT_SECONDS = 600
_DEFAULT_HTTP_HEARTBEAT_SECONDS = 15
_DEFAULT_HTTP_TIMEOUT_RAW = "10m"


class ControllerAllStepExecutors:
    """Command set: 4 phase-plan action handlers (run-action variants)."""

    def __init__(
        self,
        cfg: "ControllerAllConfig",
        kube: "KubernetesClient",
        plan_loader: "ControllerPlanLoader",
        renderer: "ControllerTemplateRenderer",
        args_env_resolver: "ArgsEnvResolver",
        deployer: "ComponentDeployer",
        run_phase: Callable[..., None],
        run_script: Callable[..., None],
    ) -> None:
        self._cfg = cfg
        self._kube = kube
        self._plan_loader = plan_loader
        self._renderer = renderer
        self._args_env_resolver = args_env_resolver
        self._deployer = deployer
        self._run_phase = run_phase
        self._run_script = run_script

    def execute_component_script(
        self,
        step: ControllerPhasePlanStep,
        plan: ControllerComponentPlan,
        components: dict[str, str],
        phase_enabled: "PhaseEnabledPredicate",
        resolve_tech: Callable[[ControllerPhasePlanStep], tuple[str, str]],
        phase_name_fn: Callable[[str, ControllerPhasePlanStep], str],
    ) -> None:
        params = dict(step.params or {})
        component_key, component_technology = resolve_tech(step)
        enabled = phase_enabled.is_enabled(step)
        script_name = self._validate_component_script_step(
            params, component_key, component_technology, enabled,
        )
        args = self._args_env_resolver.resolve_args(
            params.get("args"),
            component_key=component_key,
            component_technology=component_technology,
        )
        env = self._args_env_resolver.resolve_env(
            params.get("env"),
            component_key=component_key,
            component_technology=component_technology,
        )
        name = self._renderer.format_phase_name(
            phase_name_fn("", step),
            component_key=component_key,
            component_technology=component_technology,
        )
        if not name:
            raise ConfigError(
                "bootstrap_all run action 'component_script' requires non-empty phase_name."
            )
        self._run_phase(
            name,
            lambda s=script_name, a=tuple(args), e=dict(env): self._run_script(s, *a, env=e),
            enabled=enabled,
        )

    def _validate_component_script_step(
        self,
        params: dict,
        component_key: str,
        component_technology: str,
        enabled: bool,
    ) -> str:
        if enabled and not component_key:
            raise ConfigError(
                "bootstrap_all run action 'component_script' requires "
                "params.component, params.binding, or params.technology."
            )
        if enabled and not component_technology:
            raise ConfigError(
                "bootstrap_all run action 'component_script' could not resolve "
                f"technology for component '{component_key}'."
            )
        script_phase = str(params.get("script_phase") or "").strip()
        if not script_phase:
            raise ConfigError(
                "bootstrap_all run action 'component_script' requires params.script_phase."
            )
        script_name = self._plan_loader.phase_script(script_phase, component_technology)
        if enabled and not script_name:
            raise ConfigError(
                "bootstrap_all run action 'component_script' could not resolve "
                f"script for component '{component_key or component_technology}'."
            )
        return script_name

    def execute_script(
        self,
        step: ControllerPhasePlanStep,
        phase_enabled: "PhaseEnabledPredicate",
        phase_name_fn: Callable[[str, ControllerPhasePlanStep], str],
    ) -> None:
        params = dict(step.params or {})
        script_name = self._renderer.render(params.get("script", ""))
        if not script_name:
            raise ConfigError("bootstrap_all run action 'script' requires params.script.")
        enabled = phase_enabled.is_enabled(step)
        args = self._args_env_resolver.resolve_args(params.get("args"))
        env = self._args_env_resolver.resolve_env(params.get("env"))
        name = self._renderer.format_phase_name(phase_name_fn("", step))
        if not name:
            raise ConfigError(
                "bootstrap_all run action 'script' requires non-empty phase_name."
            )
        self._run_phase(
            name,
            lambda s=script_name, a=tuple(args), e=dict(env): self._run_script(s, *a, env=e),
            enabled=enabled,
        )

    def execute_enable_components(
        self,
        step: ControllerPhasePlanStep,
        plan: ControllerComponentPlan,
        phase_enabled: "PhaseEnabledPredicate",
    ) -> None:
        if not phase_enabled.is_enabled(step):
            return
        components_to_enable = resolve_bootstrap_enable_components(
            plan.config, aliases=plan.aliases,
        )
        if not components_to_enable:
            raise ConfigError(
                "bootstrap_all run action 'enable_components' requires a non-empty "
                "enable_components list."
            )
        for component in components_to_enable:
            sync_script = self._plan_loader.phase_script("component_key_sync", component)
            if not sync_script:
                raise ConfigError(
                    "Could not resolve runner_phase_scripts.component_key_sync "
                    f"for component '{component}'."
                )
            self._run_phase(
                f"Sync component integration keys ({component})",
                lambda s=sync_script: self._run_script(
                    s, env={"NAMESPACE": self._cfg.namespace},
                ),
                enabled=True,
            )
            self._run_phase(
                f"Enable component deployment ({component})",
                lambda app=component: self._deployer.enable_component_deployment(app),
                enabled=True,
            )

    def execute_http_action(
        self,
        step: ControllerPhasePlanStep,
        phase_enabled: "PhaseEnabledPredicate",
    ) -> None:
        if not phase_enabled.is_enabled(step):
            return
        params = step.params or {}
        action_name = str(params.get("action_name", "")).strip()
        if not action_name:
            raise ConfigError("http_action requires params.action_name")
        svc_port = int(params.get("service_port", _DEFAULT_HTTP_SERVICE_PORT))
        namespace = self._renderer.render(
            str(params.get("namespace_var", "$namespace")), component_key="",
        )
        wait_svc = self._build_http_wait_service(namespace, svc_port)
        pod_name = wait_svc._find_bootstrap_pod()
        if not pod_name:
            raise ConfigError("Bootstrap service pod not found for http_action")
        info(f"Triggering action '{action_name}' on bootstrap service")
        self._trigger_http_action(namespace, pod_name, svc_port, action_name)
        info(f"Action '{action_name}' accepted, waiting for completion...")
        wait_svc.wait_for_bootstrap_service(wait_for_action=action_name)

    def _build_http_wait_service(self, namespace: str, svc_port: int):
        from media_stack.cli.workflows.controller_job_wait_service import (
            ControllerJobWaitConfig,
            ControllerJobWaitService,
        )
        return ControllerJobWaitService(
            cfg=ControllerJobWaitConfig(
                namespace=namespace,
                timeout_seconds=_DEFAULT_HTTP_TIMEOUT_SECONDS,
                timeout_raw=_DEFAULT_HTTP_TIMEOUT_RAW,
                heartbeat_interval=_DEFAULT_HTTP_HEARTBEAT_SECONDS,
                service_port=svc_port,
            ),
            kube=self._kube,
            info=info,
            warn=warn,
        )

    def _trigger_http_action(
        self, namespace: str, pod_name: str, svc_port: int, action_name: str,
    ) -> None:
        trigger_result = self._kube.run(
            ["-n", namespace, "exec", pod_name, "--", "python3", "-c",
             "import urllib.request,json; "
             f"req=urllib.request.Request('http://127.0.0.1:{svc_port}/actions/{action_name}',"
             "data=b'{}',headers={'Content-Type':'application/json'}); "
             "r=urllib.request.urlopen(req); print(r.read().decode())"],
            check=False,
        )
        if trigger_result.returncode != 0:
            raise ConfigError(
                f"Failed to trigger action '{action_name}': "
                f"{trigger_result.stderr or trigger_result.stdout}"
            )


__all__ = ["ControllerAllStepExecutors"]
