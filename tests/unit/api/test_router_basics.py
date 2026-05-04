"""Tests for the Router infrastructure itself
(``api/routing/router.py`` + dispatcher + decorators).

Covers:
  * Path compilation: exact paths, parameterized paths, edge cases
  * Lookup: O(1) for exact, regex for parameterized
  * Decorators: tag-only behavior, double-tag rejection, bad path
  * RouteModule subclass auto-registration
  * Drift checks: missing spec entry, duplicate registration,
    handler-signature mismatch
  * Dispatch outcomes: HANDLED / NO_MATCH / METHOD_NOT_ALLOWED

Tests build their OWN ``Router`` against a fixture spec written
to a temp file — so they don't depend on the production
``openapi.yaml`` and don't interfere with each other through
the ``RouteModuleRegistry`` singleton.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from media_stack.api.routing import (
    DispatchOutcome,
    RouteModule,
    RouteModuleRegistry,
    Router,
    RouterDispatcher,
    RouterMisconfigured,
    get,
    post,
)


@pytest.fixture
def reset_registry():
    """Each test gets a fresh ``RouteModuleRegistry`` so subclass
    declarations from one test don't leak into another."""
    RouteModuleRegistry.reset_for_tests()
    yield
    RouteModuleRegistry.reset_for_tests()


def _write_spec(tmp_path: Path, paths: dict[str, list[str]]) -> Path:
    """Write a tiny ``openapi.yaml`` with the given paths/verbs.
    Each value is a list of HTTP methods."""
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


class _StubHandler:
    """Tiny ``ControllerAPIHandler`` stand-in for dispatch tests."""

    def __init__(self) -> None:
        self.last_status: int | None = None
        self.last_body: Any = None

    def _json_response(self, status: int, body: Any) -> None:
        self.last_status = status
        self.last_body = body

    def _raw_response(
        self, status: int, content_type: str, body: bytes,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        self.last_status = status
        self.last_body = body


class TestExactPathDispatch:

    def test_registers_and_dispatches_exact_path(
        self, tmp_path, reset_registry,
    ) -> None:
        spec_path = _write_spec(tmp_path, {"/api/foo": ["GET"]})

        class FooRoutes(RouteModule):
            @get("/api/foo")
            def handle_foo(self, handler):
                handler._json_response(200, {"foo": True})

        router = Router(
            openapi_path=spec_path,
            routes_package=None,
        )
        match = router.match("GET", "/api/foo")
        assert match is not None
        assert match.route.path == "/api/foo"
        assert match.params == {}

        handler = _StubHandler()
        match.route.handler(handler)
        assert handler.last_body == {"foo": True}


class TestParameterizedPath:

    def test_extracts_path_parameter(
        self, tmp_path, reset_registry,
    ) -> None:
        spec_path = _write_spec(
            tmp_path, {"/api/users/{user_id}": ["GET"]},
        )

        class UserRoutes(RouteModule):
            @get("/api/users/{user_id}")
            def handle_get_user(self, handler, user_id):
                handler._json_response(200, {"user_id": user_id})

        router = Router(
            openapi_path=spec_path,
            routes_package=None,
        )
        match = router.match("GET", "/api/users/abc-123")
        assert match is not None
        assert match.params == {"user_id": "abc-123"}

        handler = _StubHandler()
        match.route.handler(handler, **match.params)
        assert handler.last_body == {"user_id": "abc-123"}

    def test_param_does_not_match_across_segments(
        self, tmp_path, reset_registry,
    ) -> None:
        spec_path = _write_spec(
            tmp_path, {"/api/users/{user_id}": ["GET"]},
        )

        class UserRoutes(RouteModule):
            @get("/api/users/{user_id}")
            def handle_get_user(self, handler, user_id):
                pass

        router = Router(
            openapi_path=spec_path,
            routes_package=None,
        )
        # Multi-segment paths shouldn't match a single-{name} route.
        assert router.match("GET", "/api/users/abc/extra") is None


class TestDriftChecks:

    def test_path_missing_from_spec_raises(
        self, tmp_path, reset_registry,
    ) -> None:
        spec_path = _write_spec(tmp_path, {"/api/known": ["GET"]})

        class StrayRoutes(RouteModule):
            @get("/api/unknown")
            def handle_unknown(self, handler):
                pass

        with pytest.raises(RouterMisconfigured) as excinfo:
            Router(openapi_path=spec_path, routes_package=None)
        assert "/api/unknown" in str(excinfo.value)
        assert "not in the OpenAPI spec" in str(excinfo.value)

    def test_verb_missing_from_spec_raises(
        self, tmp_path, reset_registry,
    ) -> None:
        spec_path = _write_spec(tmp_path, {"/api/foo": ["GET"]})

        class WrongVerbRoutes(RouteModule):
            @post("/api/foo")
            def handle_post_foo(self, handler):
                pass

        with pytest.raises(RouterMisconfigured) as excinfo:
            Router(openapi_path=spec_path, routes_package=None)
        assert "POST" in str(excinfo.value)
        assert "GET" in str(excinfo.value)

    def test_duplicate_registration_raises(
        self, tmp_path, reset_registry,
    ) -> None:
        spec_path = _write_spec(tmp_path, {"/api/foo": ["GET"]})

        class FirstRoutes(RouteModule):
            @get("/api/foo")
            def handle_first(self, handler):
                pass

        class SecondRoutes(RouteModule):
            @get("/api/foo")
            def handle_second(self, handler):
                pass

        with pytest.raises(RouterMisconfigured) as excinfo:
            Router(openapi_path=spec_path, routes_package=None)
        assert "Duplicate route registration" in str(excinfo.value)

    def test_handler_missing_path_param_kwarg_raises(
        self, tmp_path, reset_registry,
    ) -> None:
        spec_path = _write_spec(
            tmp_path, {"/api/users/{user_id}": ["GET"]},
        )

        class BadSigRoutes(RouteModule):
            @get("/api/users/{user_id}")
            def handle_no_kwarg(self, handler):
                # Missing user_id — drift check should catch.
                pass

        with pytest.raises(RouterMisconfigured) as excinfo:
            Router(openapi_path=spec_path, routes_package=None)
        assert "user_id" in str(excinfo.value)

    def test_missing_spec_file_raises(
        self, tmp_path, reset_registry,
    ) -> None:
        nowhere = tmp_path / "missing.yaml"
        with pytest.raises(RouterMisconfigured) as excinfo:
            Router(openapi_path=nowhere, routes_package=None)
        assert "OpenAPI spec not found" in str(excinfo.value)


class TestDecoratorBehavior:

    def test_double_tag_raises(self, reset_registry) -> None:
        with pytest.raises(ValueError) as excinfo:
            class DoubleTagged(RouteModule):
                @get("/api/foo")
                @post("/api/foo")
                def handle(self, handler):
                    pass
        assert "already tagged" in str(excinfo.value)

    def test_path_must_be_absolute(self, reset_registry) -> None:
        with pytest.raises(ValueError) as excinfo:
            @get("not-a-path")
            def handler(self, handler):  # noqa: B902
                pass
        assert "absolute path" in str(excinfo.value)


class TestDispatcher:

    def test_handled_outcome_invokes_handler(
        self, tmp_path, reset_registry,
    ) -> None:
        spec_path = _write_spec(tmp_path, {"/api/foo": ["GET"]})

        class FooRoutes(RouteModule):
            @get("/api/foo")
            def handle_foo(self, handler):
                handler._json_response(200, {"foo": True})

        dispatcher = RouterDispatcher(Router(
            openapi_path=spec_path, routes_package=None,
        ))
        handler = _StubHandler()
        outcome = dispatcher.try_dispatch("GET", "/api/foo", handler)
        assert outcome == DispatchOutcome.HANDLED
        assert handler.last_body == {"foo": True}

    def test_no_match_outcome_when_path_not_in_spec(
        self, tmp_path, reset_registry,
    ) -> None:
        spec_path = _write_spec(tmp_path, {"/api/foo": ["GET"]})
        dispatcher = RouterDispatcher(Router(
            openapi_path=spec_path, routes_package=None,
        ))
        handler = _StubHandler()
        outcome = dispatcher.try_dispatch("GET", "/api/bar", handler)
        assert outcome == DispatchOutcome.NO_MATCH

    def test_method_not_allowed_when_path_in_spec_with_other_verb(
        self, tmp_path, reset_registry,
    ) -> None:
        spec_path = _write_spec(tmp_path, {"/api/foo": ["GET"]})

        class FooRoutes(RouteModule):
            @get("/api/foo")
            def handle_foo(self, handler):
                pass

        dispatcher = RouterDispatcher(Router(
            openapi_path=spec_path, routes_package=None,
        ))
        handler = _StubHandler()
        outcome = dispatcher.try_dispatch("POST", "/api/foo", handler)
        assert outcome == DispatchOutcome.METHOD_NOT_ALLOWED


class TestStrictModeCoverage:

    def test_assert_full_spec_coverage_raises_when_paths_unhandled(
        self, tmp_path, reset_registry,
    ) -> None:
        spec_path = _write_spec(
            tmp_path,
            {"/api/foo": ["GET"], "/api/bar": ["GET"]},
        )

        class OnlyFooRoutes(RouteModule):
            @get("/api/foo")
            def handle_foo(self, handler):
                pass

        router = Router(
            openapi_path=spec_path, routes_package=None,
        )
        with pytest.raises(RouterMisconfigured) as excinfo:
            router.assert_full_spec_coverage()
        assert "/api/bar" in str(excinfo.value)

    def test_assert_full_spec_coverage_passes_when_complete(
        self, tmp_path, reset_registry,
    ) -> None:
        spec_path = _write_spec(tmp_path, {"/api/foo": ["GET"]})

        class FooRoutes(RouteModule):
            @get("/api/foo")
            def handle_foo(self, handler):
                pass

        router = Router(
            openapi_path=spec_path, routes_package=None,
        )
        router.assert_full_spec_coverage()  # should not raise
