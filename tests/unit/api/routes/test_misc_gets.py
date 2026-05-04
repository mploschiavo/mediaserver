"""Tests for ``api/routes/misc_gets.py`` (ADR-0007 Phase 2 wave 5).

Each test class owns one route. Each test invokes the production
Router via ``RouteDispatchHarness.with_default_router()`` — same
auto-discovery, same spec-parity check, same dispatch path used
in production.

Patch targets:

* ``grafana.json`` routes through ``metrics_svc.get_grafana_dashboard``
  imported at module top — patch the ``metrics_svc`` reference on
  the route module.
* ``snapshot-diff`` routes through ``ops_svc.diff_snapshots`` —
  patch the ``ops_svc`` reference on the route module.
* ``openapi.json`` + ``openapi.yaml`` route through the
  ``_SpecDumpStrategy`` instance constructed in
  ``MiscGetsGetRoutes.__init__``. The route module's strategies
  bind ``_OPENAPI_YAML`` + ``_build_openapi_servers`` at
  instantiation time (Router startup), so we patch the strategy
  itself or its private ``_yaml_source`` / ``_servers_builder``
  attrs to control output without touching the legacy module.
* ``/api/events`` is SSE — the route method delegates to a
  ``_SseEventEmitter`` strategy whose ``emit`` writes to the
  socket via ``handler.send_response`` / ``handler.wfile.write``.
  ``MockControllerHandler`` doesn't implement those (it's not a
  streaming socket), so the SSE tests patch
  ``_SseEventEmitter.emit`` to capture invocation + assert that
  the route hands off the parsed ``topics`` set correctly.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import yaml

from tests.unit.api.routes._helpers import RouteDispatchHarness


class TestEventsSseRoute:
    """``GET /api/events`` — unified domain-event SSE stream.

    The route method parses ``handler.path``'s query string for
    ``topics=``, builds the topic set via
    ``events_sse_svc.parse_topics``, then hands off to a freshly
    constructed ``_SseEventEmitter`` instance whose ``emit`` runs
    the SSE write loop. We patch ``_SseEventEmitter.emit`` so the
    test never tries to drive a real ``send_response`` /
    ``wfile.write`` cycle (the mock handler isn't a real socket).
    """

    @patch("media_stack.api.routes.misc_gets._SseEventEmitter.emit")
    def test_dispatches_to_sse_emitter(self, mock_emit) -> None:
        harness = RouteDispatchHarness.with_default_router()
        harness.dispatch("GET", "/api/events")
        mock_emit.assert_called_once()

    def test_parses_topics_query_param(self) -> None:
        """The route reads off the unstripped ``handler.path`` so
        the dispatcher's query-string strip doesn't take ``topics=``
        with it. We patch the ``_SseEventEmitter`` constructor to
        capture the topic set the route built before handing off
        to the strategy."""
        from media_stack.api.routes import misc_gets as route_mod
        from tests.unit.api.routes._helpers import MockControllerHandler

        captured: dict = {}
        real_init = route_mod._SseEventEmitter.__init__

        def _capture_init(self, topics):
            captured["topics"] = topics
            real_init(self, topics)

        with patch.object(
            route_mod._SseEventEmitter, "__init__", _capture_init,
        ), patch.object(route_mod._SseEventEmitter, "emit"):
            instance = route_mod.MiscGetsGetRoutes()
            handler = MockControllerHandler(
                path="/api/events?topics=jobs",
            )
            instance.handle_events_sse(handler)

        assert "topics" in captured
        assert "jobs" in captured["topics"]

    def test_empty_topics_yields_all_known_topics(self) -> None:
        """An absent ``topics=`` query param means "all known
        topics" per ``events_sse_svc.parse_topics``. The captured
        topic set should contain at least the ``jobs`` and
        ``sessions`` topics shipping today."""
        from media_stack.api.routes import misc_gets as route_mod
        from tests.unit.api.routes._helpers import MockControllerHandler

        captured: dict = {}
        real_init = route_mod._SseEventEmitter.__init__

        def _capture_init(self, topics):
            captured["topics"] = topics
            real_init(self, topics)

        with patch.object(
            route_mod._SseEventEmitter, "__init__", _capture_init,
        ), patch.object(route_mod._SseEventEmitter, "emit"):
            instance = route_mod.MiscGetsGetRoutes()
            handler = MockControllerHandler(path="/api/events")
            instance.handle_events_sse(handler)

        assert "topics" in captured
        assert "jobs" in captured["topics"]
        assert "sessions" in captured["topics"]


class TestGrafanaDashboardRoute:
    """``GET /api/grafana.json`` — pre-built Grafana dashboard
    JSON. One-line delegation to ``metrics_svc.get_grafana_dashboard``
    imported at module top, so we patch the ``metrics_svc``
    reference on the route module.
    """

    @patch("media_stack.api.routes.misc_gets.metrics_svc")
    def test_returns_grafana_dashboard_json(
        self, mock_metrics,
    ) -> None:
        mock_metrics.get_grafana_dashboard.return_value = {
            "dashboard": {
                "title": "Media Stack",
                "panels": [
                    {"type": "stat", "title": "Services Up"},
                ],
            },
            "overwrite": True,
        }
        harness = RouteDispatchHarness.with_default_router()
        response = harness.dispatch("GET", "/api/grafana.json")

        assert response.status == 200
        body = json.loads(response.body)
        assert body["overwrite"] is True
        assert body["dashboard"]["title"] == "Media Stack"
        mock_metrics.get_grafana_dashboard.assert_called_once_with()

    @patch("media_stack.api.routes.misc_gets.metrics_svc")
    def test_post_returns_method_not_allowed(self, _mock_metrics) -> None:
        harness = RouteDispatchHarness.with_default_router()
        from media_stack.api.routing import DispatchOutcome
        outcome, _ = harness.try_dispatch("POST", "/api/grafana.json")
        assert outcome == DispatchOutcome.METHOD_NOT_ALLOWED


class TestOpenapiJsonRoute:
    """``GET /api/openapi.json`` — live spec parsed to JSON with the
    runtime ``servers`` list grafted in. The strategy is constructed
    in ``MiscGetsGetRoutes.__init__`` with ``_OPENAPI_YAML`` +
    ``_build_openapi_servers`` bound at startup. We patch the YAML
    source + servers builder on the strategy instance the live
    Router constructed."""

    def _patch_strategy(self, harness, yaml_src, servers):
        """Locate the ``MiscGetsGetRoutes`` instance the Router
        built and rebind its ``_spec_dumper`` strategy state."""
        from media_stack.api.routes.misc_gets import MiscGetsGetRoutes
        for spec in harness._dispatcher._router.registered_routes():
            handler = spec.handler
            module = getattr(handler, "__self__", None)
            if isinstance(module, MiscGetsGetRoutes):
                module._spec_dumper._yaml_source = yaml_src
                module._spec_dumper._servers_builder = lambda: servers
                return module
        raise AssertionError("MiscGetsGetRoutes not registered")

    def test_returns_parsed_spec_with_runtime_servers(self) -> None:
        harness = RouteDispatchHarness.with_default_router()
        yaml_src = (
            "openapi: 3.0.3\n"
            "info:\n"
            "  title: Test\n"
            "  version: '1.0.0'\n"
            "paths: {}\n"
        )
        runtime_servers = [
            {"url": "/", "description": "Current host (auto-detected)"},
        ]
        self._patch_strategy(harness, yaml_src, runtime_servers)

        response = harness.dispatch("GET", "/api/openapi.json")
        assert response.status == 200
        assert response.content_type == "application/json"
        body = json.loads(response.body)
        assert body["openapi"] == "3.0.3"
        assert body["info"]["title"] == "Test"
        assert body["servers"] == runtime_servers

    def test_yaml_parse_error_falls_back_to_legacy_stub(self) -> None:
        """A YAML parse error should NOT take ``/api/docs`` down —
        the strategy falls back to the handler's
        ``_get_openapi_spec`` stub instead."""
        harness = RouteDispatchHarness.with_default_router()
        # Inject deliberately broken YAML so safe_load raises.
        bad_yaml = "openapi: 3.0.3\n: : : invalid"
        self._patch_strategy(harness, bad_yaml, [])

        from tests.unit.api.routes._helpers import MockControllerHandler

        class _StubHandler(MockControllerHandler):
            def _get_openapi_spec(self):
                return {"openapi": "3.0.0", "info": {"title": "stub"}}

        # The harness builds its own MockControllerHandler so we
        # can't inject the stubbed subclass through it directly;
        # invoke the route's bound method on a stubbed handler.
        from media_stack.api.routes.misc_gets import MiscGetsGetRoutes
        for spec in harness._dispatcher._router.registered_routes():
            module = getattr(spec.handler, "__self__", None)
            if isinstance(module, MiscGetsGetRoutes):
                handler = _StubHandler(path="/api/openapi.json")
                module.handle_openapi_json(handler)
                assert handler.captured.status == 200
                body = json.loads(handler.captured.body)
                assert body["info"]["title"] == "stub"
                return
        raise AssertionError("MiscGetsGetRoutes not registered")


class TestOpenapiYamlRoute:
    """``GET /api/openapi.yaml`` — same spec re-emitted as YAML
    (NOT JSON). Returns ``text/yaml; charset=utf-8`` via
    ``_raw_response``.
    """

    def _patch_strategy(self, harness, yaml_src, servers):
        from media_stack.api.routes.misc_gets import MiscGetsGetRoutes
        for spec in harness._dispatcher._router.registered_routes():
            module = getattr(spec.handler, "__self__", None)
            if isinstance(module, MiscGetsGetRoutes):
                module._spec_dumper._yaml_source = yaml_src
                module._spec_dumper._servers_builder = lambda: servers
                return module
        raise AssertionError("MiscGetsGetRoutes not registered")

    def test_returns_yaml_content_type(self) -> None:
        harness = RouteDispatchHarness.with_default_router()
        yaml_src = (
            "openapi: 3.0.3\n"
            "info:\n"
            "  title: Test\n"
            "  version: '1.0.0'\n"
            "paths: {}\n"
        )
        self._patch_strategy(harness, yaml_src, [{"url": "/"}])

        response = harness.dispatch("GET", "/api/openapi.yaml")
        assert response.status == 200
        assert response.content_type == "text/yaml; charset=utf-8"

    def test_yaml_body_parses_back_to_dict_with_servers(self) -> None:
        """Re-emitted YAML should round-trip cleanly + contain the
        runtime ``servers`` list grafted in by the strategy."""
        harness = RouteDispatchHarness.with_default_router()
        yaml_src = (
            "openapi: 3.0.3\n"
            "info:\n"
            "  title: Test\n"
            "  version: '1.0.0'\n"
            "paths: {}\n"
        )
        runtime_servers = [{"url": "/", "description": "Auto"}]
        self._patch_strategy(harness, yaml_src, runtime_servers)

        response = harness.dispatch("GET", "/api/openapi.yaml")
        assert response.status == 200
        parsed = yaml.safe_load(response.body.decode("utf-8"))
        assert parsed["openapi"] == "3.0.3"
        assert parsed["servers"] == runtime_servers

    def test_yaml_parse_error_falls_back_to_raw_source(self) -> None:
        """If the YAML source is malformed and parsing fails, the
        strategy returns the raw source unchanged so a YAML break
        doesn't take the docs page down."""
        harness = RouteDispatchHarness.with_default_router()
        broken_yaml = "openapi: 3.0.3\n: : : invalid"
        self._patch_strategy(harness, broken_yaml, [])

        response = harness.dispatch("GET", "/api/openapi.yaml")
        assert response.status == 200
        assert response.content_type == "text/yaml; charset=utf-8"
        assert response.body == broken_yaml.encode("utf-8")


class TestSnapshotDiffRoute:
    """``GET /api/snapshot-diff`` — diff between two config
    snapshots. The route reads ``a`` + ``b`` off the unstripped
    ``handler.path`` query string and delegates to
    ``ops_svc.diff_snapshots``.
    """

    @patch("media_stack.api.routes.misc_gets.ops_svc")
    def test_returns_diff_payload(self, mock_ops) -> None:
        mock_ops.diff_snapshots.return_value = {
            "added": ["sonarr.yml"],
            "removed": [],
            "changed": ["radarr.yml"],
        }
        harness = RouteDispatchHarness.with_default_router()
        response = harness.dispatch("GET", "/api/snapshot-diff")

        assert response.status == 200
        body = json.loads(response.body)
        assert body["added"] == ["sonarr.yml"]
        assert body["changed"] == ["radarr.yml"]
        # No query string -> empty filenames.
        mock_ops.diff_snapshots.assert_called_once_with("", "")

    @patch("media_stack.api.routes.misc_gets.ops_svc")
    def test_passes_query_params_a_and_b_through(
        self, mock_ops,
    ) -> None:
        """The dispatcher strips ``?a=…&b=…`` before route match,
        but ``handler.path`` retains it. The route reads off the
        unstripped form so the snapshot filenames flow through."""
        mock_ops.diff_snapshots.return_value = {
            "added": [], "removed": [], "changed": [],
        }
        from media_stack.api.routes.misc_gets import MiscGetsGetRoutes
        from tests.unit.api.routes._helpers import MockControllerHandler

        instance = MiscGetsGetRoutes()
        handler = MockControllerHandler(
            path=(
                "/api/snapshot-diff"
                "?a=snapshot-20260406T120000.json"
                "&b=snapshot-20260407T120000.json"
            ),
        )
        instance.handle_snapshot_diff(handler)

        assert handler.captured.status == 200
        mock_ops.diff_snapshots.assert_called_once_with(
            "snapshot-20260406T120000.json",
            "snapshot-20260407T120000.json",
        )


class TestQueryStringParser:
    """Direct unit tests for the ``_QueryStringParser`` strategy.
    The parser is the only part of the snapshot-diff handler that
    has interesting branching, so it's worth exercising directly
    rather than only through the route harness."""

    def test_no_query_string_returns_empty(self) -> None:
        from media_stack.api.routes.misc_gets import _QueryStringParser
        assert _QueryStringParser().parse("/api/snapshot-diff") == {}

    def test_parses_two_keys(self) -> None:
        from media_stack.api.routes.misc_gets import _QueryStringParser
        params = _QueryStringParser().parse(
            "/api/snapshot-diff?a=foo.json&b=bar.json",
        )
        assert params == {"a": "foo.json", "b": "bar.json"}

    def test_skips_segments_without_equals(self) -> None:
        """A bare ``flag`` segment (no ``=``) is silently dropped —
        same shape the legacy ``_handle_snapshot_diff`` had."""
        from media_stack.api.routes.misc_gets import _QueryStringParser
        params = _QueryStringParser().parse(
            "/api/snapshot-diff?a=foo.json&flag&b=bar.json",
        )
        assert params == {"a": "foo.json", "b": "bar.json"}


class TestRoutingIntegration:
    """Pin auto-discovery + spec-parity behaviour for the misc-gets
    domain. If a future change accidentally drops a handler from
    the registry, this test fires before any per-route test does.
    """

    def test_all_misc_gets_routes_registered(self) -> None:
        harness = RouteDispatchHarness.with_default_router()
        expected = {
            "/api/events",
            "/api/grafana.json",
            "/api/openapi.json",
            "/api/openapi.yaml",
            "/api/snapshot-diff",
        }
        registered = {
            r.path
            for r in harness._dispatcher._router.registered_routes()
            if r.path in expected
        }
        assert registered == expected, (
            f"Missing misc-gets routes: {expected - registered}"
        )

    def test_post_to_events_returns_method_not_allowed(self) -> None:
        harness = RouteDispatchHarness.with_default_router()
        from media_stack.api.routing import DispatchOutcome
        outcome, _ = harness.try_dispatch("POST", "/api/events")
        assert outcome == DispatchOutcome.METHOD_NOT_ALLOWED

    def test_post_to_openapi_yaml_returns_method_not_allowed(
        self,
    ) -> None:
        harness = RouteDispatchHarness.with_default_router()
        from media_stack.api.routing import DispatchOutcome
        outcome, _ = harness.try_dispatch("POST", "/api/openapi.yaml")
        assert outcome == DispatchOutcome.METHOD_NOT_ALLOWED

    def test_post_to_snapshot_diff_returns_method_not_allowed(
        self,
    ) -> None:
        harness = RouteDispatchHarness.with_default_router()
        from media_stack.api.routing import DispatchOutcome
        outcome, _ = harness.try_dispatch("POST", "/api/snapshot-diff")
        assert outcome == DispatchOutcome.METHOD_NOT_ALLOWED
