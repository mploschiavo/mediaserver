"""Tests for ``api/routes/state.py`` (ADR-0007 Phase 2).

Each test class owns one route. Each test invokes the production
Router via ``RouteDispatchHarness.with_default_router()`` — same
auto-discovery, same spec-parity check, same dispatch path used
in production.

Handlers in this domain read from ``handler.state``; the harness's
``MockControllerHandler`` ships with a default state stub, but
each test passes a fresh ``ControllerStateStub`` via the harness's
``state=`` kwarg to avoid cross-test pollution.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from tests.unit.api.routes._helpers import RouteDispatchHarness


@dataclass
class ControllerStateStub:
    """Minimal ``ControllerState``-shaped stub for the State
    routes. Each route reads ONE field; the stub gives every test
    a clean slate it can customize per-case."""

    app_status: dict[str, Any] = field(default_factory=dict)
    runtime_config: dict[str, Any] = field(default_factory=dict)
    webhook_urls: list[str] = field(default_factory=list)
    initial_bootstrap_done: bool = True
    phase: str = "ready"
    state_dict: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        if self.state_dict is not None:
            return dict(self.state_dict)
        return {
            "phase": self.phase,
            "app_status": dict(self.app_status),
            "runtime_config": dict(self.runtime_config),
            "webhook_urls": list(self.webhook_urls),
            "initial_bootstrap_done": self.initial_bootstrap_done,
        }


class TestStatusRoute:
    """``/status`` returns the full controller state via
    ``state.to_dict()``."""

    def test_returns_state_dict_verbatim(self) -> None:
        harness = RouteDispatchHarness.with_default_router()
        state = ControllerStateStub(state_dict={
            "phase": "complete",
            "elapsed_seconds": 60.5,
            "app_status": {"sonarr": {"status": "configured"}},
        })
        response = harness.dispatch("GET", "/status", state=state)
        assert response.status == 200
        assert json.loads(response.body) == {
            "phase": "complete",
            "elapsed_seconds": 60.5,
            "app_status": {"sonarr": {"status": "configured"}},
        }


class TestAppsRoute:
    """``/apps`` returns ``{"apps": {<name>: <info>, ...}}``."""

    def test_returns_app_status_map(self) -> None:
        harness = RouteDispatchHarness.with_default_router()
        state = ControllerStateStub(app_status={
            "sonarr": {"status": "configured"},
            "radarr": {"status": "configured"},
        })
        response = harness.dispatch("GET", "/apps", state=state)
        assert response.status == 200
        assert json.loads(response.body) == {
            "apps": {
                "sonarr": {"status": "configured"},
                "radarr": {"status": "configured"},
            },
        }

    def test_returns_empty_apps_map_when_unconfigured(self) -> None:
        harness = RouteDispatchHarness.with_default_router()
        response = harness.dispatch(
            "GET", "/apps", state=ControllerStateStub(),
        )
        assert response.status == 200
        assert json.loads(response.body) == {"apps": {}}


class TestAppByNameRoute:
    """``/apps/{appName}`` — parameterized route. Tests that the
    Router's path-param injection passes ``appName`` through as a
    kwarg AND that the 404 path fires when the app is unknown."""

    def test_returns_app_info_when_known(self) -> None:
        harness = RouteDispatchHarness.with_default_router()
        state = ControllerStateStub(app_status={
            "sonarr": {"status": "configured", "version": "4.0"},
        })
        response = harness.dispatch(
            "GET", "/apps/sonarr", state=state,
        )
        assert response.status == 200
        assert json.loads(response.body) == {
            "sonarr": {"status": "configured", "version": "4.0"},
        }

    def test_returns_404_when_app_unknown(self) -> None:
        harness = RouteDispatchHarness.with_default_router()
        state = ControllerStateStub(app_status={
            "sonarr": {"status": "configured"},
        })
        response = harness.dispatch(
            "GET", "/apps/unknown-app", state=state,
        )
        assert response.status == 404
        assert json.loads(response.body) == {
            "error": "app 'unknown-app' not found",
        }

    def test_returns_404_when_app_status_empty(self) -> None:
        harness = RouteDispatchHarness.with_default_router()
        response = harness.dispatch(
            "GET", "/apps/sonarr", state=ControllerStateStub(),
        )
        assert response.status == 404
        body = json.loads(response.body)
        assert body == {"error": "app 'sonarr' not found"}

    def test_app_name_with_dashes_passes_through(self) -> None:
        """Path params accept any non-slash chars per the Router's
        regex — names with dashes / dots / digits all dispatch
        cleanly."""
        harness = RouteDispatchHarness.with_default_router()
        state = ControllerStateStub(app_status={
            "media-stack-controller": {"status": "configured"},
        })
        response = harness.dispatch(
            "GET", "/apps/media-stack-controller", state=state,
        )
        assert response.status == 200
        body = json.loads(response.body)
        assert body == {
            "media-stack-controller": {"status": "configured"},
        }


class TestConfigRoute:
    """``/config`` returns ``{"config": <runtime_config>}``."""

    def test_returns_runtime_config_snapshot(self) -> None:
        harness = RouteDispatchHarness.with_default_router()
        state = ControllerStateStub(runtime_config={
            "skip_envoy": False, "dry_run": True,
        })
        response = harness.dispatch("GET", "/config", state=state)
        assert response.status == 200
        assert json.loads(response.body) == {
            "config": {"skip_envoy": False, "dry_run": True},
        }

    def test_returns_empty_config_when_no_overrides(self) -> None:
        harness = RouteDispatchHarness.with_default_router()
        response = harness.dispatch(
            "GET", "/config", state=ControllerStateStub(),
        )
        assert response.status == 200
        assert json.loads(response.body) == {"config": {}}


class TestWebhooksRoute:
    """``/webhooks`` returns ``{"webhook_urls": [...]}``."""

    def test_returns_webhook_url_list(self) -> None:
        harness = RouteDispatchHarness.with_default_router()
        state = ControllerStateStub(webhook_urls=[
            "https://hooks.example.com/media-stack",
            "https://discord.example/api/webhooks/abc",
        ])
        response = harness.dispatch("GET", "/webhooks", state=state)
        assert response.status == 200
        assert json.loads(response.body) == {
            "webhook_urls": [
                "https://hooks.example.com/media-stack",
                "https://discord.example/api/webhooks/abc",
            ],
        }

    def test_returns_empty_list_when_no_webhooks(self) -> None:
        harness = RouteDispatchHarness.with_default_router()
        response = harness.dispatch(
            "GET", "/webhooks", state=ControllerStateStub(),
        )
        assert response.status == 200
        assert json.loads(response.body) == {"webhook_urls": []}


class TestRoutingIntegration:
    """Pin auto-discovery + spec-parity behavior for the State
    domain. If a future change accidentally drops a handler from
    the registry, this fires before any per-route test does."""

    def test_all_state_routes_registered(self) -> None:
        harness = RouteDispatchHarness.with_default_router()
        expected = {
            "/status", "/apps", "/apps/{appName}",
            "/config", "/webhooks",
        }
        registered = {
            r.path
            for r in harness._dispatcher._router.registered_routes()
            if r.path in expected
        }
        assert registered == expected, (
            f"Missing State routes: {expected - registered}"
        )

    def test_post_to_state_get_path_returns_method_not_allowed(
        self,
    ) -> None:
        harness = RouteDispatchHarness.with_default_router()
        outcome, _ = harness.try_dispatch("POST", "/status")
        from media_stack.api.routing import DispatchOutcome
        assert outcome == DispatchOutcome.METHOD_NOT_ALLOWED

    def test_legacy_only_api_webhooks_falls_through(self) -> None:
        """``/api/webhooks`` is matched by the legacy chain but is
        NOT in the OpenAPI spec; the Router must NO_MATCH so the
        legacy fallback path keeps serving it during Phase 2."""
        harness = RouteDispatchHarness.with_default_router()
        outcome, _ = harness.try_dispatch("GET", "/api/webhooks")
        from media_stack.api.routing import DispatchOutcome
        assert outcome == DispatchOutcome.NO_MATCH
