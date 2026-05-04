"""Services-registry GET routes (ADR-0007 Phase 2).

Three routes migrated off the ``handlers_get.handle()`` elif chain:

* ``GET /api/services`` -- Apps-page listing of every registered
  service with profile-aware filtering. Drives the dashboard's
  card grid.
* ``GET /api/services/categories`` -- category groupings derived
  from the registry YAML, plus a synthetic ``Infrastructure``
  bucket for the controller itself. Drives the grouped UI view.
* ``GET /api/services/{service_id}/api-key`` -- per-service API-key
  status (configured? masked preview?) without ever returning the
  full key. Parameterized -- the spec declares ``service_id`` as a
  path parameter (snake_case, matching the rest of the controller's
  wire-format convention).

ADR-0007 Phase 2 Phase E: bodies lifted verbatim from the legacy
``GetRequestHandler._handle_services`` / ``_handle_services_categories``
helpers so the legacy chain can be deleted entirely.
"""

from __future__ import annotations

import copy
import os
from http import HTTPStatus
from typing import Any
from urllib.parse import parse_qs

from media_stack.api.routing import RouteModule, get


class ServicesListingService:
    """Builds the Apps-page service listing.

    Accepts an ``include_all`` flag for the ``?include=all`` query
    param so the route module can hand off the parsed value rather
    than re-parsing the URL.
    """

    def build(self, *, include_all: bool) -> dict[str, Any]:
        from media_stack.api.services.registry import (
            SERVICES,
            build_apps_listing,
        )

        ctrl_port = int(
            os.environ.get(
                "BOOTSTRAP_API_PORT",
                os.environ.get("CONTROLLER_PORT", "9100"),
            ),
        )
        return build_apps_listing(
            list(SERVICES),
            include_all=include_all,
            controller_port=ctrl_port,
        )


class ServicesCategoriesService:
    """Returns the registry's categories plus a synthetic
    ``Infrastructure`` bucket for the controller."""

    def build(self) -> list[dict[str, Any]]:
        from media_stack.api.services.registry import CATEGORIES

        cats = copy.deepcopy(CATEGORIES)
        infra = next(
            (c for c in cats if c["label"].lower() == "infrastructure"),
            None,
        )
        if infra:
            if "controller" not in infra["ids"]:
                infra["ids"].append("controller")
        else:
            cats.append(
                {"label": "Infrastructure", "ids": ["controller"]},
            )
        return cats


class ServicesRegistryGetRoutes(RouteModule):
    """All ``/api/services*`` GET routes. The Router auto-discovers
    + instantiates this class + walks its tagged methods at
    startup."""

    def __init__(
        self,
        *,
        listing_service: ServicesListingService | None = None,
        categories_service: ServicesCategoriesService | None = None,
    ) -> None:
        self._listing = listing_service or ServicesListingService()
        self._categories = (
            categories_service or ServicesCategoriesService()
        )

    @get("/api/services")
    def handle_services(self, handler: Any) -> None:
        """Return the Apps-page listing -- every registered service
        with hostname/port plus the synthetic ``controller`` entry,
        filtered by ``COMPOSE_PROFILES`` unless ``?include=all``
        is set."""
        # The Apps page renders one card per launchable, profile-
        # active service. Two filter dimensions:
        #
        #   * ``web_ui: false`` -- hidden registry entries that exist
        #     ONLY to anchor jobs in the bootstrap DAG (``core``,
        #     ``media_integrity``). They have no host/port and the
        #     dashboard must not render them.
        #
        #   * Profile gate -- the active deploy's ``COMPOSE_PROFILES``
        #     set decides whether plex / authentik / traefik / etc.
        #     should be considered "deployed". Without this filter
        #     the launcher used to show every YAML-declared service
        #     (28+) regardless of whether the operator actually
        #     deployed it, leading to a row of broken tiles and a
        #     "why is plex listed when I never enabled it?" support
        #     loop.
        #
        # Operators can opt out per-request with ``?include=all`` --
        # useful for tooling and the registry inspector -- but the UI
        # treats the unfiltered list as the default.
        params: dict[str, str] = {}
        if "?" in handler.path:
            for k, vs in parse_qs(
                handler.path.split("?", 1)[1], keep_blank_values=True,
            ).items():
                if vs:
                    params[k] = vs[0]
        include_all = (
            params.get("include", "").strip().lower() == "all"
        )
        handler._json_response(
            HTTPStatus.OK,
            self._listing.build(include_all=include_all),
        )

    @get("/api/services/categories")
    def handle_services_categories(self, handler: Any) -> None:
        """Return the registry's category groupings with the
        ``Infrastructure`` bucket augmented to include the
        controller itself."""
        handler._json_response(
            HTTPStatus.OK, self._categories.build(),
        )

    @get("/api/services/{service_id}/api-key")
    def handle_service_api_key(
        self, handler: Any, service_id: str,
    ) -> None:
        """Return API-key status (configured? masked preview?) for a
        single service. The kwarg name ``service_id`` matches the
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
        svc = SERVICE_MAP.get(service_id)
        if not svc or not svc.api_key_env:
            handler._json_response(
                HTTPStatus.NOT_FOUND,
                {
                    "error": (
                        f"Service '{service_id}' not found or has "
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
            "service": service_id,
            "env": svc.api_key_env,
            "has_key": bool(current),
            "key_preview": preview,
        })


__all__ = [
    "ServicesRegistryGetRoutes",
    "ServicesListingService",
    "ServicesCategoriesService",
]
