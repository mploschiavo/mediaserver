"""Downloads-domain GET routes (ADR-0007 Phase 2 wave 3).

Three routes migrated off the ``handlers_get.handle()`` elif
chain. Two download-related paths were already migrated in
earlier waves and are intentionally NOT re-registered here:

* ``GET /api/downloads`` lives in ``api/routes/stack_update.py``
  (``StackUpdateGetRoutes.handle_downloads``).
* ``GET /api/download-history`` lives in
  ``api/routes/indexers_quality.py``
  (``IndexersQualityGetRoutes.handle_download_history``).

Re-registering either would trip the Router's duplicate-path
guard at startup (``RouterMisconfigured``).

Routes covered (all GET):

* ``/api/download-client-settings`` (Content) — connection +
  queueing knobs for both download clients. Backed by
  ``content_svc.get_download_client_settings``.
* ``/api/download-categories`` (Config) — per-client category
  catalogue (qB labels, SAB categories). Backed by
  ``config_svc.get_download_categories``.
* ``/api/download-analytics`` (Config) — aggregated history
  rollup (totals, per-service counts, top indexers, daily
  trend). Backed by ``content_svc.get_download_analytics``.

Pattern: **Adapter** — each route method adapts the legacy
service-call shape ("call a free function on the services
package, hand the dict to ``_json_response``") onto the Router's
class-method registration surface. The body of each method is
lifted verbatim from ``handlers_get.py`` so behaviour is
byte-stable; only the registration mechanism changes
(``@get(path)``-tagged class methods vs. ``elif path == "..."``
branches).
"""

from __future__ import annotations

from http import HTTPStatus
from typing import Any

from media_stack.api.routing import RouteModule, get
from media_stack.api.services import config as config_svc
from media_stack.api.services import content as content_svc


class DownloadsGetRoutes(RouteModule):
    """Download-domain GET routes — client settings, categories,
    and analytics. The Router auto-discovers + instantiates this
    class + walks its tagged methods at startup.

    ``GET /api/downloads`` and ``GET /api/download-history`` are
    NOT registered here — see this module's docstring for where
    they actually live.
    """

    @get("/api/download-client-settings")
    def handle_download_client_settings(self, handler: Any) -> None:
        """Return connection + queueing knobs for the configured
        download clients (qBittorrent rate limits, SABnzbd queue
        depth, Jellyfin scan-task state). Drives the Settings →
        Downloads tab.
        """
        handler._json_response(
            HTTPStatus.OK, content_svc.get_download_client_settings(),
        )

    @get("/api/download-categories")
    def handle_download_categories(self, handler: Any) -> None:
        """Return the configured per-client category catalogue (qB
        labels, SAB categories) used by the *arr clients to route
        downloads into the right ``save_path``. Drives Settings →
        Categories.
        """
        handler._json_response(
            HTTPStatus.OK, config_svc.get_download_categories(),
        )

    @get("/api/download-analytics")
    def handle_download_analytics(self, handler: Any) -> None:
        """Return the aggregated download-history rollup —
        ``total_records``, per-service counts, top indexers,
        daily trend. Drives the Downloads page's analytics
        charts.
        """
        handler._json_response(
            HTTPStatus.OK, content_svc.get_download_analytics(),
        )


__all__ = ["DownloadsGetRoutes"]
