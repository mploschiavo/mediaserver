"""EPG / IPTV / LiveTV / RSS-feed GET routes (ADR-0007 Phase 2 wave 3).

Five routes migrated off the ``handlers_get.handle()`` elif chain
covering the electronic-program-guide + IPTV-tuner + RSS surfaces:

* ``GET /api/livetv-sources`` — configured M3U tuner URLs +
  XMLTV guide URLs as parallel ``tuners[]`` / ``guides[]`` lists
  with a single "currently active" pair scalarized into
  ``tuner_url`` / ``guide_url``.
* ``GET /api/iptv-countries`` — IPTV country presets with
  pre-resolved guide and tuner URLs. ``source`` flags whether
  the operator-edited profile or the built-in defaults catalogue
  is in effect.
* ``GET /api/epg-providers`` — enabled guide-provider list (in
  priority order) plus the cached probe results from the most
  recent health check.
* ``GET /api/epg-health`` — synchronous probe of every enabled
  guide provider for every configured country; returns
  aggregate counters + a sparse per-country / per-provider
  details matrix.
* ``GET /api/feed.xml`` — RSS 2.0 feed of recent action events
  + last health-check status for operators subscribed via an
  RSS reader. Returns ``application/rss+xml`` (NOT JSON), so it
  uses ``handler._raw_response`` instead of ``_json_response``
  and the body is the UTF-8-encoded XML produced by
  ``metrics_svc.get_rss_feed``.

Implementation choices, per Phase 2's "lift the body OR call the
helper — agent's choice based on what's cleanest" rule:

* ``livetv-sources`` + ``iptv-countries`` are one-line
  delegations to the ``config_svc`` shim functions
  (``get_livetv_sources`` / ``get_iptv_countries``) the legacy
  chain already used. No body lift needed.
* ``feed.xml`` lifts the legacy two-line body verbatim — the
  status code, content-type, and ``.encode("utf-8")`` shape are
  load-bearing for downstream RSS readers, so a delegation
  helper would just hide a one-line call.
* ``epg-providers`` + ``epg-health`` LIFT the legacy bodies
  (which deferred-import out of ``epg_provider_service``) into
  the route methods, preserving the lazy-import shape so the
  full provider catalogue + health cache aren't pulled into
  memory at startup. Note: ``_load_health_cache`` has a
  leading-underscore name in the source module but is exported
  as a module-level shim for cross-module callers — the legacy
  chain imports it that way and we keep the same import.
"""

from __future__ import annotations

from http import HTTPStatus
from typing import Any

from media_stack.api.cache import api_cache
from media_stack.api.routing import RouteModule, get
from media_stack.api.services import config as config_svc
from media_stack.api.services import metrics as metrics_svc


class EpgGetRoutes(RouteModule):
    """Config-tag + Metrics-tag GET routes covering live-TV
    sources, IPTV-country presets, EPG provider config + health,
    and the RSS feed. The Router auto-discovers + instantiates
    this class + walks its tagged methods at startup."""

    @get("/api/livetv-sources")
    def handle_livetv_sources(self, handler: Any) -> None:
        """Return the configured M3U tuner URLs + EPG XMLTV guide
        URLs as parallel ``tuners[]`` / ``guides[]`` lists with the
        currently active pair scalarized into ``tuner_url`` /
        ``guide_url``. Powers the LiveTV settings page.
        """
        handler._json_response(
            HTTPStatus.OK, config_svc.get_livetv_sources(),
        )

    @get("/api/iptv-countries")
    def handle_iptv_countries(self, handler: Any) -> None:
        """Return the IPTV country presets with pre-resolved guide
        and tuner URLs. ``source`` distinguishes the operator-edited
        ``profile`` list from the built-in ``defaults`` catalogue.
        """
        handler._json_response(
            HTTPStatus.OK, config_svc.get_iptv_countries(),
        )

    @get("/api/epg-providers")
    def handle_epg_providers(self, handler: Any) -> None:
        """Return the enabled guide-provider list (in priority
        order) + the cached probe outcomes from the most recent
        health check. The ``epg_provider_service`` import is
        deferred to keep the route module's startup graph minimal.
        """
        from media_stack.services.epg_provider_service import (
            _load_health_cache,
            get_guide_providers,
        )
        handler._json_response(HTTPStatus.OK, {
            "providers": get_guide_providers(),
            "health": _load_health_cache(),
        })

    @get("/api/epg-health")
    def handle_epg_health(self, handler: Any) -> None:
        """Synchronously probe every enabled guide provider for
        every configured country; return aggregate counters
        (``healthy`` / ``unhealthy``) plus a sparse per-country,
        per-provider ``details`` matrix.
        """
        from media_stack.services.epg_provider_service import (
            run_health_check,
        )
        handler._json_response(HTTPStatus.OK, run_health_check())

    @get("/api/feed.xml")
    def handle_feed_xml(self, handler: Any) -> None:
        """Return an RSS 2.0 feed of action events + last health
        status. Uses ``_raw_response`` (not ``_json_response``)
        because RSS readers expect ``application/rss+xml``; the
        body is the UTF-8-encoded XML produced by
        ``metrics_svc.get_rss_feed``.
        """
        handler._raw_response(
            HTTPStatus.OK,
            "application/rss+xml; charset=utf-8",
            metrics_svc.get_rss_feed(handler.state, api_cache).encode(
                "utf-8",
            ),
        )


__all__ = ["EpgGetRoutes"]
