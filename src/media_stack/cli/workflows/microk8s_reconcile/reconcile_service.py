"""Microk8sReconcileService — Composition Root + Template Method.

ADR-0015 Phase 7a. The pre-Phase-7a ``Microk8sReconcileRunner`` god
class (~165 LoC, 11 methods) lived in
``cli/commands/microk8s_reconcile_main.py`` and combined four
concerns:

* phase-handler dispatch loop,
* ``kubectl apply`` invocations,
* rollout / wait / status reporting,
* mutable state accumulation.

Phase 7a splits those onto :class:`ManifestApplier`,
:class:`RolloutCoordinator`, and this Composition Root which
wires them together + owns the dispatch loop. The dispatch loop
is the Template Method: every phase step routes through the
``handlers`` dict; concrete behaviour lives on the strategy
classes.
"""

from __future__ import annotations

from typing import Callable

from media_stack.cli.workflows.microk8s_reconcile.manifest_applier import (
    ManifestApplier,
)
from media_stack.cli.workflows.microk8s_reconcile.models import (
    Microk8sReconcileConfig,
    Microk8sReconcileState,
)
from media_stack.cli.workflows.microk8s_reconcile.rollout_coordinator import (
    RolloutCoordinator,
)
from media_stack.core.cli_common import kube_cmd, run_command
from media_stack.core.exceptions import ConfigError
from media_stack.core.subprocess_utils import CommandResult
from media_stack.services.controller_component_resolver import (
    evaluate_phase_condition,
)


class Microk8sReconcileService:
    """Composition Root + Template Method for the microk8s-reconcile workflow."""

    def __init__(
        self,
        cfg: Microk8sReconcileConfig,
        *,
        kubectl_command: tuple[str, ...] | None = None,
        command_runner: Callable[..., CommandResult] | None = None,
    ) -> None:
        self._cfg = cfg
        self._kubectl_command = tuple(kubectl_command or kube_cmd())
        self._command_runner = command_runner or run_command
        self._state = Microk8sReconcileState()
        self._manifest_applier = ManifestApplier(cfg, self._run_kubectl)
        self._rollout = RolloutCoordinator(
            cfg, self._state, self._run_kubectl, self._kubectl_command,
        )

    @property
    def state(self) -> Microk8sReconcileState:
        return self._state

    def _run_kubectl(
        self, args: list[str], *, check: bool = True,
    ) -> CommandResult:
        return self._command_runner(
            [*self._kubectl_command, *args], check=check,
        )

    def _condition_context(self) -> dict[str, object]:
        return {
            "flags": {
                "include_optional": self._cfg.include_optional,
            },
            "state": {
                "optional_deployments_present": self._state.optional_deployments_present,
                "rollout_failures": self._state.rollout_failures,
            },
            "config": {
                "namespace": self._cfg.namespace,
                "wait_timeout": self._cfg.wait_timeout,
            },
        }

    def _handlers(self) -> dict[str, Callable[[], None]]:
        return {
            "apply_base_kustomize": self._manifest_applier.apply_base_kustomize,
            "apply_optional_manifests": self._manifest_applier.apply_optional_manifests,
            "apply_conditional_manifests": self._manifest_applier.apply_conditional_manifests,
            "restart_all_deployments": self._rollout.restart_all_deployments,
            "wait_all_rollouts": self._rollout.wait_all_rollouts,
            "print_pod_state": self._rollout.print_pod_state,
            "fail_if_rollout_failed": self._rollout.fail_if_rollout_failed,
        }

    def run(self) -> int:
        self._state.optional_deployments_present = bool(
            self._rollout.list_optional_deployments()
        )
        handlers = self._handlers()

        for step in self._cfg.phase_plan:
            if not step.enabled:
                continue
            if not evaluate_phase_condition(step.when, context=self._condition_context()):
                continue
            action = handlers.get(step.handler)
            if not callable(action):
                raise ConfigError(
                    "adapter_hooks.microk8s_reconcile.phase_plan references unknown handler "
                    f"'{step.handler}'"
                )
            print(f"[INFO] [{step.event.value}] {step.phase_name}")
            action()

        print("\n[OK] Reconcile complete.")
        return 0


__all__ = ["Microk8sReconcileService"]
