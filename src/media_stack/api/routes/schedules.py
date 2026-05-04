"""Schedules GET routes (ADR-0007 Phase 2 wave 6 cleanup).

Re-homed from ``api/routes/misc_legacy.py``. The wave-4
``misc_legacy`` bucket reconciliation found that only
``/api/schedules`` actually needed migration (every other
candidate was already in a sibling module or ineligible for
spec-parity reasons), so the catch-all module ended up owning a
single Jobs-tagged endpoint. Wave 6 fixes the organizational
mismatch by giving the route a properly-named home.

The handler is a thin pass-through to the scheduler service
singleton — the scheduling logic itself is covered by the
per-service test files.
"""

from __future__ import annotations

from http import HTTPStatus
from typing import Any

from media_stack.api.routing import RouteModule, get
from media_stack.api.services import scheduler as sched_svc


class SchedulesGetRoutes(RouteModule):
    """``/api/schedules`` — list configured recurring schedules.

    The Router auto-discovers + instantiates this class + walks
    its tagged methods at startup. Stateless — the underlying
    scheduler service is already a module-level singleton, so
    there's nothing to constructor-inject here without forcing
    the singleton open.
    """

    @get("/api/schedules")
    def handle_schedules(self, handler: Any) -> None:
        """Configured recurring schedules.

        Returns the scheduler's ``get_schedules`` payload —
        ``{"count": N, "schedules": [...]}`` — unchanged from the
        legacy chain. Drives the Jobs UI's "Schedules" tab.
        """
        handler._json_response(
            HTTPStatus.OK, sched_svc.get_schedules(),
        )


__all__ = ["SchedulesGetRoutes"]
