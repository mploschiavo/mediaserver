"""Tests for ``api/routes/schedules.py`` (ADR-0007 Phase 2
wave 6 cleanup — re-homed from ``misc_legacy.py``).

Each test invokes the production Router via
``RouteDispatchHarness.with_default_router()`` — same
auto-discovery, same spec-parity check, same dispatch path used
in production.

The handler body is a thin pass-through to the scheduler service
singleton; these tests mock the singleton and assert the response
shape. The service logic itself is covered by the per-service
test files.
"""

from __future__ import annotations

import json
from unittest.mock import patch

from tests.unit.api.routes._helpers import RouteDispatchHarness


class TestApiSchedules:
    """``/api/schedules`` — list configured recurring schedules.
    Pass-through to ``sched_svc.get_schedules``."""

    @patch("media_stack.api.services.scheduler.get_schedules")
    def test_returns_schedule_list(self, mock_schedules) -> None:
        mock_schedules.return_value = {
            "count": 1,
            "schedules": [
                {
                    "id": 1776991803778,
                    "action": "run-media-hygiene",
                    "label": "Auto-cleanup",
                    "interval_seconds": 3600,
                    "created_at": 1776991803.7784,
                    "last_run": 1777136520.8929,
                },
            ],
        }
        harness = RouteDispatchHarness.with_default_router()
        response = harness.dispatch("GET", "/api/schedules")
        assert response.status == 200
        body = json.loads(response.body)
        assert body["count"] == 1
        assert body["schedules"][0]["action"] == "run-media-hygiene"
        mock_schedules.assert_called_once_with()

    @patch("media_stack.api.services.scheduler.get_schedules")
    def test_returns_empty_schedule_list(self, mock_schedules) -> None:
        mock_schedules.return_value = {"count": 0, "schedules": []}
        harness = RouteDispatchHarness.with_default_router()
        response = harness.dispatch("GET", "/api/schedules")
        assert response.status == 200
        body = json.loads(response.body)
        assert body == {"count": 0, "schedules": []}

    @patch("media_stack.api.services.scheduler.get_schedules")
    def test_passes_through_arbitrary_payload_keys(
        self, mock_schedules,
    ) -> None:
        # The OpenAPI spec types this response as
        # ``additionalProperties: true``, so the handler must NOT
        # filter the service's payload — whatever ``get_schedules``
        # returns flows through unchanged.
        mock_schedules.return_value = {
            "count": 0,
            "schedules": [],
            "next_run_at": 1777137000.0,
            "tz": "America/New_York",
        }
        harness = RouteDispatchHarness.with_default_router()
        response = harness.dispatch("GET", "/api/schedules")
        body = json.loads(response.body)
        assert body["next_run_at"] == 1777137000.0
        assert body["tz"] == "America/New_York"


class TestRoutingIntegration:
    """Pin the auto-discovery + spec-parity behavior for the
    schedules domain. If a future change accidentally drops the
    handler from the registry, this fires before any per-route
    test does."""

    def test_schedules_route_registered_by_schedules_module(
        self,
    ) -> None:
        from media_stack.api.routes.schedules import SchedulesGetRoutes
        harness = RouteDispatchHarness.with_default_router()
        match = next(
            (
                r
                for r in harness._dispatcher._router.registered_routes()
                if r.path == "/api/schedules" and r.verb == "GET"
            ),
            None,
        )
        assert match is not None, "/api/schedules not registered"
        # ``CompiledRoute.handler`` is the bound method; its
        # ``__self__`` is the RouteModule instance the Router
        # owns. Confirm that instance is THIS module's class so
        # we don't silently let a different domain claim the path.
        assert isinstance(match.handler.__self__, SchedulesGetRoutes)

    def test_post_to_schedules_not_handled_by_get_module(
        self,
    ) -> None:
        # ``/api/schedules`` ALSO has a POST in the spec (handled
        # by ``handlers_post``, not migrated to the Router yet).
        # The GET-only module here MUST NOT claim the POST verb —
        # which would either route through the wrong handler or
        # shadow the legacy POST chain. Until a future wave
        # migrates POST, the Router for POST must report
        # ``NO_MATCH`` (legacy chain handles it) — NOT ``HANDLED``
        # and NOT ``METHOD_NOT_ALLOWED`` (which would mean only
        # GET is in the verb table).
        harness = RouteDispatchHarness.with_default_router()
        outcome, _response = harness.try_dispatch(
            "POST", "/api/schedules",
        )
        from media_stack.api.routing import DispatchOutcome
        assert outcome == DispatchOutcome.NO_MATCH, (
            f"Expected POST /api/schedules to fall through to the "
            f"legacy chain (NO_MATCH); got {outcome}"
        )
