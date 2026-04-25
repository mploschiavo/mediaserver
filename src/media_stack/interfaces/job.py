"""Job port.

Every recurring or one-shot operation that the ``JobRunner`` knows
how to schedule implements ``Job``. The current implementation
lives in ``services/jobs/framework.py``; Phase 16-E will rebuild it
on top of this port.

Phase 16-A scaffolding: protocol + result/context shapes only.
No production code imports from here yet.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class JobContext:
    """Per-invocation context passed to ``Job.run``.

    A bag of *immutable* references the job needs to do its work:
    a logger, a clock, a config snapshot, opaque correlation id.
    The shape stays narrow on purpose — anything bulkier belongs
    behind a port the job depends on, not here.
    """

    correlation_id: str
    config: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class JobResult:
    """Outcome of one ``Job.run`` invocation.

    ``status`` is one of ``"ok" | "skipped" | "unknown" | "error"``
    matching the controller-rollup vocabulary. ``unknown`` and
    ``skipped`` are NOT failure signals — see the
    ``unknown-as-actionable`` bug-class memo for context.
    """

    status: str
    detail: str = ""
    artifacts: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class Job(Protocol):
    """Base port for a runnable job."""

    name: str

    def run(self, ctx: JobContext) -> JobResult:
        """Execute the job. MUST be the only side-effecting method
        on the port. Pure inputs in (``ctx``), structured outcome
        out (``JobResult``). Logging via ``ctx`` only."""
