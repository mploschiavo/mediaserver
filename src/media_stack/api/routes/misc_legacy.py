"""Misc-legacy GET routes (ADR-0007 Phase 2 wave 4).

This module gathers the leftover top-level + ``/api/*`` GET paths
the wave-4 brief enumerated as the "misc legacy" bucket once the
sibling state, log-streams, logs, health, and system-diag waves
had already claimed the bulk of them.

Pre-flight + during-wave reconciliation showed the candidate list
broke down like this:

* ``/status`` — already migrated by ``state.py`` (wave 2).
* ``/apps`` — already migrated by ``state.py`` (wave 2).
* ``/config`` — already migrated by ``state.py`` (wave 2).
* ``/webhooks`` — already migrated by ``state.py`` (wave 2).
* ``/api/webhooks`` — NOT in ``contracts/api/openapi.yaml`` and
  the Router's startup spec-parity check rejects any registration
  for an undeclared path (``router.py::_RouteCompiler._check_in_spec``).
  Stays on the legacy ``handlers_get.py`` elif chain — same
  blocker that left ``/api/webhooks`` out of ``state.py`` per its
  module docstring.
* ``/logs/stream`` — already migrated by ``log_streams.py``
  (wave 2).
* ``/api/snapshots`` — claimed by the wave-4 sibling
  ``routes/system_diag.py`` (it co-locates the
  ``Operations``-tagged diagnostics surface). The brief explicitly
  said to skip what ``system_diag.py`` registered.
* ``/api/schedules`` — NOT yet migrated, IS in the spec under tag
  ``Jobs``. Migrated here.

So this module owns ``/api/schedules`` only — a single thin
pass-through to ``sched_svc.get_schedules``. The legacy ``elif``
branch in ``handlers_get.py`` for ``/api/schedules`` stays alive
as fallback during Phase 2; the final cleanup commit after every
domain has migrated removes it.
"""

from __future__ import annotations

from http import HTTPStatus
from typing import Any

from media_stack.api.routing import RouteModule, get
from media_stack.api.services import scheduler as sched_svc


class MiscLegacyGetRoutes(RouteModule):
    """The wave-4 leftover GET routes that didn't fit a sibling
    domain. The Router auto-discovers + instantiates this class +
    walks its tagged methods at startup.

    Stateless — every dependency is module-level (``sched_svc``);
    the underlying scheduler service is already a singleton inside
    its module so there's nothing to constructor-inject here
    without forcing the singleton open.
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


__all__ = ["MiscLegacyGetRoutes"]
