"""Compat re-export shim — ADR-0015 Phase 4.

The error types + min-disk constant moved to
:mod:`media_stack.cli.workflows.deploy_errors` so the workflows-
tier orchestration code (``deploy_orchestration/*``, ``deploy_config/*``)
can import them without violating the Phase 6 boundary ratchet
(``workflows/`` MUST NOT import ``commands/``).

This file remains for the entry-point shim (``deploy_stack_main.py``)
+ existing test imports (``from media_stack.cli.commands.deploy_stack_main
import DeployError``) which keep working via re-export. Removal of the
shim is queued for Phase 6's cleanup pass.
"""

from media_stack.cli.workflows.deploy_errors import (
    DeployError,
    SkipPhase,
    _MIN_STACK_DISK_ALLOCATION_GB,
)


__all__ = ["DeployError", "SkipPhase", "_MIN_STACK_DISK_ALLOCATION_GB"]
