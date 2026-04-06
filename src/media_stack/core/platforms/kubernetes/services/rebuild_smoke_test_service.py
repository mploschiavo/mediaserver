"""Ingress smoke-test helper for rebuild/bootstrap."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from typing import Callable

InfoFn = Callable[[str], None]
WarnFn = Callable[[str], None]
RunScriptFn = Callable[..., None]


@dataclass
class RebuildSmokeTestService:
    namespace: str
    node_ip: str
    info: InfoFn
    warn: WarnFn
    run_script: RunScriptFn

    def run_smoke_test(self) -> str:
        node_ip = self.node_ip.strip()
        if not node_ip:
            probe = subprocess.run(
                ["bash", "-lc", "hostname -I | awk '{print $1}'"],
                capture_output=True,
                text=True,
                check=False,
            )
            node_ip = (probe.stdout or "").strip()

        if not node_ip:
            self.warn("Could not detect NODE_IP; skipping smoke test.")
            return ""

        self.info(f"Running ingress smoke test against node IP {node_ip}")
        self.run_script(
            "microk8s-smoke-test.sh",
            node_ip,
            env={"NAMESPACE": self.namespace},
        )
        return node_ip
