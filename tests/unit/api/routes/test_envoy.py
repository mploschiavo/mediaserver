"""Tests for ``api/routes/envoy.py`` (ADR-0007 Phase 2 wave 3).

Each test class owns one route. Each test invokes the production
Router via ``RouteDispatchHarness.with_default_router()`` — same
auto-discovery, same spec-parity check, same dispatch path used
in production.

The four routes delegate to ``metrics_svc`` /
``envoy_access_log.tail_envoy_access_log``; we patch those module-
level references on the route module to assert "this route
delegates to the right service function" without re-testing
service behaviour. The two query-string-bearing routes
(``access-log``, ``timeseries``) are exercised end-to-end with
real URLs so the ``parse_qs(urlparse(handler.path).query)``
plumbing is also covered.
"""

from __future__ import annotations

import json
from unittest.mock import patch

from media_stack.api.routing import DefaultDispatcher, DispatchOutcome
from tests.unit.api.routes._helpers import (
    CapturedResponse,
    MockControllerHandler,
    RouteDispatchHarness,
)


def _dispatch_with_query(
    verb: str, path_with_query: str,
) -> CapturedResponse:
    """Mimic the production dispatch path: strip the query string
    before route-matching, but leave it on ``handler.path`` so the
    handler-side ``parse_qs(urlparse(handler.path).query)`` finds
    the params.

    The shared ``RouteDispatchHarness.dispatch`` doesn't do this
    strip today (it passes ``path`` to both the dispatcher AND the
    handler). Production strips at ``server.py`` before invoking
    the dispatcher; this helper simulates that step for routes
    whose handlers re-parse the query string off ``handler.path``.
    """
    DefaultDispatcher.reset_for_tests()
    dispatcher = DefaultDispatcher.instance()
    bare_path = path_with_query.split("?", 1)[0]
    handler = MockControllerHandler(path=path_with_query)
    outcome = dispatcher.try_dispatch(verb, bare_path, handler)
    if outcome == DispatchOutcome.METHOD_NOT_ALLOWED:
        dispatcher.write_method_not_allowed(handler, bare_path)
    return handler.captured


class TestEnvoyStatsRoute:
    """``GET /api/envoy/stats`` — filtered Envoy admin-API counters."""

    @patch("media_stack.api.routes.envoy.metrics_svc")
    def test_returns_envoy_stats(self, mock_metrics) -> None:
        mock_metrics.get_envoy_stats.return_value = {
            "stats": {
                "http.ingress_http.downstream_rq_total": 15423,
                "http.ingress_http.downstream_rq_2xx": 14891,
            },
            "raw_count": 2847,
        }
        harness = RouteDispatchHarness.with_default_router()
        response = harness.dispatch("GET", "/api/envoy/stats")

        assert response.status == 200
        assert json.loads(response.body) == {
            "stats": {
                "http.ingress_http.downstream_rq_total": 15423,
                "http.ingress_http.downstream_rq_2xx": 14891,
            },
            "raw_count": 2847,
        }
        mock_metrics.get_envoy_stats.assert_called_once_with()

    @patch("media_stack.api.routes.envoy.metrics_svc")
    def test_returns_error_envelope_when_admin_unreachable(
        self, mock_metrics,
    ) -> None:
        # The service layer wraps its own errors into the response
        # body; the route just relays the dict unchanged.
        mock_metrics.get_envoy_stats.return_value = {
            "stats": {},
            "error": "Connection refused",
        }
        harness = RouteDispatchHarness.with_default_router()
        response = harness.dispatch("GET", "/api/envoy/stats")

        assert response.status == 200
        body = json.loads(response.body)
        assert body == {"stats": {}, "error": "Connection refused"}


class TestEnvoyAdminSummaryRoute:
    """``GET /api/envoy/admin-summary`` — operator-facing aggregate."""

    @patch("media_stack.api.routes.envoy.metrics_svc")
    def test_returns_admin_summary(self, mock_metrics) -> None:
        mock_metrics.get_envoy_admin_summary.return_value = {
            "clusters": [
                {"name": "service_jellyfin", "healthy": 1, "total": 1},
            ],
            "rq_total": 15423,
            "rq_per_s": 12.5,
            "p95_ms": 42,
            "active_cx": 7,
            "tls_handshake_errors": 0,
        }
        harness = RouteDispatchHarness.with_default_router()
        response = harness.dispatch("GET", "/api/envoy/admin-summary")

        assert response.status == 200
        body = json.loads(response.body)
        assert body["clusters"][0]["name"] == "service_jellyfin"
        assert body["rq_total"] == 15423
        assert body["p95_ms"] == 42
        mock_metrics.get_envoy_admin_summary.assert_called_once_with()


class TestEnvoyAccessLogRoute:
    """``GET /api/envoy/access-log`` — tail recent access-log rows."""

    @patch(
        "media_stack.api.routes.envoy.tail_envoy_access_log",
    )
    def test_returns_rows_with_default_limit(self, mock_tail) -> None:
        mock_tail.return_value = [
            {"ts": "2026-05-03T00:00:00Z", "method": "GET",
             "path": "/api/health", "status": 200},
        ]
        harness = RouteDispatchHarness.with_default_router()
        response = harness.dispatch("GET", "/api/envoy/access-log")

        assert response.status == 200
        body = json.loads(response.body)
        assert body["limit"] == 50
        assert body["rows"][0]["path"] == "/api/health"
        mock_tail.assert_called_once_with(limit=50)

    @patch(
        "media_stack.api.routes.envoy.tail_envoy_access_log",
    )
    def test_clamps_limit_above_max(self, mock_tail) -> None:
        mock_tail.return_value = []
        response = _dispatch_with_query(
            "GET", "/api/envoy/access-log?limit=99999",
        )

        assert response.status == 200
        body = json.loads(response.body)
        assert body["limit"] == 500
        mock_tail.assert_called_once_with(limit=500)

    @patch(
        "media_stack.api.routes.envoy.tail_envoy_access_log",
    )
    def test_clamps_limit_below_min(self, mock_tail) -> None:
        mock_tail.return_value = []
        response = _dispatch_with_query(
            "GET", "/api/envoy/access-log?limit=0",
        )

        assert response.status == 200
        body = json.loads(response.body)
        assert body["limit"] == 1
        mock_tail.assert_called_once_with(limit=1)

    @patch(
        "media_stack.api.routes.envoy.tail_envoy_access_log",
    )
    def test_falls_back_to_default_on_invalid_limit(
        self, mock_tail,
    ) -> None:
        mock_tail.return_value = []
        response = _dispatch_with_query(
            "GET", "/api/envoy/access-log?limit=not-a-number",
        )

        assert response.status == 200
        body = json.loads(response.body)
        assert body["limit"] == 50
        mock_tail.assert_called_once_with(limit=50)

    @patch(
        "media_stack.api.routes.envoy.tail_envoy_access_log",
    )
    def test_passes_explicit_limit_within_bounds(self, mock_tail) -> None:
        mock_tail.return_value = []
        response = _dispatch_with_query(
            "GET", "/api/envoy/access-log?limit=25",
        )

        assert response.status == 200
        body = json.loads(response.body)
        assert body["limit"] == 25
        mock_tail.assert_called_once_with(limit=25)

    @patch(
        "media_stack.api.routes.envoy.tail_envoy_access_log",
    )
    def test_returns_500_envelope_when_tailer_raises_oserror(
        self, mock_tail,
    ) -> None:
        # OSError is one of the narrowed catch classes — the route
        # body must convert it to the legacy 500 + error envelope
        # rather than letting it propagate.
        mock_tail.side_effect = OSError("permission denied")
        harness = RouteDispatchHarness.with_default_router()
        response = harness.dispatch("GET", "/api/envoy/access-log")

        assert response.status == 500
        body = json.loads(response.body)
        assert body["rows"] == []
        assert "permission denied" in body["error"]

    @patch(
        "media_stack.api.routes.envoy.tail_envoy_access_log",
    )
    def test_returns_500_envelope_when_tailer_raises_runtimeerror(
        self, mock_tail,
    ) -> None:
        mock_tail.side_effect = RuntimeError("docker not available")
        harness = RouteDispatchHarness.with_default_router()
        response = harness.dispatch("GET", "/api/envoy/access-log")

        assert response.status == 500
        body = json.loads(response.body)
        assert body["rows"] == []
        assert "docker not available" in body["error"]

    @patch(
        "media_stack.api.routes.envoy.tail_envoy_access_log",
    )
    def test_truncates_long_error_to_200_chars(
        self, mock_tail,
    ) -> None:
        long_msg = "x" * 500
        mock_tail.side_effect = ValueError(long_msg)
        harness = RouteDispatchHarness.with_default_router()
        response = harness.dispatch("GET", "/api/envoy/access-log")

        assert response.status == 500
        body = json.loads(response.body)
        assert len(body["error"]) == 200


class TestEnvoyTimeseriesRoute:
    """``GET /api/envoy/timeseries`` — rolling buffer of samples +
    derived rate deltas."""

    @patch("media_stack.api.routes.envoy.metrics_svc")
    def test_returns_timeseries_with_default_window(
        self, mock_metrics,
    ) -> None:
        mock_metrics.get_envoy_timeseries.return_value = {
            "samples": [{"ts": 1000, "rq_total": 10}],
            "deltas": [],
            "window_seconds": 1800,
            "now": 2000,
        }
        harness = RouteDispatchHarness.with_default_router()
        response = harness.dispatch("GET", "/api/envoy/timeseries")

        assert response.status == 200
        body = json.loads(response.body)
        assert body["window_seconds"] == 1800
        mock_metrics.get_envoy_timeseries.assert_called_once_with(1800)

    @patch("media_stack.api.routes.envoy.metrics_svc")
    def test_passes_explicit_window_to_service(self, mock_metrics) -> None:
        mock_metrics.get_envoy_timeseries.return_value = {
            "samples": [], "deltas": [], "window_seconds": 600, "now": 1,
        }
        response = _dispatch_with_query(
            "GET", "/api/envoy/timeseries?window=600",
        )

        assert response.status == 200
        # The route hands the raw int to the service; clamping to
        # >=60 is the service's job (so it stays a single source of
        # truth for the buffer's behaviour).
        mock_metrics.get_envoy_timeseries.assert_called_once_with(600)

    @patch("media_stack.api.routes.envoy.metrics_svc")
    def test_falls_back_to_default_on_invalid_window(
        self, mock_metrics,
    ) -> None:
        mock_metrics.get_envoy_timeseries.return_value = {
            "samples": [], "deltas": [],
            "window_seconds": 1800, "now": 1,
        }
        response = _dispatch_with_query(
            "GET", "/api/envoy/timeseries?window=not-a-number",
        )

        assert response.status == 200
        mock_metrics.get_envoy_timeseries.assert_called_once_with(1800)


class TestRoutingIntegration:
    """Pin the auto-discovery + spec-parity behaviour for the
    envoy domain. If a future change accidentally drops a handler
    from the registry, this fires before any per-route test does.
    """

    def test_all_envoy_routes_registered(self) -> None:
        harness = RouteDispatchHarness.with_default_router()
        expected = {
            "/api/envoy/stats",
            "/api/envoy/admin-summary",
            "/api/envoy/access-log",
            "/api/envoy/timeseries",
        }
        registered = {
            r.path
            for r in harness._dispatcher._router.registered_routes()
            if r.path in expected
        }
        assert registered == expected, (
            f"Missing envoy routes: {expected - registered}"
        )

    def test_post_to_envoy_stats_returns_method_not_allowed(self) -> None:
        harness = RouteDispatchHarness.with_default_router()
        outcome, _ = harness.try_dispatch("POST", "/api/envoy/stats")
        from media_stack.api.routing import DispatchOutcome
        assert outcome == DispatchOutcome.METHOD_NOT_ALLOWED
