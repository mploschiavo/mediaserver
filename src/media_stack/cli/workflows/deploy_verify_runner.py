"""DeployVerifyRunner — orchestrate deterministic deploy + verification.

ADR-0015 Phase 7i. Pre-Phase-7i ``DeployVerifyCommand`` lived in
commands/ with a ``@staticmethod _run`` violator + module-level
alias hacks (the ``main()`` body called bare ``info()`` / ``_run()``
that resolved via module-level singleton aliases). Phase 7i moves
the workflow onto this class with proper instance methods.
"""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

from media_stack.core.exceptions import ConfigError, MediaStackError


_VALID_PROFILES = frozenset(
    {"minimal", "full", "public-demo", "power-user"}
)


class DeployVerifyRunner:
    """Workflow: install → verify-flow → smoke → optional Playwright → status."""

    def ts(self) -> str:
        return time.strftime("%Y-%m-%dT%H:%M:%S%z")

    def info(self, message: str) -> None:
        print(f"[{self.ts()}] [INFO] {message}")

    def _run_script(
        self,
        script_path: Path,
        *args: str,
        env: dict[str, str] | None = None,
    ) -> None:
        command = [str(script_path), *args]
        proc = subprocess.run(
            command,
            check=False,
            text=True,
            env=(dict(os.environ) | dict(env or {})),
        )
        if proc.returncode != 0:
            raise MediaStackError(
                f"Command failed (exit={proc.returncode}): {' '.join(command)}"
            )

    def run(
        self,
        *,
        node_ip: str,
        namespace: str,
        profile: str,
        ingress_domain: str,
        run_playwright: bool,
        root_dir: Path,
    ) -> int:
        if not node_ip:
            raise ConfigError("NODE_IP is required")
        if profile not in _VALID_PROFILES:
            raise ConfigError(
                f"Unsupported profile '{profile}'. Use {'|'.join(sorted(_VALID_PROFILES))}."
            )

        scripts_dir = root_dir / "bin"

        self.info("Starting deploy and verification")
        self.info(f"Node IP: {node_ip}")
        self.info(f"Namespace: {namespace}")
        self.info(f"Profile: {profile}")
        self.info(f"Ingress domain: {ingress_domain}")

        self.info("Phase 1/5: install and bootstrap")
        self._run_script(
            scripts_dir / "install.sh",
            "--profile", profile,
            "--namespace", namespace,
            "--ingress-domain", ingress_domain,
            "--node-ip", node_ip,
        )

        self.info("Phase 2/5: verify end-to-end flow")
        self._run_script(scripts_dir / "test" / "verify-flow.sh", namespace)

        self.info("Phase 3/5: ingress smoke test")
        self._run_script(
            scripts_dir / "test" / "microk8s-smoke-test.sh", node_ip, namespace,
        )

        if run_playwright:
            self.info("Phase 4/5: Playwright ingress smoke")
            self._run_script(
                scripts_dir / "test" / "run-playwright-smoke.sh",
                node_ip, namespace,
            )
        else:
            self.info(
                "Phase 4/5: Playwright ingress smoke skipped (RUN_PLAYWRIGHT=0)"
            )

        self.info("Phase 5/5: final status snapshot")
        self._run_script(
            scripts_dir / "utils" / "stack-status.sh",
            env={"NAMESPACE": namespace},
        )

        print()
        print(
            f"[OK] Deploy + verification complete for namespace '{namespace}'."
        )
        print("[INFO] Render hosts entries if needed:")
        print(
            f"  bash bin/utils/render-hosts-example.sh {node_ip} {namespace}"
        )
        return 0


__all__ = ["DeployVerifyRunner"]
