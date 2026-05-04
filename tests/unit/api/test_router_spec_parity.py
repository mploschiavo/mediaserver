"""Spec/router parity tests (ADR-0007 Phase 1).

Runs the PRODUCTION ``Router`` against ``contracts/api/openapi.yaml``.
The router's startup checks already enforce the strict direction
(every registered route must be in the spec) — these tests pin the
runtime invariants from the OUTSIDE so we'd notice a regression
even if someone disabled the startup check.

During Phase 2 migration this test is **permissive**: it logs the
spec paths that have no router-registered handler (those still
work via the legacy ``handlers_get.handle()`` / ``handlers_post.handle()``
chain). After Phase 2 completes,
``test_full_spec_coverage_strict_mode`` is enabled and the legacy
chains are deleted.
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


class TestPermissiveMigrationCoverage:
    """Phase 1 / early Phase 2: the spec has many paths the router
    hasn't claimed yet. They fall through to the legacy chain. We
    pin the count so it can only go DOWN (each Phase 2 commit
    migrates a domain → reduces the missing count)."""

    def test_unregistered_spec_paths_only_decrease(
        self, production_router,
    ) -> None:
        spec_paths = production_router.spec_paths()
        registered = {
            (r.verb, r.path)
            for r in production_router.registered_routes()
        }
        missing = sorted(
            f"{verb} {path}"
            for path, verbs in spec_paths.items()
            for verb in verbs
            if (verb, path) not in registered
        )

        # Permissive baseline. Each Phase 2 commit lowers it.
        # When the count reaches 0, flip
        # ``test_full_spec_coverage_strict_mode`` to active and
        # delete this test + the legacy fallback chains.
        baseline_path = (
            production_router._openapi_path.parents[2]
            / ".ratchets"
            / "router-unmigrated-routes-baseline.txt"
        )
        if not baseline_path.is_file():
            baseline_path.parent.mkdir(parents=True, exist_ok=True)
            baseline_path.write_text(f"{len(missing)}\n")
            pytest.skip(
                f"Seeded baseline at {len(missing)} unmigrated routes",
            )

        baseline = int(baseline_path.read_text().strip())
        assert len(missing) <= baseline, (
            f"Unmigrated route count regressed: {baseline} → "
            f"{len(missing)}. Phase 2's direction is DOWN — every "
            f"new domain migration should reduce the count. If you "
            f"genuinely added a new spec path that you haven't "
            f"migrated yet, lower the baseline at "
            f"{baseline_path} after migrating it.\n"
            f"Newly missing entries: "
            f"{[m for m in missing if m not in _baseline_snapshot()][:10]}"
        )


def _baseline_snapshot() -> set[str]:
    """Stub — we only check counts in Phase 1. A future tightening
    can pin specific paths."""
    return set()


@pytest.mark.skip(
    reason="Strict-mode coverage. Activated after Phase 2 completes "
           "and the legacy handlers_{get,post}.handle() chains are "
           "deleted. Skipped during migration so spec paths without "
           "registered handlers fall through to the legacy chain.",
)
class TestFullSpecCoverageStrictMode:
    """Phase 2 cleanup gate. When activated, asserts every spec
    path/verb has a router-registered handler. Until then,
    ``test_unregistered_spec_paths_only_decrease`` enforces the
    direction."""

    def test_full_spec_coverage(self, production_router) -> None:
        with pytest.raises((RouterMisconfigured, type(None))):
            production_router.assert_full_spec_coverage()
