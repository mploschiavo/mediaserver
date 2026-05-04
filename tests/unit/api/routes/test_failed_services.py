"""Tests for ``api/routes/failed_services.py`` (ADR-0007 Phase 2).

Each test class owns one route. Each test invokes the production
Router via ``RouteDispatchHarness.with_default_router()`` — same
auto-discovery, same spec-parity check, same dispatch path used
in production.

``/api/failed-services`` reads from ``handler.state``; tests pass
a ``ControllerStateStub`` via the harness's ``state=`` kwarg.
``/api/auto-heal`` calls the ``services/auto_heal.py`` module
function — tests patch ``status`` to assert the response shape
without exercising the underlying recovery loop.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import patch

from tests.unit.api.routes._helpers import RouteDispatchHarness


@dataclass
class ControllerStateStub:
    """Minimal ``ControllerState``-shaped stub for the
    ``/api/failed-services`` route. Only ``get_failed_services``
    is exercised; the stub returns whatever the test injected."""

    failed_services: dict[str, dict[str, Any]] = field(
        default_factory=dict,
    )

    def get_failed_services(self) -> dict[str, dict[str, Any]]:
        return dict(self.failed_services)


class TestFailedServicesRoute:
    """``/api/failed-services`` returns
    ``{"failed_services": <map>, "count": <int>}``."""

    def test_returns_failed_services_map_and_count(self) -> None:
        harness = RouteDispatchHarness.with_default_router()
        state = ControllerStateStub(failed_services={
            "sonarr": {"reason": "timeout", "attempts": 3},
            "radarr": {"reason": "auth", "attempts": 2},
        })
        response = harness.dispatch(
            "GET", "/api/failed-services", state=state,
        )
        assert response.status == 200
        assert json.loads(response.body) == {
            "failed_services": {
                "sonarr": {"reason": "timeout", "attempts": 3},
                "radarr": {"reason": "auth", "attempts": 2},
            },
            "count": 2,
        }

    def test_returns_empty_map_and_zero_count_when_none_failing(
        self,
    ) -> None:
        harness = RouteDispatchHarness.with_default_router()
        response = harness.dispatch(
            "GET", "/api/failed-services",
            state=ControllerStateStub(),
        )
        assert response.status == 200
        assert json.loads(response.body) == {
            "failed_services": {},
            "count": 0,
        }

    def test_count_matches_distinct_service_keys(self) -> None:
        """``len(get_failed_services())`` counts distinct service
        ids — guards against a refactor that swaps the dict for a
        list-of-events (which would inflate ``count``)."""
        harness = RouteDispatchHarness.with_default_router()
        state = ControllerStateStub(failed_services={
            "jellyfin": {"reason": "503"},
        })
        response = harness.dispatch(
            "GET", "/api/failed-services", state=state,
        )
        body = json.loads(response.body)
        assert body["count"] == 1
        assert list(body["failed_services"].keys()) == ["jellyfin"]


class TestAutoHealRoute:
    """``/api/auto-heal`` returns the auto-heal service status
    snapshot."""

    @patch("media_stack.api.services.auto_heal.status")
    def test_returns_auto_heal_status(self, mock_status) -> None:
        mock_status.return_value = {
            "enabled": True,
            "recent_events": [
                {"service_id": "sonarr", "action": "restart"},
            ],
        }
        harness = RouteDispatchHarness.with_default_router()
        response = harness.dispatch("GET", "/api/auto-heal")
        assert response.status == 200
        assert json.loads(response.body) == {
            "enabled": True,
            "recent_events": [
                {"service_id": "sonarr", "action": "restart"},
            ],
        }
        mock_status.assert_called_once_with()

    @patch("media_stack.api.services.auto_heal.status")
    def test_returns_disabled_status_when_off(self, mock_status) -> None:
        mock_status.return_value = {
            "enabled": False,
            "recent_events": [],
        }
        harness = RouteDispatchHarness.with_default_router()
        response = harness.dispatch("GET", "/api/auto-heal")
        assert response.status == 200
        assert json.loads(response.body) == {
            "enabled": False,
            "recent_events": [],
        }


class TestRoutingIntegration:
    """Pin auto-discovery + spec-parity behavior for the
    failed-services / auto-heal domain. If a future change
    accidentally drops a handler from the registry, this fires
    before any per-route test does."""

    def test_all_failed_services_routes_registered(self) -> None:
        harness = RouteDispatchHarness.with_default_router()
        expected = {"/api/failed-services", "/api/auto-heal"}
        registered = {
            r.path
            for r in harness._dispatcher._router.registered_routes()
            if r.path in expected
        }
        assert registered == expected, (
            f"Missing failed-services routes: {expected - registered}"
        )

    def test_post_to_failed_services_returns_method_not_allowed(
        self,
    ) -> None:
        harness = RouteDispatchHarness.with_default_router()
        outcome, _ = harness.try_dispatch("POST", "/api/failed-services")
        from media_stack.api.routing import DispatchOutcome
        assert outcome == DispatchOutcome.METHOD_NOT_ALLOWED

    def test_post_to_auto_heal_returns_method_not_allowed(
        self,
    ) -> None:
        """``/api/auto-heal/enabled`` and ``/api/auto-heal/run`` are
        POST routes in the spec — but the bare ``/api/auto-heal``
        path is GET-only, so a POST to it must 405 (not silently
        match a sibling)."""
        harness = RouteDispatchHarness.with_default_router()
        outcome, _ = harness.try_dispatch("POST", "/api/auto-heal")
        from media_stack.api.routing import DispatchOutcome
        assert outcome == DispatchOutcome.METHOD_NOT_ALLOWED
