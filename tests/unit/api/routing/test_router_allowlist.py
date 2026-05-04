"""Tests for the Router infrastructure-GET allowlist
(``api/routing/router.py::_INFRASTRUCTURE_ALLOWLIST``).

The allowlist is the carve-out that lets
``Router.assert_full_spec_coverage()`` pass once Phase 2 is done
even though five (verb, path) entries in ``openapi.yaml`` will
never have a registered RouteModule handler — they're served by
``server.py`` directly (landing pages, static assets, Prometheus
metrics, Swagger docs).

These tests pin:

* The exact 5-entry allowlist set — adding a sixth requires
  intent + this test to be updated.
* ``assert_full_spec_coverage`` skips an allowlisted (verb, path)
  even when no handler is registered.
* ``assert_full_spec_coverage`` still raises for a NON-allowlisted
  spec path with no handler — the carve-out hasn't accidentally
  become a wildcard.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from media_stack.api.routing import (
    RouteModule,
    RouteModuleRegistry,
    Router,
    RouterMisconfigured,
    get,
)
from media_stack.api.routing.router import _INFRASTRUCTURE_ALLOWLIST


_EXPECTED_ALLOWLIST: frozenset[tuple[str, str]] = frozenset({
    ("GET", "/"),
    ("GET", "/dashboard"),
    ("GET", "/api/docs"),
    ("GET", "/api/static/{asset}"),
    ("GET", "/metrics"),
})


@pytest.fixture
def reset_registry():
    """Each test gets a fresh ``RouteModuleRegistry`` so subclass
    declarations from one test don't leak into another."""
    RouteModuleRegistry.reset_for_tests()
    yield
    RouteModuleRegistry.reset_for_tests()


def _write_spec(tmp_path: Path, paths: dict[str, list[str]]) -> Path:
    """Write a tiny ``openapi.yaml`` with the given paths/verbs."""
    import yaml
    spec = {
        "openapi": "3.0.0",
        "info": {"title": "Test", "version": "0.1"},
        "paths": {
            path: {verb.lower(): {"responses": {"200": {}}}
                   for verb in verbs}
            for path, verbs in paths.items()
        },
    }
    p = tmp_path / "openapi.yaml"
    p.write_text(yaml.safe_dump(spec))
    return p


class TestAllowlistMembership:
    """The allowlist is exact-match. New entries require explicit
    intent — adding to the constant without updating this test
    fails the build."""

    def test_allowlist_size_is_five(self) -> None:
        assert len(_INFRASTRUCTURE_ALLOWLIST) == 5

    def test_allowlist_contains_exact_five_entries(self) -> None:
        assert _INFRASTRUCTURE_ALLOWLIST == _EXPECTED_ALLOWLIST

    def test_allowlist_is_immutable(self) -> None:
        """``frozenset`` so a runtime mutation can't accidentally
        add a path; pin the type so a later refactor doesn't drop
        the immutability guarantee."""
        assert isinstance(_INFRASTRUCTURE_ALLOWLIST, frozenset)

    def test_allowlist_only_contains_get_verbs(self) -> None:
        """Every infrastructure carve-out is a GET — POSTs/DELETEs
        always need a real handler. Pin the verb-discrimination so
        a later mistake doesn't accidentally let a mutation
        endpoint slip past strict coverage."""
        for verb, _path in _INFRASTRUCTURE_ALLOWLIST:
            assert verb == "GET", (
                f"Allowlist entry ({verb!r}, _) is not a GET — "
                f"only infrastructure GETs may bypass strict coverage"
            )


class TestAllowlistedPathSkipsCoverageCheck:
    """Pin that ``assert_full_spec_coverage`` honours the allowlist:
    an allowlisted spec path with no handler does NOT raise."""

    def test_landing_page_skipped(
        self, tmp_path, reset_registry,
    ) -> None:
        spec_path = _write_spec(tmp_path, {"/": ["GET"]})
        router = Router(openapi_path=spec_path, routes_package=None)
        # No handler registered, but ``GET /`` is on the allowlist.
        router.assert_full_spec_coverage()  # must not raise

    def test_dashboard_skipped(
        self, tmp_path, reset_registry,
    ) -> None:
        spec_path = _write_spec(tmp_path, {"/dashboard": ["GET"]})
        router = Router(openapi_path=spec_path, routes_package=None)
        router.assert_full_spec_coverage()

    def test_api_docs_skipped(
        self, tmp_path, reset_registry,
    ) -> None:
        spec_path = _write_spec(tmp_path, {"/api/docs": ["GET"]})
        router = Router(openapi_path=spec_path, routes_package=None)
        router.assert_full_spec_coverage()

    def test_api_static_asset_skipped(
        self, tmp_path, reset_registry,
    ) -> None:
        spec_path = _write_spec(
            tmp_path, {"/api/static/{asset}": ["GET"]},
        )
        router = Router(openapi_path=spec_path, routes_package=None)
        router.assert_full_spec_coverage()

    def test_metrics_skipped(
        self, tmp_path, reset_registry,
    ) -> None:
        spec_path = _write_spec(tmp_path, {"/metrics": ["GET"]})
        router = Router(openapi_path=spec_path, routes_package=None)
        router.assert_full_spec_coverage()

    def test_all_five_together_skipped(
        self, tmp_path, reset_registry,
    ) -> None:
        spec_path = _write_spec(tmp_path, {
            "/": ["GET"],
            "/dashboard": ["GET"],
            "/api/docs": ["GET"],
            "/api/static/{asset}": ["GET"],
            "/metrics": ["GET"],
        })
        router = Router(openapi_path=spec_path, routes_package=None)
        # Zero handlers but all five paths allowlisted — pass.
        router.assert_full_spec_coverage()


class TestNonAllowlistedPathStillFails:
    """Pin that the carve-out is exact-match: a NON-allowlisted
    spec path with no handler still raises strict-coverage."""

    def test_unknown_path_with_no_handler_raises(
        self, tmp_path, reset_registry,
    ) -> None:
        spec_path = _write_spec(tmp_path, {"/api/unmigrated": ["GET"]})
        router = Router(openapi_path=spec_path, routes_package=None)
        with pytest.raises(RouterMisconfigured) as excinfo:
            router.assert_full_spec_coverage()
        assert "/api/unmigrated" in str(excinfo.value)

    def test_post_to_allowlisted_path_still_fails(
        self, tmp_path, reset_registry,
    ) -> None:
        """The allowlist is (verb, path) tuples — ``POST /`` is
        NOT allowlisted even though ``GET /`` is. Pin so a future
        spec change adding a POST to a landing-page path doesn't
        accidentally inherit the bypass.
        """
        spec_path = _write_spec(tmp_path, {"/": ["POST"]})
        router = Router(openapi_path=spec_path, routes_package=None)
        with pytest.raises(RouterMisconfigured) as excinfo:
            router.assert_full_spec_coverage()
        assert "POST" in str(excinfo.value)
        assert "/" in str(excinfo.value)

    def test_mixed_paths_only_unknown_reported(
        self, tmp_path, reset_registry,
    ) -> None:
        """Spec with one allowlisted path + one un-handled path:
        the error message should call out only the un-handled
        path, not the allowlisted one."""
        spec_path = _write_spec(tmp_path, {
            "/metrics": ["GET"],            # allowlisted
            "/api/unmigrated": ["GET"],     # not allowlisted
        })
        router = Router(openapi_path=spec_path, routes_package=None)
        with pytest.raises(RouterMisconfigured) as excinfo:
            router.assert_full_spec_coverage()
        msg = str(excinfo.value)
        assert "/api/unmigrated" in msg
        assert "/metrics" not in msg


class TestHandlerRegisteredAlongsideAllowlistedPath:
    """Sanity: an allowlisted path doesn't BLOCK a handler from
    being registered — it just means strict-coverage doesn't
    require one. If someone DOES register a handler for an
    allowlisted path, the route still works normally."""

    def test_can_register_handler_for_allowlisted_path(
        self, tmp_path, reset_registry,
    ) -> None:
        spec_path = _write_spec(tmp_path, {"/metrics": ["GET"]})

        class MetricsRoutes(RouteModule):
            @get("/metrics")
            def handle_metrics(self, handler):
                handler._json_response(200, {"metrics": "..."})

        router = Router(openapi_path=spec_path, routes_package=None)
        # Both: handler is reachable AND strict coverage passes
        # (because an allowlisted path that DOES have a handler
        # is even less reason to fail coverage).
        match = router.match("GET", "/metrics")
        assert match is not None
        router.assert_full_spec_coverage()
