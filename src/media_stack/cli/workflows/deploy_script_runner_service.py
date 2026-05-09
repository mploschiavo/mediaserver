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
    """Run a shell script under ``bin/`` or a Python module path.

    Per ADR-0012: the previously module-level ``_find_script`` helper
    is folded onto this class as the ``find_script`` instance method
    (no ``@staticmethod``). The module-level ``_INSTANCE`` carries an
    ``_find_script`` alias so test patches and the historical
    underscore-prefixed import surface keep resolving.
    """

    cfg: DeployScriptRunnerConfig

    def find_script(self, bin_dir: Path, name: str) -> Path:
        """Find a script in ``bin/`` or its first-level subdirectories."""
        direct = bin_dir / name
        if direct.is_file():
            return direct
        for child in bin_dir.iterdir():
            if child.is_dir() and not child.name.startswith("."):
                candidate = child / name
                if candidate.is_file():
                    return candidate
        return direct  # fall through — let caller handle missing file

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
            # Dispatch through ``sys.modules`` so ``mock.patch`` on the
            # module-level ``_find_script`` alias keeps intercepting
            # (ADR-0012 design principle 3).
            _module = sys.modules[__name__]
            script_path = _module._find_script(self.cfg.root_dir / "bin", script_name)
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


# Module-level singleton + aliases (ADR-0012 pattern).
# ``DeployScriptRunnerService`` is a dataclass that requires ``cfg``
# to instantiate, so build a bare instance via ``__new__`` purely to
# host the ``find_script`` helper alias (the helper is stateless —
# it doesn't read ``self.cfg``).
_INSTANCE = DeployScriptRunnerService.__new__(DeployScriptRunnerService)

_find_script = _INSTANCE.find_script
