"""Tests for ``api/routes/snapshots.py`` (ADR-0007 Phase 2 wave 7).

One route: ``GET /api/snapshots/{filename}`` — single-snapshot read.
The legacy chain at ``handlers_get.py:1216-1218`` always returns 200
even when the service layer returns ``{"error": ...}``; this file
pins that behaviour.
"""

from __future__ import annotations

import json
from typing import Any

from media_stack.api.routes.snapshots import (
    SnapshotsGetRoutes,
    SnapshotsRepository,
)
from media_stack.api.routing import (
    DefaultDispatcher,
    DispatchOutcome,
    Router,
    RouterDispatcher,
)
from tests.unit.api.routes._helpers import RouteDispatchHarness


# ---------------------------------------------------------------------------
# Harness — rebinds auto-discovered SnapshotsGetRoutes with test stub
# ---------------------------------------------------------------------------


class _RouteHarness:
    @classmethod
    def with_routes(cls, routes: SnapshotsGetRoutes) -> RouteDispatchHarness:
        DefaultDispatcher.reset_for_tests()
        router = Router()
        cls._rebind(router, routes)
        return RouteDispatchHarness(RouterDispatcher(router))

    @classmethod
    def _rebind(cls, router: Router, routes: SnapshotsGetRoutes) -> None:
        for key, route in list(router._exact.items()):
            m = cls._maybe_replacement(route, routes)
            if m is not None:
                router._exact[key] = type(route)(
                    verb=route.verb, path=route.path, handler=m,
                    pattern=route.pattern, param_names=route.param_names,
                    display=route.display,
                )
        for idx, route in enumerate(list(router._parameterized)):
            m = cls._maybe_replacement(route, routes)
            if m is not None:
                router._parameterized[idx] = type(route)(
                    verb=route.verb, path=route.path, handler=m,
                    pattern=route.pattern, param_names=route.param_names,
                    display=route.display,
                )

    @staticmethod
    def _maybe_replacement(route: Any, routes: SnapshotsGetRoutes) -> Any:
        if "SnapshotsGetRoutes" not in route.display:
            return None
        method_name = route.display.rsplit(".", 1)[-1]
        return getattr(routes, method_name, None)


def _repo_returning(payload: dict[str, Any]) -> SnapshotsRepository:
    return SnapshotsRepository(get_detail_fn=lambda fn: payload)


# ---------------------------------------------------------------------------
# /api/snapshots/{filename} — route shape
# ---------------------------------------------------------------------------


class TestSnapshotDetailRoute:
    """``GET /api/snapshots/{filename}`` — full snapshot content."""

    def test_happy_path_returns_200_with_snapshot_body(self) -> None:
        payload = {
            "snapshot": {"sonarr/config.xml": "..."},
            "file": "snapshot-20260407T120000.json",
        }
        routes = SnapshotsGetRoutes(
            snapshots_repository=_repo_returning(payload),
        )
        harness = _RouteHarness.with_routes(routes)

        response = harness.dispatch(
            "GET", "/api/snapshots/snapshot-20260407T120000.json",
        )

        assert response.status == 200
        assert json.loads(response.body) == payload

    def test_filename_path_param_forwarded_unchanged(self) -> None:
        """``filename`` captured from URL path and passed to repository."""
        captured: list[str] = []

        routes = SnapshotsGetRoutes(
            snapshots_repository=SnapshotsRepository(
                get_detail_fn=lambda fn: captured.append(fn) or {"file": fn},
            ),
        )
        harness = _RouteHarness.with_routes(routes)
        harness.dispatch(
            "GET", "/api/snapshots/snapshot-20260101T000000.json",
        )

        assert captured == ["snapshot-20260101T000000.json"]

    def test_service_error_payload_returns_200_not_4xx(self) -> None:
        """Legacy behaviour: ``{"error": "..."}`` from service returns
        200 — NOT converted to 404 or 400. UI reads the error body
        in-band.
        """
        error_payload = {"error": "Snapshot not found"}
        routes = SnapshotsGetRoutes(
            snapshots_repository=_repo_returning(error_payload),
        )
        harness = _RouteHarness.with_routes(routes)

        response = harness.dispatch(
            "GET", "/api/snapshots/snapshot-missing.json",
        )

        assert response.status == 200
        assert json.loads(response.body) == error_payload

    def test_path_traversal_error_also_200(self) -> None:
        """Service returns ``{"error": "Invalid snapshot filename"}``
        for path traversal; route still emits 200."""
        payload = {"error": "Invalid snapshot filename"}
        routes = SnapshotsGetRoutes(
            snapshots_repository=_repo_returning(payload),
        )
        harness = _RouteHarness.with_routes(routes)

        response = harness.dispatch(
            "GET", "/api/snapshots/bad..file.json",
        )

        assert response.status == 200
        assert json.loads(response.body) == payload


# ---------------------------------------------------------------------------
# SnapshotsRepository unit
# ---------------------------------------------------------------------------


class TestSnapshotsRepository:
    def test_injected_fn_used_when_provided(self) -> None:
        expected = {"snapshot": {"k": "v"}, "file": "test.json"}
        repo = SnapshotsRepository(get_detail_fn=lambda fn: expected)
        assert repo.get_detail("test.json") == expected

    def test_filename_forwarded_to_injected_fn(self) -> None:
        received: list[str] = []
        repo = SnapshotsRepository(
            get_detail_fn=lambda fn: received.append(fn) or {},  # type: ignore[func-returns-value]
        )
        repo.get_detail("snapshot-abc.json")
        assert received == ["snapshot-abc.json"]


# ---------------------------------------------------------------------------
# Auto-discovery + spec-parity integration
# ---------------------------------------------------------------------------


class TestRoutingIntegration:
    def test_snapshots_route_registered(self) -> None:
        harness = RouteDispatchHarness.with_default_router()
        found = any(
            r.path == "/api/snapshots/{filename}"
            for r in harness._dispatcher._router.registered_routes()
        )
        assert found, "/api/snapshots/{filename} not registered"

    def test_post_to_snapshots_does_not_match_get_route(self) -> None:
        """``POST`` against the parameterized snapshot path falls
        through with ``NO_MATCH``. The Router's literal-string
        spec-path lookup can't see the parameterized template, so it
        returns ``NO_MATCH`` rather than ``METHOD_NOT_ALLOWED`` for
        path-template routes — pin that contract.
        """
        harness = RouteDispatchHarness.with_default_router()
        outcome, _ = harness.try_dispatch(
            "POST", "/api/snapshots/snapshot-20260407T120000.json",
        )
        assert outcome == DispatchOutcome.NO_MATCH
