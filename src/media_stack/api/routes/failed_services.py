"""Failed-services + auto-heal GET routes (ADR-0007 Phase 2).

Covers ``/api/failed-services`` and ``/api/auto-heal``. Both are
``Health``-tagged in ``contracts/api/openapi.yaml`` but live in
their own narrow domain — surfacing the controller's auto-heal
machinery (``ControllerState.failed_services`` + the
``services/auto_heal.py`` recovery loop). Combined into a single
``RouteModule`` because each is one tiny endpoint.

Method bodies are lifted verbatim from the legacy
``handlers_get.GetRequestHandler.handle()`` chain
(``/api/failed-services`` at line 217, ``/api/auto-heal`` at
line 265). Phase 2 only moves WHERE the dispatch decision is
made (Router instead of an ``elif`` chain); the response shape
is identical so downstream consumers see no change.
"""

from __future__ import annotations

from http import HTTPStatus
from typing import Any

from media_stack.api.routing import RouteModule, get


class FailedServicesGetRoutes(RouteModule):
    """Auto-heal-domain GET routes. The Router auto-discovers and
    instantiates this class at startup, then walks tagged methods
    for registration."""

    @get("/api/failed-services")
    def handle_failed_services(self, handler: Any) -> None:
        """Services that have tripped the failure threshold.

        Returns ``{"failed_services": <map>, "count": <int>}`` —
        a snapshot of ``ControllerState.failed_services`` plus the
        current cardinality. ``get_failed_services`` returns a
        dict keyed by service id; ``len(...)`` gives the count of
        keys (i.e. distinct failing services).
        """
        handler._json_response(HTTPStatus.OK, {
            "failed_services": handler.state.get_failed_services(),
            "count": len(handler.state.get_failed_services()),
        })

    @get("/api/auto-heal")
    def handle_auto_heal(self, handler: Any) -> None:
        """Auto-heal service status.

        Returns ``{"enabled": <bool>, "recent_events": [...]}`` —
        whether the auto-heal loop is armed and a tail of the
        recovery actions it has emitted recently. See
        ``services/auto_heal.py::status`` for the field semantics.
        """
        from media_stack.api.services import auto_heal as autoheal_svc
        handler._json_response(HTTPStatus.OK, autoheal_svc.status())


__all__ = ["FailedServicesGetRoutes"]
