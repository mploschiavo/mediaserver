"""Bootstrap job log capture and lookup helpers."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from media_stack.core.exceptions import KubernetesError
from media_stack.core.platforms.kubernetes.kube_client import KubernetesClient


@dataclass(frozen=True)
class ControllerJobLogsConfig:
    namespace: str
    job_name: str
    log_file: Path
    tail_lines: int


@dataclass
class ControllerJobLogsService:
    cfg: ControllerJobLogsConfig
    kube: KubernetesClient

    def capture_logs(self) -> None:
        # Try deployment label selector first (persistent service mode),
        # fall back to job/ prefix (legacy Job mode).
        result = self.kube.run(
            [
                "-n",
                self.cfg.namespace,
                "logs",
                "-l", f"app={self.cfg.job_name}",
                "--timestamps",
                "--tail=500",
            ],
            check=False,
        )
        if result.returncode != 0 or not (result.stdout or "").strip():
            result = self.kube.run(
                [
                    "-n",
                    self.cfg.namespace,
                    "logs",
                    f"job/{self.cfg.job_name}",
                    "--timestamps",
                ],
                check=False,
            )
        if result.returncode != 0:
            raise KubernetesError(result.stderr or result.stdout)
        self.cfg.log_file.write_text(result.stdout or "", encoding="utf-8")
        lines = (result.stdout or "").splitlines()
        tail = lines[-max(1, self.cfg.tail_lines) :]
        if tail:
            print("\n".join(tail))

    def log_contains(self, marker: str) -> bool:
        if not self.cfg.log_file.exists():
            return False
        try:
            return marker in self.cfg.log_file.read_text(encoding="utf-8")
        except Exception:
            return False
