"""Shared exceptions and constants for the deploy-stack command modules."""

from __future__ import annotations


class DeployError(RuntimeError):
    """Raised when deploy/bootstrap orchestration fails."""


class SkipPhase(RuntimeError):
    """Signal that current phase should be marked as skipped."""


_MIN_STACK_DISK_ALLOCATION_GB = 20
