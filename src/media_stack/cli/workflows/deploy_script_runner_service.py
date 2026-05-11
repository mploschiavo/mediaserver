"""Compat shim — ADR-0015 Phase 2.

The Phase 2 unification (commit landing this file change)
collapsed :class:`DeployScriptRunnerService` and the controller-side
:class:`ControllerScriptRunnerService` into a single
:class:`ScriptRunnerService` in :mod:`script_runner_service`. The
two services had identical bodies modulo one env-var injection;
keeping both forks was the audit's Phase 2 finding.

This shim preserves the legacy import surface so existing callers
(``cli/workflows/deploy_orchestration/deploy_service_factories.py``)
and tests (``tests/unit/adapters/test_rebuild_script_runner_service.py``)
continue to work:

* :class:`DeployScriptRunnerConfig` keeps its ``root_dir`` +
  ``namespace`` fields. A property adapts it into a
  :class:`ScriptRunnerConfig` with ``extra_env={"NAMESPACE": …}``.
* :class:`DeployScriptRunnerService` subclasses
  :class:`ScriptRunnerService` and overrides ``__post_init__`` to
  apply the namespace adapter.

Removal of this shim is queued for ADR-0015 Phase 6's cleanup
pass (with all known callers migrated to the unified service).
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
class DeployScriptRunnerConfig:
    """Legacy config — adapts to :class:`ScriptRunnerConfig` via the service."""

    root_dir: Path
    namespace: str


@dataclass
class DeployScriptRunnerService(ScriptRunnerService):
    """Deploy-side adapter: injects ``NAMESPACE`` env into every script call.

    Pre-Phase-2 this had a full duplicated body; now it's a thin
    subclass that re-wraps the legacy config dataclass into the
    unified :class:`ScriptRunnerConfig` with the namespace as
    ``extra_env={"NAMESPACE": …}``.
    """

    cfg: DeployScriptRunnerConfig  # type: ignore[assignment]

    def __post_init__(self) -> None:
        # Translate the legacy config into the unified shape on
        # construction. The unified service reads ``self.cfg`` as
        # a ScriptRunnerConfig; we replace the dataclass-bound
        # value with an adapted view that carries the namespace
        # via extra_env. The legacy ``cfg.namespace`` attribute
        # is still readable for any caller that introspects it.
        self.cfg = ScriptRunnerConfig(  # type: ignore[assignment]
            root_dir=self.cfg.root_dir,
            extra_env={"NAMESPACE": self.cfg.namespace},
        )


# Module-level alias for test-patch compatibility. Tests historically
# mock ``_find_script`` on this module path; preserve that import
# surface by re-exposing the unified alias here.
_INSTANCE = DeployScriptRunnerService.__new__(DeployScriptRunnerService)
_find_script = _INSTANCE.find_script

# Also expose the module-level alias the bash branch in
# ``run_script`` dispatches through. The unified runner's bash
# branch does ``sys.modules[script_runner_service]._find_script``;
# legacy callers that import this module and patch its own
# ``_find_script`` get the same effect because the alias above
# points at the unified instance method.
sys.modules[__name__]._find_script = _find_script  # type: ignore[attr-defined]


__all__ = ["DeployScriptRunnerConfig", "DeployScriptRunnerService"]
