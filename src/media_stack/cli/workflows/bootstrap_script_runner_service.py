"""Shell script execution helper for bootstrap orchestration."""

from __future__ import annotations

import os
import shlex
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ControllerScriptRunnerConfig:
    root_dir: Path


@dataclass
class ControllerScriptRunnerService:
    cfg: ControllerScriptRunnerConfig

    def run_script(
        self,
        script_name: str,
        *args: str,
        env: dict[str, str] | None = None,
    ) -> None:
        script_path = self.cfg.root_dir / "scripts" / script_name
        call_env = dict(os.environ)
        if env:
            call_env.update({k: str(v) for k, v in env.items()})
        proc = subprocess.run(
            ["bash", str(script_path), *list(args)],
            cwd=str(self.cfg.root_dir),
            env=call_env,
            check=False,
            text=True,
            capture_output=True,
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
