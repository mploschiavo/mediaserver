"""Compat shim — ADR-0015 Phase 2.

The Phase 2 unification (commit landing this file change)
collapsed :class:`ControllerScriptRunnerService` and the deploy-side
:class:`DeployScriptRunnerService` into a single
:class:`ScriptRunnerService` in :mod:`script_runner_service`. The
two services had identical bodies modulo one env-var injection;
keeping both forks was the audit's Phase 2 finding.

This shim preserves the legacy import surface so existing callers
(``cli/commands/run_controller_job_main.py``) and tests
(``tests/unit/core/test_bootstrap_script_runner_service.py``)
continue to work:

* :class:`ControllerScriptRunnerConfig` keeps its single
  ``root_dir`` field and adapts to the unified
  :class:`ScriptRunnerConfig`.
* :class:`ControllerScriptRunnerService` subclasses
  :class:`ScriptRunnerService` — no env-var injection (unlike the
  deploy variant), so the adapter is the identity transform plus
  an empty ``extra_env`` dict.

Removal of this shim is queued for ADR-0015 Phase 6's cleanup
pass.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

from media_stack.cli.workflows.script_runner_service import (
    ScriptRunnerConfig,
    ScriptRunnerService,
)


@dataclass(frozen=True)
class ControllerScriptRunnerConfig:
    """Legacy config — adapts to :class:`ScriptRunnerConfig` via the service."""

    root_dir: Path


@dataclass
class ControllerScriptRunnerService(ScriptRunnerService):
    """Controller-side adapter: no env injection, just the root_dir.

    Pre-Phase-2 this had a full duplicated body; now it's a thin
    subclass that re-wraps the legacy single-field config into the
    unified :class:`ScriptRunnerConfig`. The bootstrap-job runner
    doesn't carry a namespace because it discovers the namespace
    via :class:`KubernetesClient` at call time, not at script-runner
    construction.
    """

    cfg: ControllerScriptRunnerConfig  # type: ignore[assignment]

    def __post_init__(self) -> None:
        # Identity adapter — no extra_env injection.
        self.cfg = ScriptRunnerConfig(root_dir=self.cfg.root_dir)  # type: ignore[assignment]


# Module-level alias for test-patch compatibility (same shape as the
# deploy shim above — see :mod:`deploy_script_runner_service`).
_INSTANCE = ControllerScriptRunnerService.__new__(ControllerScriptRunnerService)
_find_script = _INSTANCE.find_script
sys.modules[__name__]._find_script = _find_script  # type: ignore[attr-defined]


__all__ = ["ControllerScriptRunnerConfig", "ControllerScriptRunnerService"]
