"""Deployment rollout waiting helpers for rebuild/bootstrap."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Callable

InfoFn = Callable[[str], None]
WarnFn = Callable[[str], None]
RunKubeFn = Callable[..., object]


@dataclass(frozen=True)
class RebuildDeploymentsWaitConfig:
    namespace: str
    wait_timeout: str


@dataclass
class RebuildDeploymentsWaitService:
    cfg: RebuildDeploymentsWaitConfig
    info: InfoFn
    warn: WarnFn
    run_kube: RunKubeFn

    def wait_for_deployments(self) -> None:
        proc = self.run_kube(["-n", self.cfg.namespace, "get", "deploy"], check=False)
        if proc.returncode != 0:
            raise RuntimeError("Failed listing deployments.")

        deploys = [x.strip() for x in (proc.stdout or "").splitlines() if x.strip()]
        if not deploys:
            raise RuntimeError(f"No deployments found in namespace '{self.cfg.namespace}'.")

        failures = 0
        for deploy in deploys:
            replica_probe = self.run_kube(
                [
                    "-n",
                    self.cfg.namespace,
                    "get",
                    "deploy",
                    deploy,
                    "-o",
                    "json",
                ],
                check=False,
            )
            replicas = "1"
            if replica_probe.returncode == 0 and replica_probe.stdout.strip():
                try:
                    import json

                    payload = json.loads(replica_probe.stdout)
                    replicas = str((payload.get("spec") or {}).get("replicas", 1))
                except Exception:
                    replicas = "1"
            if replicas == "0":
                self.info(f"Skipping rollout wait for deploy/{deploy} (replicas=0)")
                continue

            self.info(f"Waiting for deploy/{deploy} rollout")
            rollout = self.run_kube(
                [
                    "-n",
                    self.cfg.namespace,
                    "rollout",
                    "status",
                    f"deploy/{deploy}",
                    f"--timeout={self.cfg.wait_timeout}",
                ],
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
            pods = self.run_kube(
                ["-n", self.cfg.namespace, "get", "pods", "-o", "wide"],
                check=False,
            )
            if pods.stdout.strip():
                print(pods.stdout.rstrip())
            if pods.stderr.strip():
                print(pods.stderr.rstrip(), file=sys.stderr)
            raise RuntimeError(f"{failures} deployment(s) failed readiness checks.")
