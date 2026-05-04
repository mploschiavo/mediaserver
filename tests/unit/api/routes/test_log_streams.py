"""Tests for ``api/routes/log_streams.py`` (ADR-0007 Phase 2).

Each test class owns one route. Each test invokes the production
Router via ``RouteDispatchHarness.with_default_router()`` — same
auto-discovery, same spec-parity check, same dispatch path used
in production.

The SSE route is asserted at the "Router-entered-the-SSE-branch"
level only: the harness's ``MockControllerHandler._sse_response``
is a stub that records ``status=200`` + ``content_type=
"text/event-stream"`` and stops — it deliberately doesn't try to
emulate a streaming socket. Real streaming behaviour is covered
elsewhere by integration tests that hit the live HTTP server.

The parameterized ``/api/logs/{service}`` route delegates to the
legacy ``handlers_get._handle_service_logs`` helper; we patch the
symbol the route module imported and assert the call shape (the
helper's behaviour itself is covered by the legacy handler's own
tests, so re-testing it here would just couple two test suites).
"""

from __future__ import annotations

import json
from unittest.mock import patch

from tests.unit.api.routes._helpers import RouteDispatchHarness


class TestLogStreamRoute:
    """``GET /logs/stream`` — legacy unfiltered SSE stream. The
    route handler just calls ``handler._sse_response()``; the
    ``MockControllerHandler`` stub records that the SSE branch
    fired by setting status 200 + the SSE content type."""

    def test_dispatches_to_sse_branch(self) -> None:
        harness = RouteDispatchHarness.with_default_router()
        response = harness.dispatch("GET", "/logs/stream")
        assert response.status == 200
        assert response.content_type == "text/event-stream"

    def test_post_to_sse_path_returns_method_not_allowed(self) -> None:
        harness = RouteDispatchHarness.with_default_router()
        from media_stack.api.routing import DispatchOutcome
        outcome, _ = harness.try_dispatch("POST", "/logs/stream")
        assert outcome == DispatchOutcome.METHOD_NOT_ALLOWED


class TestServiceLogsRoute:
    """``GET /api/logs/{service}`` — parameterized recent-logs
    fetch. Body delegated to the legacy helper; we assert that
    delegation happens (and the ``service`` path-param flows
    through into the rebuilt ``path`` argument the helper expects).
    """

    @patch("media_stack.api.routes.log_streams._handle_service_logs")
    def test_delegates_to_legacy_helper(self, mock_helper) -> None:
        def _emit(handler, _path):
            handler._json_response(200, {"lines": ["log line 1"]})
        mock_helper.side_effect = _emit

        harness = RouteDispatchHarness.with_default_router()
        response = harness.dispatch("GET", "/api/logs/sonarr")

        assert response.status == 200
        assert json.loads(response.body) == {"lines": ["log line 1"]}
        mock_helper.assert_called_once()

    @patch("media_stack.api.routes.log_streams._handle_service_logs")
    def test_passes_service_path_to_helper(self, mock_helper) -> None:
        """The helper's signature is ``(handler, path)`` and it
        re-parses the path to pull the service id back out. The
        route module reconstructs ``/api/logs/<service>`` from the
        Router's ``service`` kwarg so the helper still finds it
        at ``path.split('/')[3]``."""
        def _emit(handler, _path):
            handler._json_response(200, {"path_seen": _path})
        mock_helper.side_effect = _emit

        harness = RouteDispatchHarness.with_default_router()
        response = harness.dispatch("GET", "/api/logs/sonarr")

        assert response.status == 200
        body = json.loads(response.body)
        assert body == {"path_seen": "/api/logs/sonarr"}
        mock_helper.assert_called_once()
        _handler_arg, path_arg = mock_helper.call_args.args
        assert path_arg == "/api/logs/sonarr"

    @patch("media_stack.api.routes.log_streams._handle_service_logs")
    def test_service_with_dashes_passes_through(self, mock_helper) -> None:
        """Path params accept any non-slash chars per the Router's
        regex — names with dashes / digits dispatch cleanly."""
        def _emit(handler, _path):
            handler._json_response(200, {"path_seen": _path})
        mock_helper.side_effect = _emit

        harness = RouteDispatchHarness.with_default_router()
        response = harness.dispatch("GET", "/api/logs/media-stack-controller")

        assert response.status == 200
        body = json.loads(response.body)
        assert body == {"path_seen": "/api/logs/media-stack-controller"}


class TestRoutingIntegration:
    """Pin auto-discovery + spec-parity behaviour for the
    log-streams domain. If a future change accidentally drops a
    handler from the registry, this test fires before any
    per-route test does."""

    def test_all_log_stream_routes_registered(self) -> None:
        harness = RouteDispatchHarness.with_default_router()
        expected = {"/logs/stream", "/api/logs/{service}"}
        registered = {
            r.path
            for r in harness._dispatcher._router.registered_routes()
            if r.path in expected
        }
        assert registered == expected, (
            f"Missing log-stream routes: {expected - registered}"
        )

    def test_parameterized_route_captures_service_id(self) -> None:
        """The Router's regex captures arbitrary path segments
        (with hyphens, digits, etc.) as ``service``. The handler
        delegates to the helper, which sees the rebuilt path."""
        with patch(
            "media_stack.api.routes.log_streams._handle_service_logs",
        ) as mock_helper:
            def _emit(handler, _path):
                handler._json_response(200, {"ok": True, "path": _path})
            mock_helper.side_effect = _emit

            harness = RouteDispatchHarness.with_default_router()
            response = harness.dispatch(
                "GET", "/api/logs/some-arr-99",
            )
        assert response.status == 200
        body = json.loads(response.body)
        assert body["path"] == "/api/logs/some-arr-99"
