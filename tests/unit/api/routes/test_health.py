"""Tests for ``api/routes/health.py`` (ADR-0007 Phase 1
proof-of-pattern).

Mirrors the shape Phase 2 agents copy verbatim for their domain.
Each test class owns one route. Each test invokes the production
Router via ``RouteDispatchHarness.with_default_router()`` — same
auto-discovery, same spec parity check, same dispatch path.

The handler bodies do real work (call into ``health_svc``,
``crashloop_svc``, etc.); these tests mock those services where
needed and assert the response shape. They don't re-test the
service logic itself — that lives in the per-service test files.
"""

from __future__ import annotations

import json
from unittest.mock import patch

from tests.unit.api.routes._helpers import RouteDispatchHarness


class TestHealthzReadyz:
    """Probes — the simplest routes. No service mocks; pure status
    response."""

    def test_healthz_returns_ok(self) -> None:
        harness = RouteDispatchHarness.with_default_router()
        response = harness.dispatch("GET", "/healthz")
        assert response.status == 200
        assert json.loads(response.body) == {"status": "ok"}

    def test_readyz_returns_bootstrap_state(self) -> None:
        harness = RouteDispatchHarness.with_default_router()
        response = harness.dispatch("GET", "/readyz")
        assert response.status == 200
        body = json.loads(response.body)
        assert body["status"] == "ready"
        assert "initial_bootstrap_done" in body
        assert "phase" in body


class TestApiHealth:
    """``/api/health`` runs a real probe sweep + appends history.
    We mock the underlying service to avoid hitting the network."""

    @patch("media_stack.api.services.health.append_health_history")
    @patch("media_stack.api.services.health.probe_services")
    def test_api_health_returns_probe_result(
        self, mock_probe, mock_append,
    ) -> None:
        mock_probe.return_value = {
            "services": {"jellyfin": {"status": "ok"}},
        }
        harness = RouteDispatchHarness.with_default_router()
        response = harness.dispatch("GET", "/api/health")
        assert response.status == 200
        body = json.loads(response.body)
        assert body == {"services": {"jellyfin": {"status": "ok"}}}
        mock_append.assert_called_once_with({"jellyfin": {"status": "ok"}})

    @patch("media_stack.api.services.health.get_health_history")
    def test_api_health_history_returns_history(self, mock_history) -> None:
        mock_history.return_value = [{"t": 1, "services": {}}]
        harness = RouteDispatchHarness.with_default_router()
        response = harness.dispatch("GET", "/api/health-history")
        assert response.status == 200
        assert json.loads(response.body) == [{"t": 1, "services": {}}]


class TestOpsHealth:
    @patch("media_stack.api.services.health.get_ops_health")
    def test_ops_health_returns_aggregated_stats(
        self, mock_ops,
    ) -> None:
        mock_ops.return_value = {"bootstrap_at": 1700000000.0}
        harness = RouteDispatchHarness.with_default_router()
        response = harness.dispatch("GET", "/api/ops/health")
        assert response.status == 200
        assert json.loads(response.body) == {
            "bootstrap_at": 1700000000.0,
        }


class TestHealthConfigIntegrity:
    @patch(
        "media_stack.api.services.config_integrity.check_all",
    )
    def test_returns_services_and_timestamp(self, mock_check) -> None:
        mock_check.return_value = {"jellyfin": {"ok": True}}
        harness = RouteDispatchHarness.with_default_router()
        response = harness.dispatch(
            "GET", "/api/health/config-integrity",
        )
        assert response.status == 200
        body = json.loads(response.body)
        assert body["services"] == {"jellyfin": {"ok": True}}
        assert "checked_at" in body


class TestHealthCrashloops:
    @patch(
        "media_stack.api.services.crashloop"
        ".list_non_registry_problem_pods",
    )
    @patch(
        "media_stack.api.services.crashloop.check_all",
    )
    def test_returns_services_and_non_registry_pods(
        self, mock_check, mock_non_registry,
    ) -> None:
        mock_check.return_value = {"sonarr": {"crashloop": False}}
        mock_non_registry.return_value = []
        harness = RouteDispatchHarness.with_default_router()
        response = harness.dispatch("GET", "/api/health/crashloops")
        assert response.status == 200
        body = json.loads(response.body)
        assert body["services"] == {"sonarr": {"crashloop": False}}
        assert body["non_registry_pods"] == []
        assert "checked_at" in body


class TestHealthStories:
    @patch(
        "media_stack.api.services.health_stories.compose_live",
    )
    def test_returns_composed_stories(self, mock_compose) -> None:
        mock_compose.return_value = [{"title": "All systems nominal"}]
        harness = RouteDispatchHarness.with_default_router()
        response = harness.dispatch("GET", "/api/health/stories")
        assert response.status == 200
        assert json.loads(response.body) == [
            {"title": "All systems nominal"},
        ]


class TestRoutingIntegration:
    """Pin the auto-discovery + spec-parity behavior for the
    health domain. If a future change accidentally drops a
    handler from the registry, this fires before any per-route
    test does."""

    def test_all_health_routes_registered(self) -> None:
        harness = RouteDispatchHarness.with_default_router()
        expected = {
            "/healthz", "/readyz", "/api/health", "/api/health-history",
            "/api/ops/health", "/api/health/config-integrity",
            "/api/health/crashloops", "/api/health/stories",
        }
        registered = {
            r.path for r in harness._dispatcher._router.registered_routes()
            if r.path in expected
        }
        assert registered == expected, (
            f"Missing health routes: {expected - registered}"
        )

    def test_post_to_get_only_path_returns_method_not_allowed(
        self,
    ) -> None:
        # Path exists in spec for GET; POST should be 405.
        harness = RouteDispatchHarness.with_default_router()
        outcome, response = harness.try_dispatch("POST", "/healthz")
        from media_stack.api.routing import DispatchOutcome
        assert outcome == DispatchOutcome.METHOD_NOT_ALLOWED
