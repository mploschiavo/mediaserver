"""Script/module runner for deploy/bootstrap orchestration."""

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
        merged_env = dict(os.environ)
        merged_env.update({"NAMESPACE": self.cfg.namespace})
        if env:
            merged_env.update({k: str(v) for k, v in env.items()})

        # Python module path (e.g. "media_stack.cli.commands.foo_main")
        if "." in script_name and not script_name.endswith(".sh"):
            cmd = [sys.executable, "-m", script_name, *list(args)]
            label = script_name
        else:
            script_path = self.cfg.root_dir / "bin" / script_name
            cmd = ["bash", str(script_path), *list(args)]
            label = script_name

        proc = subprocess.run(
            cmd,
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
                f"{label} failed ({proc.returncode}): "
                f"{' '.join(shlex.quote(x) for x in cmd)}"
            )
