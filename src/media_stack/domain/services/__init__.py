"""Service-lifecycle types — ADR-0003 Phase 1.

Public surface lives in ``lifecycle``. Re-exported here so callers can
``from media_stack.domain.services import ServiceLifecycle, ProbeResult,
Outcome, OrchestrationContext`` without reaching into the submodule.
"""

from __future__ import annotations

from media_stack.domain.services.lifecycle import (
    OrchestrationContext,
    Outcome,
    ProbeResult,
    ProbeStatus,
    ServiceLifecycle,
)

__all__ = [
    "OrchestrationContext",
    "Outcome",
    "ProbeResult",
    "ProbeStatus",
    "ServiceLifecycle",
]
