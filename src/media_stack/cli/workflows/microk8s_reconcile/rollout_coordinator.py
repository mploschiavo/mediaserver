"""RolloutCoordinator — Strategy for rollout + wait + status phase handlers.

ADR-0015 Phase 7a. Pre-Phase-7a these four handlers
(``_handle_restart_all_deployments``, ``_handle_wait_all_rollouts``,
``_handle_print_pod_state``, ``_handle_fail_if_rollout_failed``) sat
as methods on :class:`Microk8sReconcileRunner`. They share a
single responsibility — observing rollout state + escalating
failures — so they collapse to one Strategy class.

The mutable :class:`Microk8sReconcileState` is constructor-
injected because ``_handle_wait_all_rollouts`` accumulates the
rollout-failures count into it and ``_handle_fail_if_rollout_failed``
reads it back. The state object survives across handler calls
inside the same dispatch loop.
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING, Callable

from media_stack.core.exceptions import MediaStackError

if TYPE_CHECKING:
    from media_stack.cli.workflows.microk8s_reconcile.models import (
        Microk8sReconcileConfig,
        Microk8sReconcileState,
    )
    from media_stack.core.subprocess_utils import CommandResult


class RolloutCoordinator:
    """Strategy: restart deployments, wait for rollouts, escalate failures."""

    def __init__(
        self,
        cfg: "Microk8sReconcileConfig",
        state: "Microk8sReconcileState",
        kubectl_runner: Callable[..., "CommandResult"],
        kubectl_command: tuple[str, ...],
    ) -> None:
        self._cfg = cfg
        self._state = state
        self._kubectl = kubectl_runner
        self._kubectl_command = kubectl_command

    def list_optional_deployments(self) -> list[str]:
        proc = self._kubectl(
            ["-n", self._cfg.namespace, "get", "deploy", "-o", "name"],
            check=False,
        )
        if proc.returncode != 0:
            return []
        allowed = {
            str(name).strip()
            for name in self._cfg.optional_deployments
            if str(name).strip()
        }
        names: list[str] = []
        for row in (proc.stdout or "").splitlines():
            token = row.strip()
            if not token:
                continue
            short = token.removeprefix("deploy/")
            if short in allowed:
                names.append(short)
        return names

    def restart_all_deployments(self) -> None:
        print(f"[INFO] Restarting all deployments in namespace {self._cfg.namespace}")
        self._kubectl(["-n", self._cfg.namespace, "rollout", "restart", "deploy", "--all"])

    def wait_all_rollouts(self) -> None:
        deploy_proc = self._kubectl(
            [
                "-n",
                self._cfg.namespace,
                "get",
                "deploy",
                "-o",
                "jsonpath={range .items[*]}{.metadata.name}{'\\n'}{end}",
            ],
            check=False,
        )
        deploys = [
            line.strip()
            for line in (deploy_proc.stdout or "").splitlines()
            if line.strip()
        ]
        failed = 0
        for deploy in deploys:
            print(f"[INFO] Waiting for deploy/{deploy}")
            status_proc = self._kubectl(
                [
                    "-n",
                    self._cfg.namespace,
                    "rollout",
                    "status",
                    f"deploy/{deploy}",
                    f"--timeout={self._cfg.wait_timeout}",
                ],
                check=False,
            )
            sys.stdout.write(status_proc.stdout or "")
            sys.stderr.write(status_proc.stderr or "")
            if status_proc.returncode != 0:
                print(
                    f"[WARN] deploy/{deploy} did not become ready in {self._cfg.wait_timeout}",
                    file=sys.stderr,
                )
                failed += 1
        self._state.rollout_failures = failed

    def print_pod_state(self) -> None:
        print("\n[INFO] Current pod state:")
        pods_proc = self._kubectl(["-n", self._cfg.namespace, "get", "pods"], check=False)
        sys.stdout.write(pods_proc.stdout or "")
        sys.stderr.write(pods_proc.stderr or "")

    def fail_if_rollout_failed(self) -> None:
        if self._state.rollout_failures <= 0:
            return
        joined = " ".join(self._kubectl_command)
        print(
            f"\n[WARN] {self._state.rollout_failures} deployment(s) still not ready.",
            file=sys.stderr,
        )
        print("[WARN] Inspect with:", file=sys.stderr)
        print(
            f"  {joined} -n {self._cfg.namespace} get events --sort-by=.lastTimestamp | tail -n 200",
            file=sys.stderr,
        )
        print(
            f"  {joined} -n {self._cfg.namespace} logs deploy/<name> --tail=200",
            file=sys.stderr,
        )
        raise MediaStackError("One or more deployments did not become ready")


__all__ = ["RolloutCoordinator"]
