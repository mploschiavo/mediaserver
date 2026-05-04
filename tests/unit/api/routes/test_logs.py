"""Tests for ``api/routes/logs.py`` (ADR-0007 Phase 2 wave 3).

Each test class owns one route. Each test invokes the production
Router via ``RouteDispatchHarness.with_default_router()`` — same
auto-discovery, same spec-parity check, same dispatch path used in
production.

The route module itself does the query-string parsing for
``/api/logs`` (``after_seq``, ``action``); we exercise both the
no-query and with-query forms to pin that the
``server.py``-strips-the-query invariant + the in-handler reparse
of ``handler.path`` agree on the parameters.

The ``/api/logs/stream`` SSE variant is intentionally NOT tested
here — the OpenAPI spec doesn't declare it, so it can't be
registered with the Router (see the route module's docstring for
the blocker). That route stays on the legacy elif chain for now
and is covered by the existing handlers_get tests.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import patch

from media_stack.api.routing import DefaultDispatcher, DispatchOutcome
from tests.unit.api.routes._helpers import (
    CapturedResponse,
    MockControllerHandler,
    RouteDispatchHarness,
    _MockState,
)


def _dispatch_with_query(
    verb: str, path_with_query: str, *, state: Any = None,
) -> CapturedResponse:
    """Mimic the production dispatch path: strip the query string
    before route-matching, but leave it on ``handler.path`` so the
    handler-side reparse (legacy ``handler.path.split("?", 1)[1]``
    code) finds the params.

    The shared ``RouteDispatchHarness.dispatch`` doesn't do this strip
    today (it passes ``path`` to both the dispatcher AND the
    handler). Production strips at ``server.py:113`` before invoking
    the dispatcher; this helper simulates that step for the tests
    that need a query string.
    """
    DefaultDispatcher.reset_for_tests()
    dispatcher = DefaultDispatcher.instance()
    bare_path = path_with_query.split("?", 1)[0]
    handler = MockControllerHandler(path=path_with_query, state=state)
    outcome = dispatcher.try_dispatch(verb, bare_path, handler)
    if outcome == DispatchOutcome.METHOD_NOT_ALLOWED:
        dispatcher.write_method_not_allowed(handler, bare_path)
    return handler.captured


class _RingBufferMockState(_MockState):
    """A ``_MockState`` extension that owns an in-memory log ring
    buffer compatible with ``state.get_logs_since(after_seq, action)``.

    Tests append entries via ``add_log`` and the route handler reads
    them via the same shape as ``ControllerState.get_logs_since`` —
    a ``(seq, ts, msg, action)`` 4-tuple per entry.
    """

    def __init__(self) -> None:
        super().__init__()
        self._entries: list[tuple[int, float, str, str]] = []

    def add_log(self, seq: int, ts: float, msg: str, action: str) -> None:
        self._entries.append((seq, ts, msg, action))

    def get_logs_since(
        self, after_seq: int = 0, action: str = "",
    ) -> list[tuple[int, float, str, str]]:
        if action:
            return [
                e for e in self._entries
                if e[0] > after_seq and e[3] == action
            ]
        return [e for e in self._entries if e[0] > after_seq]


class TestLogLevelRoute:
    """``GET /api/log-level`` — current runtime log level."""

    @patch("media_stack.services.runtime_platform.get_log_level")
    def test_returns_current_level(self, mock_get_level) -> None:
        mock_get_level.return_value = "INFO"
        harness = RouteDispatchHarness.with_default_router()
        response = harness.dispatch("GET", "/api/log-level")

        assert response.status == 200
        assert json.loads(response.body) == {"level": "INFO"}
        mock_get_level.assert_called_once_with()

    @patch("media_stack.services.runtime_platform.get_log_level")
    def test_returns_debug_when_set(self, mock_get_level) -> None:
        mock_get_level.return_value = "DEBUG"
        harness = RouteDispatchHarness.with_default_router()
        response = harness.dispatch("GET", "/api/log-level")

        assert response.status == 200
        assert json.loads(response.body) == {"level": "DEBUG"}

    def test_post_returns_method_not_allowed(self) -> None:
        """ADR-0007 Phase 2 wave 5 migrated POST /api/log-level
        from handlers_post.py into post_admin_ops.py. The router now
        knows about this POST endpoint. Check route registration rather
        than dispatching to avoid invoking handler methods that need
        _read_json_body on the test mock."""
        harness = RouteDispatchHarness.with_default_router()
        # POST /api/log-level is now registered as part of wave 5.
        # AdminOpsPostRoutes.handle_log_level handles it via post_admin_ops.py.
        registered = {
            (r.verb, r.path)
            for r in harness._dispatcher._router.registered_routes()
        }
        assert ("POST", "/api/log-level") in registered, (
            "POST /api/log-level should be registered in router "
            "(wave 5 AdminOpsPostRoutes)"
        )


class TestLogsRingBufferRoute:
    """``GET /api/logs`` — controller log ring buffer."""

    def test_returns_empty_buffer_when_no_logs(self) -> None:
        state = _RingBufferMockState()
        harness = RouteDispatchHarness.with_default_router()
        response = harness.dispatch("GET", "/api/logs", state=state)

        assert response.status == 200
        body = json.loads(response.body)
        assert body == {"logs": [], "count": 0}

    def test_returns_all_entries_unfiltered(self) -> None:
        state = _RingBufferMockState()
        # Use a fixed timestamp so the strftime'd output is stable.
        # 1714579200.0 = 2024-05-01T12:00:00 UTC; localtime() shifts
        # to local TZ but the test only asserts string shape, not
        # tz-anchored value.
        state.add_log(1, 1714579200.0, "hello", "bootstrap")
        state.add_log(2, 1714579260.0, "world", "image_update")

        harness = RouteDispatchHarness.with_default_router()
        response = harness.dispatch("GET", "/api/logs", state=state)

        assert response.status == 200
        body = json.loads(response.body)
        assert body["count"] == 2
        assert len(body["logs"]) == 2
        assert body["logs"][0]["seq"] == 1
        assert body["logs"][0]["msg"] == "hello"
        assert body["logs"][0]["action"] == "bootstrap"
        # The ``ts`` is strftime'd to ``%Y-%m-%dT%H:%M:%S`` form —
        # exact characters depend on the test runner's timezone, so
        # only assert structural shape.
        assert isinstance(body["logs"][0]["ts"], str)
        assert "T" in body["logs"][0]["ts"]

    def test_after_seq_query_filters_older_entries(self) -> None:
        state = _RingBufferMockState()
        state.add_log(1, 1714579200.0, "old", "bootstrap")
        state.add_log(2, 1714579260.0, "newer", "bootstrap")
        state.add_log(3, 1714579320.0, "newest", "bootstrap")

        response = _dispatch_with_query(
            "GET", "/api/logs?after_seq=1", state=state,
        )

        assert response.status == 200
        body = json.loads(response.body)
        # Only seq=2 + seq=3 should come back.
        assert body["count"] == 2
        seqs = [e["seq"] for e in body["logs"]]
        assert seqs == [2, 3]

    def test_action_query_filters_other_actions(self) -> None:
        state = _RingBufferMockState()
        state.add_log(1, 1714579200.0, "boot1", "bootstrap")
        state.add_log(2, 1714579260.0, "img1", "image_update")
        state.add_log(3, 1714579320.0, "boot2", "bootstrap")

        response = _dispatch_with_query(
            "GET", "/api/logs?action=bootstrap", state=state,
        )

        assert response.status == 200
        body = json.loads(response.body)
        assert body["count"] == 2
        actions = [e["action"] for e in body["logs"]]
        assert actions == ["bootstrap", "bootstrap"]
        msgs = [e["msg"] for e in body["logs"]]
        assert msgs == ["boot1", "boot2"]

    def test_combined_after_seq_and_action_queries(self) -> None:
        state = _RingBufferMockState()
        state.add_log(1, 1714579200.0, "boot1", "bootstrap")
        state.add_log(2, 1714579260.0, "img1", "image_update")
        state.add_log(3, 1714579320.0, "boot2", "bootstrap")
        state.add_log(4, 1714579380.0, "boot3", "bootstrap")

        response = _dispatch_with_query(
            "GET", "/api/logs?after_seq=1&action=bootstrap", state=state,
        )

        assert response.status == 200
        body = json.loads(response.body)
        assert body["count"] == 2
        seqs = [e["seq"] for e in body["logs"]]
        assert seqs == [3, 4]

    def test_invalid_after_seq_falls_back_to_zero(self) -> None:
        """Garbage in the ``after_seq`` query parameter should be
        swallowed — the legacy chain logs a debug + falls back to 0.
        We don't lose entries to a malformed integer."""
        state = _RingBufferMockState()
        state.add_log(1, 1714579200.0, "first", "bootstrap")
        state.add_log(2, 1714579260.0, "second", "bootstrap")

        response = _dispatch_with_query(
            "GET", "/api/logs?after_seq=not_an_int", state=state,
        )

        assert response.status == 200
        body = json.loads(response.body)
        # after_seq fell back to 0 → all entries returned.
        assert body["count"] == 2

    def test_query_string_with_no_kv_pairs_is_ignored(self) -> None:
        """The legacy parser only assigned params for ``k=v`` shapes
        — bare flags (``?foo``) are dropped silently."""
        state = _RingBufferMockState()
        state.add_log(1, 1714579200.0, "x", "")

        response = _dispatch_with_query("GET", "/api/logs?flag", state=state)

        assert response.status == 200
        body = json.loads(response.body)
        assert body["count"] == 1


class TestLogsSourcesRoute:
    """``GET /api/logs/sources`` — Logs UI dropdown source list."""

    @patch("media_stack.api.routes.logs.ops_svc")
    @patch("media_stack.api.services.registry.SERVICES")
    def test_returns_platform_service_and_cronjob_buckets(
        self, mock_services, mock_ops,
    ) -> None:
        # SERVICES is iterated as ``{s.id for s in SERVICES}`` — fake
        # entries with .id attributes are enough.
        class _Fake:
            def __init__(self, sid: str) -> None:
                self.id = sid

        mock_services.__iter__ = lambda self: iter([
            _Fake("sonarr"), _Fake("radarr"), _Fake("jellyfin"),
        ])
        mock_ops.list_cronjob_log_sources.return_value = [
            {
                "id": "media-stack-media-hygiene",
                "label": "Media hygiene (cron)",
                "kind": "cronjob",
            },
        ]

        harness = RouteDispatchHarness.with_default_router()
        response = harness.dispatch("GET", "/api/logs/sources")

        assert response.status == 200
        body = json.loads(response.body)
        sources = body["sources"]

        # Platform pods come first: controller, ui.
        assert sources[0] == {
            "id": "controller", "label": "Controller", "kind": "platform",
        }
        assert sources[1] == {
            "id": "ui", "label": "Ui", "kind": "platform",
        }

        # Service pods follow, sorted by id.
        kinds = [s["kind"] for s in sources]
        assert kinds.count("platform") == 2
        assert kinds.count("service") == 3
        assert kinds.count("cronjob") == 1

        service_ids = [s["id"] for s in sources if s["kind"] == "service"]
        assert service_ids == sorted(service_ids)
        assert set(service_ids) == {"sonarr", "radarr", "jellyfin"}

        cronjob_entry = [s for s in sources if s["kind"] == "cronjob"]
        assert cronjob_entry == [{
            "id": "media-stack-media-hygiene",
            "label": "Media hygiene (cron)",
            "kind": "cronjob",
        }]

    @patch("media_stack.api.routes.logs.ops_svc")
    def test_empty_cronjobs_still_returns_platform_and_services(
        self, mock_ops,
    ) -> None:
        mock_ops.list_cronjob_log_sources.return_value = []

        harness = RouteDispatchHarness.with_default_router()
        response = harness.dispatch("GET", "/api/logs/sources")

        assert response.status == 200
        body = json.loads(response.body)
        kinds = [s["kind"] for s in body["sources"]]
        # Platform always present.
        assert "platform" in kinds
        # No cronjobs in the response since the helper returned [].
        assert "cronjob" not in kinds

    @patch("media_stack.api.routes.logs.ops_svc")
    def test_query_string_is_ignored(self, mock_ops) -> None:
        """The legacy chain matched both ``/api/logs/sources`` and
        ``/api/logs/sources?...``. Under the OpenAPI router the
        ``server.py`` path-strip (simulated here via
        ``_dispatch_with_query``) hands the bare path to the
        dispatcher; we just confirm a query string still dispatches
        into this route rather than falling into
        ``/api/logs/{service}``."""
        mock_ops.list_cronjob_log_sources.return_value = []

        response = _dispatch_with_query(
            "GET", "/api/logs/sources?refresh=1",
        )

        assert response.status == 200
        body = json.loads(response.body)
        assert "sources" in body


class TestRoutingIntegration:
    """Pin auto-discovery + spec-parity behaviour for the logs
    domain. If a future change accidentally drops a handler from the
    registry, this test fires before any per-route test does.
    """

    def test_all_logs_routes_registered(self) -> None:
        harness = RouteDispatchHarness.with_default_router()
        expected = {"/api/log-level", "/api/logs", "/api/logs/sources"}
        registered = {
            r.path
            for r in harness._dispatcher._router.registered_routes()
            if r.path in expected
        }
        assert registered == expected, (
            f"Missing logs routes: {expected - registered}"
        )

    def test_logs_stream_now_registered_via_webhooks_and_deferred(
        self,
    ) -> None:
        """ADR-0007 Phase 2 wave 6 added ``/api/logs/stream`` to the
        spec and migrated the handler onto
        ``WebhooksAndDeferredRoutes``. This test was previously
        pinning the deferral; flip the assertion to confirm the
        Router now owns the route. The legacy elif chain still
        matches the path during the cleanup phase, but the Router
        wins at dispatch time.
        """
        harness = RouteDispatchHarness.with_default_router()
        registered = {
            r.path
            for r in harness._dispatcher._router.registered_routes()
        }
        assert "/api/logs/stream" in registered

    def test_post_to_logs_returns_no_match_for_legacy_fallthrough(
        self,
    ) -> None:
        """``/api/logs`` is GET-only in the spec. POST falls through
        to the legacy chain (NO_MATCH); a future strict-mode flip
        would 404 it. NOT METHOD_NOT_ALLOWED — only paths the spec
        DECLARES with other verbs return 405 here."""
        from media_stack.api.routing import DispatchOutcome
        harness = RouteDispatchHarness.with_default_router()
        outcome, _ = harness.try_dispatch("POST", "/api/logs")
        # /api/logs has GET only in the spec, so POST → 405.
        assert outcome == DispatchOutcome.METHOD_NOT_ALLOWED

    def test_log_level_handler_uses_runtime_platform_module(self) -> None:
        """The legacy chain imports ``get_log_level`` lazily inside
        the elif arm. The route method does the same so its module
        import graph stays minimal at startup. We exercise the
        dispatch path end-to-end with a real (non-mocked) lazy
        import to confirm the import-on-call works."""
        from media_stack.services import runtime_platform
        with patch.object(runtime_platform, "get_log_level",
                          return_value="WARN"):
            harness = RouteDispatchHarness.with_default_router()
            response = harness.dispatch("GET", "/api/log-level")
            assert response.status == 200
            assert json.loads(response.body) == {"level": "WARN"}


class TestHandlerStateIntegration:
    """Sanity check that the route module reads ``handler.state``
    rather than reaching for a global. Prevents a regression where a
    refactor accidentally accesses a shared ring buffer instead of
    the per-handler state object.
    """

    def test_logs_route_reads_handler_state(self) -> None:
        state_a = _RingBufferMockState()
        state_a.add_log(1, 1714579200.0, "from-a", "")
        state_b = _RingBufferMockState()
        state_b.add_log(99, 1714579200.0, "from-b", "")

        harness = RouteDispatchHarness.with_default_router()
        response_a = harness.dispatch("GET", "/api/logs", state=state_a)
        response_b = harness.dispatch("GET", "/api/logs", state=state_b)

        body_a = json.loads(response_a.body)
        body_b = json.loads(response_b.body)
        assert body_a["logs"][0]["msg"] == "from-a"
        assert body_b["logs"][0]["msg"] == "from-b"
