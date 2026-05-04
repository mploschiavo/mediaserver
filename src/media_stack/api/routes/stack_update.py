"""Stack-update + content-summary GET routes (ADR-0007 Phase 2).

Five routes migrated off the ``handlers_get.handle()`` elif chain:

* ``GET /api/stack/update`` — probe the upstream registry for a
  newer published controller release. Drives the dashboard's
  "update available" banner.
* ``GET /api/stack/upgrade/{task_id}`` — poll the in-process
  upgrader's task state. Parameterized; the spec declares
  ``task_id`` (snake_case, NOT ``taskId``) as the path parameter,
  so the handler kwarg matches that exact name. The Router's
  ``_RouteCompiler._check_handler_signature`` enforces the
  match at startup.
* ``GET /api/versions`` — service version strings (Sonarr,
  Radarr, Jellyfin, etc.) snapshotted from each service's
  status endpoint. 5-minute TTL via the shared ``api_cache``.
* ``GET /api/downloads`` — active qBittorrent + SABnzbd
  downloads. No cache (live snapshot).
* ``GET /api/stats`` — per-arr library counts (series / movies
  / artists / books). 60-second TTL via the shared ``api_cache``.

Implementation choice (per Phase 2's "lift the body OR call the
helper — agent's choice based on what's cleanest" rule): each
legacy entry is a single-line delegation to a service-layer
function, so we lift those single lines verbatim into the route
methods. There's no helper-method indirection in
``handlers_get`` worth preserving for these five.

Note on the parameterized route: the legacy chain extracted the
task id with ``path.rsplit("/", 1)[-1]`` because the elif chain
matches by ``path.startswith("/api/stack/upgrade/")``. The Router
already binds the ``task_id`` segment from the regex match, so we
take the kwarg directly instead of re-parsing the URL. That
eliminates one of two URL-parsing layers and keeps the Router
as the single source of truth for path-component values.
"""

from __future__ import annotations

from http import HTTPStatus
from typing import Any

from media_stack.api.routing import RouteModule, get


class StackUpdateGetRoutes(RouteModule):
    """All ``/api/stack/{update,upgrade}`` + content-summary GET
    routes. The Router auto-discovers + instantiates this class +
    walks its tagged methods at startup."""

    @get("/api/stack/update")
    def handle_stack_update(self, handler: Any) -> None:
        """Return the registry-probe result: ``current`` /
        ``latest`` / ``upgradable`` / ``allow_inplace`` /
        ``last_checked_epoch`` / ``release_url``. The probe itself
        is cached at the service layer, so repeated calls don't
        hammer the registry.
        """
        from media_stack.api.services import stack_update as su_svc
        handler._json_response(
            HTTPStatus.OK, su_svc.check_for_update(),
        )

    @get("/api/stack/upgrade/{task_id}")
    def handle_stack_upgrade_status(
        self, handler: Any, task_id: str,
    ) -> None:
        """Return the upgrade task's current state (``idle`` /
        ``running`` / ``complete`` / ``failed`` / ``stale`` /
        ``unknown``). The kwarg name ``task_id`` matches the
        spec's path-parameter declaration verbatim — see the
        ``parameters: [{in: path, name: task_id}]`` block in
        ``contracts/api/openapi.yaml``.
        """
        from media_stack.api.services import stack_update as su_svc
        handler._json_response(
            HTTPStatus.OK, su_svc.upgrade_status(task_id),
        )

    @get("/api/versions")
    def handle_versions(self, handler: Any) -> None:
        """Return ``{"versions": {<service>: <version>, ...}}``
        — the inventory snapshot used by the Apps page. Cached
        for 5 minutes via the shared ``api_cache``.
        """
        from media_stack.api.cache import api_cache
        from media_stack.api.services import content as content_svc
        handler._json_response(
            HTTPStatus.OK, content_svc.get_versions(api_cache),
        )

    @get("/api/downloads")
    def handle_downloads(self, handler: Any) -> None:
        """Return active downloads from qBittorrent + SABnzbd.
        No caching — the dashboard polls this endpoint and wants
        a live snapshot, capped at 10 items per client at the
        service layer.
        """
        from media_stack.api.services import content as content_svc
        handler._json_response(
            HTTPStatus.OK, content_svc.get_downloads(),
        )

    @get("/api/stats")
    def handle_stats(self, handler: Any) -> None:
        """Return per-arr library counts. Cached for 60 seconds
        via the shared ``api_cache`` — these calls hit each arr's
        ``/api/v3/series`` / ``/movie`` / ``/artist`` / ``/book``
        endpoint, so the TTL keeps the arrs un-thrashed.
        """
        from media_stack.api.cache import api_cache
        from media_stack.api.services import content as content_svc
        handler._json_response(
            HTTPStatus.OK, content_svc.get_stats(api_cache),
        )


__all__ = ["StackUpdateGetRoutes"]
