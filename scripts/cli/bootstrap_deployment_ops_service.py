"""Deployment lifecycle helpers for bootstrap orchestration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from core.exceptions import KubernetesError
from core.platforms.kubernetes.kube_client import KubernetesClient

LogFn = Callable[[str], None]


@dataclass(frozen=True)
class BootstrapDeploymentOpsConfig:
    namespace: str


@dataclass
class BootstrapDeploymentOpsService:
    cfg: BootstrapDeploymentOpsConfig
    kube: KubernetesClient
    info: LogFn

    def deployment_exists(self, deployment: str) -> bool:
        result = self.kube.run(
            ["-n", self.cfg.namespace, "get", f"deploy/{deployment}"],
            check=False,
        )
        return result.returncode == 0

    def restart_deployment(self, deployment: str, *, timeout_seconds: int) -> None:
        self.info(f"Restarting deployment/{deployment}.")
        restart = self.kube.run(
            ["-n", self.cfg.namespace, "rollout", "restart", f"deployment/{deployment}"],
            check=False,
        )
        if restart.returncode != 0:
            raise KubernetesError(restart.stderr or restart.stdout)
        status = self.kube.run(
            [
                "-n",
                self.cfg.namespace,
                "rollout",
                "status",
                f"deployment/{deployment}",
                f"--timeout={timeout_seconds}s",
            ],
            check=False,
        )
        if status.returncode != 0:
            raise KubernetesError(status.stderr or status.stdout)

    def restart_deployment_if_exists(self, deployment: str, *, timeout_seconds: int) -> None:
        if not self.deployment_exists(deployment):
            self.info(
                f"deployment/{deployment} not found in namespace/{self.cfg.namespace}; "
                "skipping restart."
            )
            return
        self.restart_deployment(deployment, timeout_seconds=timeout_seconds)
