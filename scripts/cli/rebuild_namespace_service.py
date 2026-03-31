"""Namespace lifecycle helpers for rebuild/bootstrap orchestration."""

from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass
from typing import Callable

InfoFn = Callable[[str], None]
RunKubectlFn = Callable[..., subprocess.CompletedProcess[str]]


@dataclass(frozen=True)
class RebuildNamespaceConfig:
    namespace: str
    kubectl: list[str]


@dataclass
class RebuildNamespaceService:
    cfg: RebuildNamespaceConfig
    info: InfoFn
    run_kubectl: RunKubectlFn

    def wait_for_namespace_deleted(self, max_wait_seconds: int = 600) -> None:
        waited = 0
        while True:
            probe = subprocess.run(
                [*self.cfg.kubectl, "get", "namespace", self.cfg.namespace],
                capture_output=True,
                text=True,
                check=False,
            )
            if probe.returncode != 0:
                return
            if waited >= max_wait_seconds:
                raise RuntimeError(
                    f"Namespace '{self.cfg.namespace}' is still terminating after {max_wait_seconds}s."
                )
            self.info(f"Waiting for namespace/{self.cfg.namespace} deletion (elapsed {waited}s)")
            time.sleep(5)
            waited += 5

    def delete_namespace_optional(self, delete_namespace: str) -> bool:
        if delete_namespace != "1":
            return False

        exists = subprocess.run(
            [*self.cfg.kubectl, "get", "namespace", self.cfg.namespace],
            capture_output=True,
            text=True,
            check=False,
        )
        if exists.returncode != 0:
            self.info(f"Namespace/{self.cfg.namespace} does not exist; continuing")
            return True

        self.info(f"Deleting namespace/{self.cfg.namespace}")
        self.run_kubectl(["delete", "namespace", self.cfg.namespace, "--wait=false"])
        self.wait_for_namespace_deleted()
        return True
