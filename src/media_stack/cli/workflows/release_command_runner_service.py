"""Backward-compatible release command runner alias."""

from __future__ import annotations

from media_stack.cli.workflows.workflow_command_runner_service import WorkflowCommandRunnerService


class ReleaseCommandRunnerService(WorkflowCommandRunnerService):
    """Compatibility name for release workflow tests and callers."""
