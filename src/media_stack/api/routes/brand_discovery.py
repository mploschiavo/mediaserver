"""Brand + discovery GET routes (ADR-0007 Phase 2).

Two routes migrated off the ``handlers_get.handle()`` elif chain:

* ``GET /api/branding`` — white-label brand config (name, wordmark,
  homepage URL, etc.) read from ``contracts/branding.yaml``. The
  dashboard pulls this on load to render the header/favicon/splash.
* ``GET /api/discovery/popular-tv`` — Sonarr ``CustomImport`` feed
  of popular TV scraped from TVMaze, filtered to English-language
  shows with a ``thetvdb`` external id, top ~150 by rating.
  Cached in-process for 6h.

Implementation choice: each method delegates to the existing
``GetRequestHandler._handle_branding`` / ``_handle_popular_tv``
helpers in ``handlers_get`` rather than lifting their bodies
verbatim. Reasons:

* The popular-TV helper holds a class-level cache
  (``GetRequestHandler._POPULAR_TV_CACHE``) shared with the legacy
  fallback path; lifting it would split the cache into two stale
  copies depending on which path served the request.
* The branding helper is ~50 LoC of YAML candidate-path resolution
  with comment-heavy operator notes; duplicating it would create
  drift the next white-label tweak would have to chase across two
  files.

Phase 2's stated rule is "lift the body OR call the helper —
agent's choice based on what's cleanest." Calling the helper is
cleanest here. When ADR-0007's final cleanup commit deletes the
legacy chain, the helpers themselves can either move into this
file or stay where they are; either way these route methods stay
unchanged.
"""

from __future__ import annotations

from typing import Any

from media_stack.api.handlers_get import (
    _handle_branding,
    _handle_popular_tv,
)
from media_stack.api.routing import RouteModule, get


class BrandDiscoveryGetRoutes(RouteModule):
    """Brand config + discovery feeds. The Router auto-discovers
    + instantiates this class + walks its tagged methods at
    startup."""

    @get("/api/branding")
    def handle_branding(self, handler: Any) -> None:
        """Return white-label brand metadata (name, vendor, wordmark,
        etc.) merged over the defaults defined in
        ``handlers_get._handle_branding``.
        """
        _handle_branding(handler)

    @get("/api/discovery/popular-tv")
    def handle_popular_tv(self, handler: Any) -> None:
        """Return the cached TVMaze-backed popular-TV feed Sonarr's
        ``CustomImport`` provider polls. See
        ``handlers_get._handle_popular_tv`` for the cache + scoring
        rules.
        """
        _handle_popular_tv(handler)


__all__ = ["BrandDiscoveryGetRoutes"]
