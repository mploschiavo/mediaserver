"""Deployment rollout waiting helpers for rebuild/bootstrap."""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from typing import Callable

InfoFn = Callable[[str], None]
WarnFn = Callable[[str], None]


@dataclass(frozen=True)
class RebuildDeploymentsWaitConfig:
    namespace: str
    wait_timeout: str
    kubectl: list[str]


@dataclass
class RebuildDeploymentsWaitService:
    cfg: RebuildDeploymentsWaitConfig
    info: InfoFn
    warn: WarnFn

    def wait_for_deployments(self) -> None:
        proc = subprocess.run(
            [
                *self.cfg.kubectl,
                "-n",
                self.cfg.namespace,
                "get",
                "deploy",
                "-o",
                "jsonpath={range .items[*]}{.metadata.name}{'\\n'}{end}",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode != 0:
            raise RuntimeError("Failed listing deployments.")

        deploys = [x.strip() for x in (proc.stdout or "").splitlines() if x.strip()]
        if not deploys:
            raise RuntimeError(f"No deployments found in namespace '{self.cfg.namespace}'.")

        failures = 0
        for deploy in deploys:
            replica_probe = subprocess.run(
                [
                    *self.cfg.kubectl,
                    "-n",
                    self.cfg.namespace,
                    "get",
                    "deploy",
                    deploy,
                    "-o",
                    "jsonpath={.spec.replicas}",
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            replicas = (replica_probe.stdout or "1").strip() or "1"
            if replicas == "0":
                self.info(f"Skipping rollout wait for deploy/{deploy} (replicas=0)")
                continue

            self.info(f"Waiting for deploy/{deploy} rollout")
            rollout = subprocess.run(
                [
                    *self.cfg.kubectl,
                    "-n",
                    self.cfg.namespace,
                    "rollout",
                    "status",
                    f"deploy/{deploy}",
                    f"--timeout={self.cfg.wait_timeout}",
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            if rollout.stdout.strip():
                print(rollout.stdout.rstrip())
            if rollout.stderr.strip():
                print(rollout.stderr.rstrip(), file=sys.stderr)
            if rollout.returncode != 0:
                self.warn(f"deploy/{deploy} not ready within {self.cfg.wait_timeout}")
                failures += 1

        if failures:
            subprocess.run(
                [*self.cfg.kubectl, "-n", self.cfg.namespace, "get", "pods", "-o", "wide"],
                check=False,
            )
            raise RuntimeError(f"{failures} deployment(s) failed readiness checks.")
