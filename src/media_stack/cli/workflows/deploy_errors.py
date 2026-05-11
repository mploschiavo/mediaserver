"""Shared exceptions + constants for the deploy/bootstrap workflow.

Hoisted from ``cli/commands/deploy_stack_errors.py`` under ADR-0015
Phase 4. The workflows-tier code (``deploy_config/*``,
``deploy_orchestration/*``) needs these types; keeping them in
commands/ would force a workflows→commands import that the
Phase 6 boundary ratchet specifically forbids.

The original commands-tier module survives as a re-export shim so
existing imports (``from media_stack.cli.commands.deploy_stack_errors
import DeployError``) keep working until the shim is removed in
Phase 6's cleanup pass.
"""

from __future__ import annotations


class DeployError(RuntimeError):
    """Raised when deploy/bootstrap orchestration fails."""


class SkipPhase(RuntimeError):
    """Signal that current phase should be marked as skipped."""


_MIN_STACK_DISK_ALLOCATION_GB = 20


__all__ = ["DeployError", "SkipPhase", "_MIN_STACK_DISK_ALLOCATION_GB"]
