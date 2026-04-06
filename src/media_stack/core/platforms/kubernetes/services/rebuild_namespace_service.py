"""Namespace lifecycle helpers for rebuild/bootstrap orchestration."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable

InfoFn = Callable[[str], None]
RunKubeFn = Callable[..., object]


@dataclass(frozen=True)
class RebuildNamespaceConfig:
    namespace: str


@dataclass
class RebuildNamespaceService:
    cfg: RebuildNamespaceConfig
    info: InfoFn
    run_kube: RunKubeFn

    def wait_for_namespace_deleted(self, max_wait_seconds: int = 600) -> None:
        waited = 0
        while True:
            probe = self.run_kube(["get", "namespace", self.cfg.namespace], check=False)
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

        exists = self.run_kube(["get", "namespace", self.cfg.namespace], check=False)
        if exists.returncode != 0:
            self.info(f"Namespace/{self.cfg.namespace} does not exist; continuing")
            return True

        self.info(f"Deleting namespace/{self.cfg.namespace}")
        self.run_kube(["delete", "namespace", self.cfg.namespace, "--wait=false"])
        self.wait_for_namespace_deleted()
        return True
