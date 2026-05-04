"""Tests for ``api/routes/guardrails.py`` (ADR-0007 Phase 2).

Each test class owns one route. Each test invokes the production
Router via ``RouteDispatchHarness.with_default_router()`` — same
auto-discovery, same spec-parity check, same dispatch path used
in production.

The single migrated route reads from the default
``GuardrailRegistry`` plus the evaluation-loop cadence resolver.
We patch both at the import sites the route module uses, so each
test asserts the route's plumbing rather than re-testing the
registry's or evaluation loop's internals.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import patch

from tests.unit.api.routes._helpers import RouteDispatchHarness


class TestGuardrailsRegistryRoute:
    """``GET /api/guardrails`` — cross-domain registry snapshot
    plus evaluation cadence."""

    def test_returns_registry_summary_and_resolved_interval(self) -> None:
        summary_payload = [
            {"id": "storage:per_mount_threshold", "status": "ok"},
            {"id": "bandwidth:download_saturation", "status": "warn"},
        ]
        fake_registry = SimpleNamespace(
            status_summary=lambda: list(summary_payload),
        )
        with patch(
            "media_stack.services.guardrails.default",
            return_value=fake_registry,
        ), patch(
            "media_stack.application.guardrails.evaluation_loop"
            "._resolved_interval",
            return_value=120.0,
        ):
            harness = RouteDispatchHarness.with_default_router()
            response = harness.dispatch("GET", "/api/guardrails")

        assert response.status == 200
        body = json.loads(response.body)
        assert body == {
            "guardrails": summary_payload,
            "evaluation_interval_seconds": 120,
        }

    def test_resolved_interval_is_coerced_to_int(self) -> None:
        """``_resolved_interval`` returns a float (it can carry
        sub-second precision in tests). The route coerces it to
        ``int`` to match the OpenAPI schema's integer typing."""
        fake_registry = SimpleNamespace(status_summary=lambda: [])
        with patch(
            "media_stack.services.guardrails.default",
            return_value=fake_registry,
        ), patch(
            "media_stack.application.guardrails.evaluation_loop"
            "._resolved_interval",
            return_value=305.7,
        ):
            harness = RouteDispatchHarness.with_default_router()
            response = harness.dispatch("GET", "/api/guardrails")

        assert response.status == 200
        body = json.loads(response.body)
        assert body["evaluation_interval_seconds"] == 305
        assert isinstance(body["evaluation_interval_seconds"], int)

    def test_falls_back_to_default_interval_on_resolver_failure(
        self,
    ) -> None:
        """If ``_resolved_interval`` raises (e.g. profile not wired
        in early bootstrap), the route falls back to the 300-sec
        default and still emits the registry payload — the
        dashboard never sees a 500."""
        fake_registry = SimpleNamespace(
            status_summary=lambda: [{"id": "storage", "status": "ok"}],
        )
        with patch(
            "media_stack.services.guardrails.default",
            return_value=fake_registry,
        ), patch(
            "media_stack.application.guardrails.evaluation_loop"
            "._resolved_interval",
            side_effect=RuntimeError("profile not wired"),
        ):
            harness = RouteDispatchHarness.with_default_router()
            response = harness.dispatch("GET", "/api/guardrails")

        assert response.status == 200
        body = json.loads(response.body)
        assert body == {
            "guardrails": [{"id": "storage", "status": "ok"}],
            "evaluation_interval_seconds": 300,
        }

    def test_empty_registry_returns_empty_array(self) -> None:
        """A registry with no rules registered still emits a
        well-formed payload — the UI's "no guardrails configured"
        empty-state branch depends on it."""
        fake_registry = SimpleNamespace(status_summary=lambda: [])
        with patch(
            "media_stack.services.guardrails.default",
            return_value=fake_registry,
        ), patch(
            "media_stack.application.guardrails.evaluation_loop"
            "._resolved_interval",
            return_value=300.0,
        ):
            harness = RouteDispatchHarness.with_default_router()
            response = harness.dispatch("GET", "/api/guardrails")

        assert response.status == 200
        body = json.loads(response.body)
        assert body["guardrails"] == []
        assert body["evaluation_interval_seconds"] == 300

    def test_falls_back_when_interval_import_fails(self) -> None:
        """If the evaluation-loop module itself fails to import
        (rare, but possible during refactors), the handler still
        returns the registry payload with the default cadence
        rather than a 500."""
        fake_registry = SimpleNamespace(status_summary=lambda: [])

        original_import = __builtins__["__import__"] if isinstance(
            __builtins__, dict,
        ) else __builtins__.__import__

        def raising_import(name, *args, **kwargs):
            if name == (
                "media_stack.application.guardrails.evaluation_loop"
            ):
                raise ImportError("simulated import failure")
            return original_import(name, *args, **kwargs)

        with patch(
            "media_stack.services.guardrails.default",
            return_value=fake_registry,
        ), patch("builtins.__import__", side_effect=raising_import):
            harness = RouteDispatchHarness.with_default_router()
            response = harness.dispatch("GET", "/api/guardrails")

        assert response.status == 200
        body = json.loads(response.body)
        assert body["evaluation_interval_seconds"] == 300


class TestRoutingIntegration:
    """Pin auto-discovery + spec-parity behaviour for the
    guardrails domain. If a future change accidentally drops the
    handler from the registry, this test fires before any per-route
    test does."""

    def test_guardrails_route_registered(self) -> None:
        harness = RouteDispatchHarness.with_default_router()
        expected = {"/api/guardrails"}
        registered = {
            r.path
            for r in harness._dispatcher._router.registered_routes()
            if r.path in expected
        }
        assert registered == expected, (
            f"Missing guardrails routes: {expected - registered}"
        )
