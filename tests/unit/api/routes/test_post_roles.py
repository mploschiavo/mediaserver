"""Tests for ``api/routes/post_roles.py``
(ADR-0007 Phase 2 wave 8 group 1).

Single route: ``POST /api/roles/{role_slug}``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

from media_stack.api.routes.post_admin_ops import PostMutationGate
from media_stack.api.routes.post_roles import (
    RoleFieldFilter,
    RolesPostRoutes,
    RolesRepository,
)
from media_stack.api.routing import (
    DefaultDispatcher,
    DispatchOutcome,
    Router,
    RouterDispatcher,
)
from tests.unit.api.routes._helpers import (
    CapturedResponse,
    MockControllerHandler,
    RouteDispatchHarness,
)


class PostMockHandler(MockControllerHandler):
    def __init__(
        self,
        *,
        path: str = "/",
        body: bytes | dict[str, Any] = b"",
        headers: dict[str, str] | None = None,
    ) -> None:
        if isinstance(body, dict):
            body = json.dumps(body).encode("utf-8")
        merged_headers = dict(headers or {})
        if body and "Content-Length" not in merged_headers:
            merged_headers["Content-Length"] = str(len(body))
        super().__init__(path=path, body=body, headers=merged_headers)

    def _read_json_body(self) -> dict[str, Any]:
        length_str = self.headers.get("Content-Length", "0")
        try:
            length = int(length_str)
        except (TypeError, ValueError):
            length = 0
        if length <= 0:
            return {}
        try:
            return json.loads(self.rfile.read(length))
        except (json.JSONDecodeError, ValueError):
            return {}


class _AlwaysAllowGate:
    def verify(self, handler: Any) -> bool:
        return True

    def reject(self, handler: Any) -> None:  # pragma: no cover
        raise AssertionError("reject should not be called")


class _StubActorResolution:
    def __init__(self) -> None:
        self._actor = MagicMock(username="alice", is_admin=True)

    def resolve(self, handler: Any, body: dict[str, Any]) -> Any:
        return self._actor


class _StubYamlEditor:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.last_mutation: dict[str, Any] | None = None

    def edit(self, mutator: Any) -> None:
        starting = {"roles": {"adult": {"name": "Adult"}}}
        self.last_mutation = mutator(starting)


class _StubService:
    def __init__(self) -> None:
        self._roles = MagicMock()
        self._roles.reload = MagicMock()


class _RouteHarness:
    @classmethod
    def with_routes(cls, routes: RolesPostRoutes) -> RouteDispatchHarness:
        DefaultDispatcher.reset_for_tests()
        router = Router()
        cls._rebind(router, routes)
        return RouteDispatchHarness(RouterDispatcher(router))

    @classmethod
    def _rebind(cls, router: Router, routes: RolesPostRoutes) -> None:
        for key, route in list(router._exact.items()):
            replacement = cls._maybe_replacement(route, routes)
            if replacement is not None:
                router._exact[key] = type(route)(
                    verb=route.verb, path=route.path,
                    handler=replacement, pattern=route.pattern,
                    param_names=route.param_names, display=route.display,
                )
        for idx, route in enumerate(list(router._parameterized)):
            replacement = cls._maybe_replacement(route, routes)
            if replacement is not None:
                router._parameterized[idx] = type(route)(
                    verb=route.verb, path=route.path,
                    handler=replacement, pattern=route.pattern,
                    param_names=route.param_names, display=route.display,
                )

    @staticmethod
    def _maybe_replacement(route: Any, routes: RolesPostRoutes) -> Any:
        if "RolesPostRoutes" not in route.display:
            return None
        method_name = route.display.rsplit(".", 1)[-1]
        return getattr(routes, method_name, None)


def _dispatch_post(
    harness: RouteDispatchHarness,
    path: str,
    *,
    body: bytes | dict[str, Any] = b"",
    headers: dict[str, str] | None = None,
) -> CapturedResponse:
    handler = PostMockHandler(path=path, body=body, headers=headers)
    outcome = harness._dispatcher.try_dispatch("POST", path, handler)
    if outcome == DispatchOutcome.METHOD_NOT_ALLOWED:
        harness._dispatcher.write_method_not_allowed(handler, path)
    return handler.captured


def _routes_with(**kwargs: Any) -> RolesPostRoutes:
    kwargs.setdefault("mutation_gate", _AlwaysAllowGate())
    kwargs.setdefault("actor_resolution", _StubActorResolution())
    return RolesPostRoutes(**kwargs)


# ---------------------------------------------------------------------------
# RoleFieldFilter
# ---------------------------------------------------------------------------


class TestRoleFieldFilter:
    def test_filters_unknown_fields(self) -> None:
        result = RoleFieldFilter().filter({
            "name": "Adult", "evil_field": "ignore",
            "controller_admin": True,
        })
        assert result == {"name": "Adult", "controller_admin": True}

    def test_empty_input_yields_empty(self) -> None:
        assert RoleFieldFilter().filter({}) == {}


# ---------------------------------------------------------------------------
# Route handler
# ---------------------------------------------------------------------------


class TestRoleUpdateRoute:
    def test_happy_path_writes_filtered_fields(self) -> None:
        editor = _StubYamlEditor(Path("/tmp/roles.yaml"))
        svc = _StubService()
        repo = RolesRepository(
            yaml_editor_factory=lambda p: editor,
            service_builder=lambda: svc,
            roles_path_resolver=lambda: Path("/tmp/roles.yaml"),
        )
        routes = _routes_with(repository=repo)
        harness = _RouteHarness.with_routes(routes)
        response = _dispatch_post(
            harness, "/api/roles/adult",
            body={
                "name": "Adult v2",
                "evil_field": "ignored",
                "controller_admin": True,
            },
        )
        assert response.status == 200
        body = json.loads(response.body)
        assert body == {
            "role": "adult", "updated": True, "actor": "alice",
        }
        # Confirm the mutator wrote only allowlisted fields.
        new = editor.last_mutation
        assert new is not None
        assert new["roles"]["adult"]["name"] == "Adult v2"
        assert new["roles"]["adult"]["controller_admin"] is True
        assert "evil_field" not in new["roles"]["adult"]
        # Confirm the role catalog was reloaded after the YAML write.
        svc._roles.reload.assert_called_once()

    def test_csrf_gate_rejects(self) -> None:
        csrf_stub = MagicMock()
        csrf_stub.header_name = "X-CSRF-Token"
        csrf_stub.verify.return_value = False
        gate = PostMutationGate(csrf=csrf_stub)
        editor = _StubYamlEditor(Path("/tmp/roles.yaml"))
        svc = _StubService()
        repo = RolesRepository(
            yaml_editor_factory=lambda p: editor,
            service_builder=lambda: svc,
            roles_path_resolver=lambda: Path("/tmp/roles.yaml"),
        )
        routes = RolesPostRoutes(
            mutation_gate=gate, repository=repo,
            actor_resolution=_StubActorResolution(),
        )
        harness = _RouteHarness.with_routes(routes)
        response = _dispatch_post(
            harness, "/api/roles/adult",
            body={"name": "X"},
            headers={"Cookie": "media_stack_csrf=zzz"},
        )
        assert response.status == 403
        # YAML editor should never be invoked.
        assert editor.last_mutation is None


# ---------------------------------------------------------------------------
# Routing integration
# ---------------------------------------------------------------------------


class TestRoutingIntegration:
    def test_route_registered(self) -> None:
        harness = RouteDispatchHarness.with_default_router()
        registered = {
            (r.verb, r.path)
            for r in harness._dispatcher._router.registered_routes()
            if "RolesPostRoutes" in r.display
        }
        assert registered == {("POST", "/api/roles/{role_slug}")}

    def test_default_constructor_wires_real_collaborators(self) -> None:
        instance = RolesPostRoutes()
        assert isinstance(instance._gate, PostMutationGate)
        assert isinstance(instance._repo, RolesRepository)
        assert isinstance(instance._filter, RoleFieldFilter)
