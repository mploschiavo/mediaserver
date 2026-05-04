"""Spec/router parity tests (ADR-0007 Phase 2 STRICT).

Runs the PRODUCTION ``Router`` against ``contracts/api/openapi.yaml``.
The router's startup checks enforce the strict direction (every
registered route must be in the spec) — these tests pin the runtime
invariants from the OUTSIDE so we'd notice a regression even if
someone disabled the startup check.

ADR-0007 Phase 2 wave-8 (commit 6e6fde13) completed the migration:
every spec path either has a registered handler OR is on the
infrastructure allowlist (root path, /metrics, /api/docs, etc. — see
``Router._INFRASTRUCTURE_ALLOWLIST``). The legacy permissive
``test_unregistered_spec_paths_only_decrease`` is retired; strict
``test_full_spec_coverage`` is the live check.
"""

from __future__ import annotations

from collections import Counter

import pytest

from media_stack.api.routing import (
    DefaultDispatcher,
    Router,
    RouterMisconfigured,
)


@pytest.fixture(scope="module")
def production_router() -> Router:
    """Module-scoped — auto-discovery is the same in every test.

    Other test files (notably ``test_router_basics.py``) reset the
    ``RouteModuleRegistry`` between tests. This fixture re-imports
    every ``api/routes/*.py`` module under
    ``importlib.reload`` so production routes are registered
    regardless of test order.
    """
    import importlib

    from media_stack.api.routing import RouteModuleRegistry

    RouteModuleRegistry.reset_for_tests()
    DefaultDispatcher.reset_for_tests()

    # Re-import every module under api.routes/ so each subclass's
    # __init_subclass__ fires + re-registers with the fresh
    # registry.
    import media_stack.api.routes as _routes_pkg
    for _name in list(_routes_pkg.__dict__):
        if _name.startswith("_"):
            continue
        full_name = f"media_stack.api.routes.{_name}"
        try:
            importlib.reload(importlib.import_module(full_name))
        except (ImportError, AttributeError):
            continue

    return DefaultDispatcher.instance()._router


class TestProductionRouterStartsClean:
    """Construction succeeds. ``DefaultDispatcher.instance()`` is
    the one any other test uses; this test pins that the production
    spec + the registered route modules don't drift."""

    def test_router_constructs(self, production_router) -> None:
        assert isinstance(production_router, Router)

    def test_at_least_one_route_registered(
        self, production_router,
    ) -> None:
        assert len(production_router.registered_routes()) > 0


class TestRegisteredRoutesAllInSpec:
    """The Router's own startup check enforces this — every
    registered route must be in the spec. This test re-asserts
    from the outside in case the startup check is ever bypassed."""

    def test_every_registered_path_is_in_spec(
        self, production_router,
    ) -> None:
        spec_paths = production_router.spec_paths()
        for route in production_router.registered_routes():
            assert route.path in spec_paths, (
                f"{route.verb} {route.path} (registered by "
                f"{route.display}) is not in openapi.yaml"
            )

    def test_every_registered_verb_is_in_spec(
        self, production_router,
    ) -> None:
        spec_paths = production_router.spec_paths()
        for route in production_router.registered_routes():
            assert route.verb in spec_paths.get(route.path, frozenset()), (
                f"{route.verb} {route.path} (registered by "
                f"{route.display}) — spec only declares "
                f"{sorted(spec_paths.get(route.path, set()))} for "
                f"this path"
            )


class TestNoDuplicateRegistrations:

    def test_each_verb_path_pair_is_unique(
        self, production_router,
    ) -> None:
        keys = [
            (r.verb, r.path)
            for r in production_router.registered_routes()
        ]
        counts = Counter(keys)
        dups = [key for key, count in counts.items() if count > 1]
        assert dups == [], (
            f"Duplicate (verb, path) registrations: {dups}. The "
            f"router's startup check should have caught these — "
            f"they're a real misconfiguration."
        )


class TestFullSpecCoverageStrictMode:
    """ADR-0007 Phase 2 cleanup gate (active since wave-8). Every
    spec path/verb must have a router-registered handler OR be on
    the infrastructure allowlist (``Router._INFRASTRUCTURE_ALLOWLIST``).
    Failure here means a new spec path was added without a route
    module, OR a route module was removed without removing its
    spec entry first."""

    def test_full_spec_coverage(self, production_router) -> None:
        # Should not raise — wave-8 completed the migration.
        production_router.assert_full_spec_coverage()
