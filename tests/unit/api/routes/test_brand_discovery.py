"""Tests for ``api/routes/brand_discovery.py`` (ADR-0007 Phase 2).

Two routes covered:
  * ``GET /api/branding``
  * ``GET /api/discovery/popular-tv``

Both delegate to the legacy ``_handle_*`` helpers in
``handlers_get``. These tests exercise the production Router
(auto-discovery + spec parity) end-to-end and mock the
network/filesystem dependencies of the underlying helpers.
"""

from __future__ import annotations

import json
from unittest.mock import patch

from tests.unit.api.routes._helpers import RouteDispatchHarness


class TestBrandingRoute:
    """``GET /api/branding`` — yaml-config-backed brand metadata.

    The handler probes a few candidate paths for
    ``contracts/branding.yaml``; mocking ``Path.is_file`` to ``False``
    forces it onto the defaults branch, which is deterministic +
    independent of the test runner's working directory.
    """

    def test_returns_default_brand_when_no_config_file(self) -> None:
        # Build the harness BEFORE patching ``Path.is_file`` —
        # ``DefaultDispatcher.instance()`` reads the OpenAPI spec
        # from disk via ``Path.is_file`` itself; patching first
        # would make the Router refuse to construct.
        harness = RouteDispatchHarness.with_default_router()
        with patch(
            "media_stack.api.routes.brand_discovery.Path.is_file",
            return_value=False,
        ):
            response = harness.dispatch("GET", "/api/branding")
        assert response.status == 200
        body = json.loads(response.body)
        assert "brand" in body
        brand = body["brand"]
        # Defaults shipped by the controller (see
        # GetRequestHandler._handle_branding).
        assert brand["name"] == "Media Stack"
        assert brand["vendor"] == "iomio"
        assert brand["tagline"] == "Media Stack Controller"
        assert brand["homepage_url"].startswith("http")
        assert brand["wordmark"].startswith("/api/static/")
        assert brand["icon"].startswith("/api/static/")
        assert brand["illustration"].startswith("/api/static/")


class TestPopularTvRoute:
    """``GET /api/discovery/popular-tv`` — Sonarr CustomImport feed.

    The handler short-circuits on a fresh in-process cache, so the
    cleanest test seeds the cache, dispatches, and asserts the
    cached payload comes back. Resetting the cache between tests
    keeps them order-independent.
    """

    def setup_method(self) -> None:
        from media_stack.api.routes.brand_discovery import _popular_tv
        # Reset to a clean state before each test so cache state
        # from a prior run doesn't leak.
        _popular_tv._cache_ts = 0.0
        _popular_tv._cache_payload = []

    def teardown_method(self) -> None:
        from media_stack.api.routes.brand_discovery import _popular_tv
        _popular_tv._cache_ts = 0.0
        _popular_tv._cache_payload = []

    def test_serves_cached_payload_when_fresh(self) -> None:
        import time
        from media_stack.api.routes.brand_discovery import _popular_tv

        cached = [
            {"tvdbId": 81189, "title": "Breaking Bad"},
            {"tvdbId": 121361, "title": "Game of Thrones"},
        ]
        _popular_tv._cache_ts = time.time()
        _popular_tv._cache_payload = cached
        harness = RouteDispatchHarness.with_default_router()
        response = harness.dispatch("GET", "/api/discovery/popular-tv")
        assert response.status == 200
        assert json.loads(response.body) == cached

    def test_falls_back_to_stale_cache_when_tvmaze_unreachable(
        self,
    ) -> None:
        from media_stack.api.routes.brand_discovery import _popular_tv

        # Stale cache (ts=0 == far in the past) + urlopen raising
        # forces the "stale fallback" branch.
        stale = [{"tvdbId": 79126, "title": "The Wire"}]
        _popular_tv._cache_ts = 0.0
        _popular_tv._cache_payload = stale
        with patch(
            "urllib.request.urlopen",
            side_effect=OSError("network down"),
        ):
            harness = RouteDispatchHarness.with_default_router()
            response = harness.dispatch(
                "GET", "/api/discovery/popular-tv",
            )
        assert response.status == 200
        assert json.loads(response.body) == stale


class TestRoutingIntegration:
    """Pin auto-discovery + spec parity for the brand+discovery
    domain. If a future change drops a handler from the registry,
    this fires before any per-route test does."""

    def test_all_brand_discovery_routes_registered(self) -> None:
        harness = RouteDispatchHarness.with_default_router()
        expected = {"/api/branding", "/api/discovery/popular-tv"}
        registered = {
            r.path
            for r in harness._dispatcher._router.registered_routes()
            if r.path in expected
        }
        assert registered == expected, (
            f"Missing brand/discovery routes: {expected - registered}"
        )

    def test_post_to_branding_returns_method_not_allowed(self) -> None:
        # /api/branding is GET-only in the spec; POST should be 405.
        harness = RouteDispatchHarness.with_default_router()
        outcome, _ = harness.try_dispatch("POST", "/api/branding")
        from media_stack.api.routing import DispatchOutcome
        assert outcome == DispatchOutcome.METHOD_NOT_ALLOWED

    def test_post_to_popular_tv_returns_method_not_allowed(self) -> None:
        harness = RouteDispatchHarness.with_default_router()
        outcome, _ = harness.try_dispatch(
            "POST", "/api/discovery/popular-tv",
        )
        from media_stack.api.routing import DispatchOutcome
        assert outcome == DispatchOutcome.METHOD_NOT_ALLOWED
