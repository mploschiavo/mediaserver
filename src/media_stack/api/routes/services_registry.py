"""Services-registry GET routes (ADR-0007 Phase 2).

Three routes migrated off the ``handlers_get.handle()`` elif chain:

* ``GET /api/services`` — Apps-page listing of every registered
  service with profile-aware filtering. Drives the dashboard's
  card grid.
* ``GET /api/services/categories`` — category groupings derived
  from the registry YAML, plus a synthetic ``Infrastructure``
  bucket for the controller itself. Drives the grouped UI view.
* ``GET /api/services/{serviceId}/api-key`` — per-service API-key
  status (configured? masked preview?) without ever returning the
  full key. Parameterized — the spec declares ``serviceId`` as a
  path parameter (camelCase, NOT ``service_id`` — note the spec
  uses camelCase here while ``/api/services/{service_id}/reset``
  uses snake_case; we match the spec verbatim per path).

Implementation choices, per Phase 2's "lift the body OR call the
helper — agent's choice based on what's cleanest" rule:

* The two unparameterized routes delegate to the existing
  ``GetRequestHandler._handle_services`` / ``_handle_services_categories``
  helpers in ``handlers_get``. The first reads
  ``handler.path`` for the ``?include=all`` query string, which
  the Router doesn't strip — keeping the helper avoids
  reimplementing query-param parsing.
* The parameterized route LIFTS the body. The legacy
  ``_handle_service_api_key`` extracts the service id by parsing
  ``path.split("/")`` itself; the Router already hands us
  ``serviceId`` as a kwarg from the regex match
  (``(?P<serviceId>[^/]+)`` — see ``_RouteCompiler._compile_pattern``),
  so re-parsing would be redundant work that could drift from the
  Router's view of the URL. Lifting the body uses the kwarg
  directly and shrinks the handler to the actual lookup logic.

When ADR-0007's final cleanup commit deletes the legacy chain,
the two delegated helpers can move into this file or stay where
they are; either way these route methods stay unchanged.
"""

from __future__ import annotations

import os
from http import HTTPStatus
from typing import Any

from media_stack.api.handlers_get import (
    _handle_services,
    _handle_services_categories,
)
from media_stack.api.routing import RouteModule, get


class ServicesRegistryGetRoutes(RouteModule):
    """All ``/api/services*`` GET routes. The Router auto-discovers
    + instantiates this class + walks its tagged methods at
    startup."""

    @get("/api/services")
    def handle_services(self, handler: Any) -> None:
        """Return the Apps-page listing — every registered service
        with hostname/port plus the synthetic ``controller`` entry,
        filtered by ``COMPOSE_PROFILES`` unless ``?include=all``
        is set. Delegates to the legacy helper because that helper
        also handles query-string parsing off ``handler.path``.
        """
        _handle_services(handler)

    @get("/api/services/categories")
    def handle_services_categories(self, handler: Any) -> None:
        """Return the registry's category groupings with the
        ``Infrastructure`` bucket augmented to include the
        controller itself. Delegates to the legacy helper.
        """
        _handle_services_categories(handler)

    @get("/api/services/{serviceId}/api-key")
    def handle_service_api_key(
        self, handler: Any, serviceId: str,
    ) -> None:
        """Return API-key status (configured? masked preview?) for a
        single service. The kwarg name ``serviceId`` matches the
        spec's path-parameter declaration verbatim — the Router's
        ``_RouteCompiler._check_handler_signature`` enforces this
        at startup; a mismatch raises ``RouterMisconfigured`` before
        the server binds.

        Body lifted from
        ``GetRequestHandler._handle_service_api_key`` — the legacy
        version parses ``path`` itself to extract the service id,
        but the Router already hands us the parsed value as a
        kwarg, so re-parsing would just create an opportunity for
        drift between the Router's URL view and the handler's.
        """
        from media_stack.api.services.registry import SERVICE_MAP
        svc = SERVICE_MAP.get(serviceId)
        if not svc or not svc.api_key_env:
            handler._json_response(
                HTTPStatus.NOT_FOUND,
                {
                    "error": (
                        f"Service '{serviceId}' not found or has "
                        f"no API key"
                    ),
                },
            )
            return
        current = (os.environ.get(svc.api_key_env) or "").strip()
        if len(current) > 8:
            preview = f"{current[:4]}...{current[-4:]}"
        else:
            preview = "set" if current else ""
        handler._json_response(HTTPStatus.OK, {
            "service": serviceId,
            "env": svc.api_key_env,
            "has_key": bool(current),
            "key_preview": preview,
        })


__all__ = ["ServicesRegistryGetRoutes"]
