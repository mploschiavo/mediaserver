"""ScriptRunnerService — unified shell-or-Python-module dispatcher.

ADR-0015 Phase 2 consolidates the two pre-existing duplicate
script runners:

* ``DeployScriptRunnerService`` (``DeployScriptRunnerConfig`` with
  ``root_dir`` + ``namespace``; injects ``NAMESPACE`` env var).
* ``ControllerScriptRunnerService`` (``ControllerScriptRunnerConfig``
  with ``root_dir`` only).

Both files had identical bodies for ``find_script`` and ``run_script``
modulo one env-var injection — a classic copy-paste duplication
the audit flagged as Phase 2 of ADR-0015.

This module defines the single :class:`ScriptRunnerService` (Strategy
pattern: the strategy for dispatching a script-name token to either
a Python module via ``-m`` or a shell script under ``bin/``).
:class:`ScriptRunnerConfig` carries the operator-tunable bits
(``root_dir`` + an injectable ``extra_env`` dict). The deploy-side
callers pass ``extra_env={"NAMESPACE": ns}``; the controller-side
callers pass ``extra_env=None``. One body, two consumers, no fork.

The pre-Phase-2 modules (``deploy_script_runner_service``,
``controller_script_runner_service``) lived as backwards-
compatible shims for one release cycle and were deleted in
ADR-0015 Phase 6. All callers now construct
:class:`ScriptRunnerService` directly — deploy-side passes
``extra_env={"NAMESPACE": ns}``, controller-side passes nothing.
"""

from __future__ import annotations

import os
import shlex
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class ScriptRunnerConfig:
    """Operator-tunable bits the runner consumes.

    Frozen because callers (``deploy_stack_runner_services.py``,
    ``run_controller_job_main.py``) instantiate once per phase and
    reuse — mutating mid-run would silently change script
    behaviour across calls.
    """

    root_dir: Path
    extra_env: dict[str, str] = field(default_factory=dict)


@dataclass
class ScriptRunnerService:
    """Strategy: dispatch a script-name token to a Python module or a bin/ script.

    Token-shape contract:

    * Contains ``.`` and does NOT end in ``.sh`` → Python module
      path (e.g. ``media_stack.cli.commands.foo_main``). Run via
      ``sys.executable -m <token>``.
    * Otherwise → shell script under ``<root_dir>/bin/`` (resolved
      via :meth:`find_script`'s direct + one-level-deep scan).
      Run via ``bash <path>``.

    Both branches:

    * Inherit ``os.environ`` (sampled at run time, not construction
      — preflight handlers earlier in the same deploy may have
      already mutated env, e.g. exporting STACK_ADMIN_PASSWORD).
    * Layer ``cfg.extra_env`` over (the deploy use case injects
      ``NAMESPACE`` this way).
    * Layer the per-call ``env`` kwarg over that (caller's
      one-off overrides win).

    On non-zero exit the runner raises :class:`RuntimeError` with
    a shell-quoted reproduction of the failing command.
    """

    cfg: ScriptRunnerConfig

    def find_script(self, bin_dir: Path, name: str) -> Path:
        """Find a script in ``bin/`` or its first-level subdirectories.

        Walks ``bin/<name>`` first, then ``bin/<subdir>/<name>``
        for every non-dot subdir. Returns the direct path if no
        match is found — the caller surfaces the missing-file
        error when ``subprocess.run`` reports ``ENOENT``.
        """
        direct = bin_dir / name
        if direct.is_file():
            return direct
        for child in bin_dir.iterdir():
            if child.is_dir() and not child.name.startswith("."):
                candidate = child / name
                if candidate.is_file():
                    return candidate
        return direct

    def run_script(
        self,
        script_name: str,
        *args: str,
        env: dict[str, str] | None = None,
    ) -> None:
        merged_env = self._build_env(env)
        cmd, label = self._build_command(script_name, args)

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

    # -- helpers (one responsibility each, both instance methods) --------

    def _build_env(self, per_call_env: dict[str, str] | None) -> dict[str, str]:
        """Layer per-call env over cfg.extra_env over os.environ.

        Sampled at call time so preflight env writes earlier in the
        same deploy propagate. The order ensures per-call overrides
        win over config defaults win over ambient env.
        """
        merged = dict(os.environ)
        if self.cfg.extra_env:
            merged.update(self.cfg.extra_env)
        if per_call_env:
            merged.update({k: str(v) for k, v in per_call_env.items()})
        return merged

    def _build_command(
        self,
        script_name: str,
        args: tuple[str, ...],
    ) -> tuple[list[str], str]:
        """Return ``(argv, label)`` for the dispatched command.

        Python-module dispatch when the token has dots and doesn't
        end in .sh; bash-script dispatch otherwise. Bash dispatch
        goes through ``sys.modules[__name__]._find_script`` so the
        legacy module-level alias still intercepts test patches.
        """
        if "." in script_name and not script_name.endswith(".sh"):
            return (
                [sys.executable, "-m", script_name, *list(args)],
                script_name,
            )
        _module = sys.modules[__name__]
        script_path = _module._find_script(self.cfg.root_dir / "bin", script_name)
        return (["bash", str(script_path), *list(args)], script_name)


# Module-level alias for test-patch compatibility (ADR-0012 design
# principle 3 — the bash branch dispatches through this name so
# tests can patch find_script via ``mock.patch.object(MODULE,
# "_find_script", …)``).
_INSTANCE = ScriptRunnerService.__new__(ScriptRunnerService)
_find_script = _INSTANCE.find_script


__all__ = [
    "ScriptRunnerConfig",
    "ScriptRunnerService",
]
