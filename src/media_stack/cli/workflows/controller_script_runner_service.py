"""Script/module execution helper for bootstrap orchestration."""

from __future__ import annotations

import os
import shlex
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


def _find_script(bin_dir: Path, name: str) -> Path:
    """Find a script in bin/ or its subdirectories."""
    direct = bin_dir / name
    if direct.is_file():
        return direct
    for child in bin_dir.iterdir():
        if child.is_dir() and not child.name.startswith("."):
            candidate = child / name
            if candidate.is_file():
                return candidate
    return direct


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
        call_env = dict(os.environ)
        if env:
            call_env.update({k: str(v) for k, v in env.items()})

        # Python module path (e.g. "media_stack.services.apps.foo.cli.bar_main")
        if "." in script_name and not script_name.endswith(".sh"):
            cmd = [sys.executable, "-m", script_name, *list(args)]
            label = script_name
        else:
            script_path = _find_script(self.cfg.root_dir / "bin", script_name)
            cmd = ["bash", str(script_path), *list(args)]
            label = script_name

        proc = subprocess.run(
            cmd,
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
                f"{label} failed ({proc.returncode}): "
                f"{' '.join(shlex.quote(x) for x in cmd)}"
            )
