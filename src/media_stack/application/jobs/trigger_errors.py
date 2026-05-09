"""Exceptions raised by the ``TriggerEngine`` (ADR-0009).

Lifted out of ``trigger_engine`` so importers needing only the
exception types (e.g. tests, callers wrapping ``register_schedules``)
don't pull the full engine module — and to keep the engine's file
size below the codebase-wide files-over-400-lines ratchet.
"""

from __future__ import annotations

from typing import Any


class InvalidTriggerError(ValueError):
    """A trigger entry on a Job contract failed schema validation.

    Carries the offending job name and trigger payload so loader-side
    error messages can point at a specific contract instead of saying
    "something somewhere is wrong".
    """

    def __init__(self, job_name: str, trigger: Any, reason: str) -> None:
        self.job_name = job_name
        self.trigger = trigger
        self.reason = reason
        super().__init__(
            f"job {job_name!r}: invalid trigger {trigger!r}: {reason}"
        )


class TriggerCycleError(ValueError):
    """The static completion-graph contains a cycle."""

    def __init__(self, cycle: list[str]) -> None:
        self.cycle = cycle
        super().__init__(
            "trigger cycle detected: "
            + " -> ".join(cycle)
            + " (each step is 'when this job completes/fails, run that job')"
        )


__all__ = ["InvalidTriggerError", "TriggerCycleError"]
