"""Shell script runner for deploy/bootstrap orchestration."""

from __future__ import annotations

import os
import shlex
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class DeployScriptRunnerConfig:
    root_dir: Path
    namespace: str


@dataclass
class DeployScriptRunnerService:
    cfg: DeployScriptRunnerConfig

    def run_script(self, script_name: str, *args: str, env: dict[str, str] | None = None) -> None:
        script_path = self.cfg.root_dir / "scripts" / script_name
        merged_env = dict(os.environ)
        merged_env.update({"NAMESPACE": self.cfg.namespace})
        if env:
            merged_env.update({k: str(v) for k, v in env.items()})

        proc = subprocess.run(
            ["bash", str(script_path), *args],
            cwd=str(self.cfg.root_dir),
            env=merged_env,
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.stdout.strip():
            print(proc.stdout.rstrip())
        if proc.stderr.strip():
            print(proc.stderr.rstrip(), file=sys.stderr)
        if proc.returncode != 0:
            raise RuntimeError(
                f"{script_name} failed ({proc.returncode}): "
                f"{' '.join(shlex.quote(x) for x in [str(script_path), *args])}"
            )
