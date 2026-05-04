"""Indexers + arr-webhooks + quality-presets GET routes
(ADR-0007 Phase 2).

Seven routes migrated off the ``handlers_get.handle()`` elif chain,
all sharing the ``Content`` OpenAPI tag:

* ``GET /api/indexers`` — Prowlarr indexer list with
  enabled/disabled state.
* ``GET /api/indexer-stats`` — query/grab/failure counters per
  indexer from Prowlarr's stats endpoint.
* ``GET /api/download-history`` — most recent Sonarr/Radarr
  download history, capped at 10 entries per service.
* ``GET /api/quality-presets`` — curated Trash-derived preset
  catalogue used by the dashboard's preset picker.
* ``GET /api/quality-profiles/{service}`` — current quality-profile
  config for a single Servarr service. Parameterized — the
  spec declares ``service`` (lowercase, NOT camelCase) as the
  path-param name and the handler kwarg matches verbatim.
* ``GET /api/custom-formats/{service}`` — current custom-format
  definitions for a single Servarr service. Parameterized with
  the same ``service`` kwarg shape as quality-profiles.
* ``GET /api/arr-webhooks`` — ensure each *arr has the
  Jellyfin-scan webhook registered + return the resulting status
  map. The ensure pass is idempotent (only writes when missing).

Implementation choices, per Phase 2's "lift the body OR call the
helper — agent's choice based on what's cleanest" rule:

* All five non-parameterized routes are one-line delegations to
  ``content_svc`` / ``quality_preset_service`` already-public
  functions — no wrapper helpers in ``handlers_get`` exist for
  these, so the route methods invoke the service directly.
* The two parameterized routes LIFT the legacy body. The legacy
  chain does ``svc_id = path.split("/")[-1]`` to extract the
  service id — the Router already binds it to the ``service``
  kwarg from the regex match (``(?P<service>[^/]+)`` per
  ``_RouteCompiler._compile_pattern``), so re-parsing would be
  redundant work that could drift from the Router's URL view.
  Lifting drops the parsing line and uses the kwarg directly.

The ``quality_preset_service`` import is deferred inside each
method to mirror the legacy chain's lazy-import shape — keeps
the route module's import graph minimal at startup and avoids
pulling the full preset catalogue into memory until a route
actually needs it.
"""

from __future__ import annotations

from http import HTTPStatus
from typing import Any

from media_stack.api.routing import RouteModule, get
from media_stack.api.services import content as content_svc


class IndexersQualityGetRoutes(RouteModule):
    """Content-tag GET routes covering indexers, quality presets +
    profiles, custom formats, and arr-webhook ensurance. The Router
    auto-discovers + instantiates this class + walks its tagged
    methods at startup."""

    @get("/api/indexers")
    def handle_indexers(self, handler: Any) -> None:
        """Return the Prowlarr indexer list with per-indexer
        enabled/disabled state. Drives the dashboard's Indexers
        page card grid.
        """
        handler._json_response(HTTPStatus.OK, content_svc.get_indexers())

    @get("/api/indexer-stats")
    def handle_indexer_stats(self, handler: Any) -> None:
        """Return per-indexer query/grab/failure counters from
        Prowlarr's stats endpoint. Powers the Indexers page's
        performance chart.
        """
        handler._json_response(
            HTTPStatus.OK, content_svc.get_indexer_stats(),
        )

    @get("/api/download-history")
    def handle_download_history(self, handler: Any) -> None:
        """Return the 10 most recent download-history entries per
        Servarr service (Sonarr + Radarr). Drives the Downloads
        page's recent-activity feed.
        """
        handler._json_response(
            HTTPStatus.OK, content_svc.get_download_history(),
        )

    @get("/api/quality-presets")
    def handle_quality_presets(self, handler: Any) -> None:
        """Return the curated Trash-derived preset catalogue used by
        the dashboard's preset picker. Imported lazily to keep the
        route module's startup cost flat.
        """
        from media_stack.services.apps.servarr.quality_preset_service import (
            list_presets,
        )
        handler._json_response(HTTPStatus.OK, list_presets())

    @get("/api/quality-profiles/{service}")
    def handle_quality_profiles_for_service(
        self, handler: Any, service: str,
    ) -> None:
        """Return the current quality-profile config for a single
        Servarr service. ``service`` is bound by the Router from the
        path segment per the OpenAPI ``parameters: [{name: service,
        in: path}]`` declaration; the kwarg name matches the spec
        verbatim — ``_RouteCompiler._check_handler_signature``
        enforces this at startup, so a mismatch raises
        ``RouterMisconfigured`` before the server binds.

        Body lifted from the legacy chain — the legacy version did
        ``svc_id = path.split("/")[-1]`` to extract the id, but the
        Router already hands us the parsed value as a kwarg so
        re-parsing would just create an opportunity for drift
        between the Router's URL view and the handler's.
        """
        from media_stack.services.apps.servarr.quality_preset_service import (
            get_current_profiles,
        )
        handler._json_response(
            HTTPStatus.OK, get_current_profiles(service),
        )

    @get("/api/custom-formats/{service}")
    def handle_custom_formats_for_service(
        self, handler: Any, service: str,
    ) -> None:
        """Return the current custom-format definitions for a single
        Servarr service. Same parameterization shape as
        quality-profiles — ``service`` flows from URL through the
        Router into the handler kwarg without intermediate parsing.
        """
        from media_stack.services.apps.servarr.quality_preset_service import (
            get_custom_formats,
        )
        handler._json_response(
            HTTPStatus.OK, get_custom_formats(service),
        )

    @get("/api/arr-webhooks")
    def handle_arr_webhooks(self, handler: Any) -> None:
        """Ensure every *arr has the Jellyfin-scan webhook
        registered and return the resulting per-service status map.
        The ensure pass is idempotent — only writes when the
        webhook is missing or has the wrong URL.
        """
        handler._json_response(
            HTTPStatus.OK, content_svc.ensure_arr_scan_webhooks(),
        )


__all__ = ["IndexersQualityGetRoutes"]
