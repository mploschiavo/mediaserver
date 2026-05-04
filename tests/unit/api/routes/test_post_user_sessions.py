"""Tests for ``api/routes/post_user_sessions.py``
(ADR-0007 Phase 2 wave 8 group 1).

Two routes:
* ``GET  /api/users/{user_id}/sessions``
* ``POST /api/users/{user_id}/sessions/{session_id}/revoke``
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock

from media_stack.api.routes.post_admin_ops import PostMutationGate
from media_stack.api.routes.post_user_sessions import (
    UserSessionsRepository,
    UserSessionsRoutes,
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
    def __init__(self, actor: Any = None) -> None:
        self._actor = actor or MagicMock(username="alice", is_admin=True)

    def resolve(self, handler: Any, body: dict[str, Any]) -> Any:
        return self._actor


class _StubUserService:
    def __init__(self, *, sessions: list[dict[str, Any]] | None = None) -> None:
        self._sessions = sessions if sessions is not None else []
        self.calls: list[Any] = []

    def list_sessions(self, user_id: str) -> list[dict[str, Any]]:
        self.calls.append(user_id)
        return list(self._sessions)


class _StubSecurityDispatcher:
    def __init__(self) -> None:
        self.calls: list[Any] = []

    def dispatch(
        self,
        handler: Any,
        path: str,
        body: dict[str, Any],
        actor: Any,
    ) -> None:
        self.calls.append((path, dict(body), actor))
        handler._json_response(200, {"ok": True, "session_id": path.split("/")[-2]})


class _RouteHarness:
    @classmethod
    def with_routes(cls, routes: UserSessionsRoutes) -> RouteDispatchHarness:
        DefaultDispatcher.reset_for_tests()
        router = Router()
        cls._rebind(router, routes)
        return RouteDispatchHarness(RouterDispatcher(router))

    @classmethod
    def _rebind(cls, router: Router, routes: UserSessionsRoutes) -> None:
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
    def _maybe_replacement(route: Any, routes: UserSessionsRoutes) -> Any:
        if "UserSessionsRoutes" not in route.display:
            return None
        method_name = route.display.rsplit(".", 1)[-1]
        return getattr(routes, method_name, None)


def _dispatch(
    harness: RouteDispatchHarness,
    verb: str,
    path: str,
    *,
    body: bytes | dict[str, Any] = b"",
    headers: dict[str, str] | None = None,
) -> CapturedResponse:
    handler = PostMockHandler(path=path, body=body, headers=headers)
    outcome = harness._dispatcher.try_dispatch(verb, path, handler)
    if outcome == DispatchOutcome.METHOD_NOT_ALLOWED:
        harness._dispatcher.write_method_not_allowed(handler, path)
    return handler.captured


def _routes_with(**kwargs: Any) -> UserSessionsRoutes:
    kwargs.setdefault("mutation_gate", _AlwaysAllowGate())
    kwargs.setdefault("actor_resolution", _StubActorResolution())
    return UserSessionsRoutes(**kwargs)


# ---------------------------------------------------------------------------
# GET /api/users/{user_id}/sessions
# ---------------------------------------------------------------------------


class TestSessionsList:
    def test_lists_sessions_for_user(self) -> None:
        svc = _StubUserService(sessions=[
            {"id": "s1", "ip": "1.2.3.4"},
            {"id": "s2", "ip": "5.6.7.8"},
        ])
        repo = UserSessionsRepository(service_builder=lambda: svc)
        routes = _routes_with(repository=repo)
        harness = _RouteHarness.with_routes(routes)
        response = _dispatch(harness, "GET", "/api/users/u-1/sessions")
        assert response.status == 200
        body = json.loads(response.body)
        assert len(body["sessions"]) == 2
        assert svc.calls == ["u-1"]

    def test_empty_when_none(self) -> None:
        svc = _StubUserService(sessions=[])
        repo = UserSessionsRepository(service_builder=lambda: svc)
        routes = _routes_with(repository=repo)
        harness = _RouteHarness.with_routes(routes)
        response = _dispatch(harness, "GET", "/api/users/ghost/sessions")
        assert response.status == 200
        assert json.loads(response.body) == {"sessions": []}


# ---------------------------------------------------------------------------
# POST /api/users/{user_id}/sessions/{session_id}/revoke
# ---------------------------------------------------------------------------


class TestSessionRevoke:
    def test_delegates_to_security_dispatcher(self) -> None:
        dispatcher = _StubSecurityDispatcher()
        repo = UserSessionsRepository(security_dispatcher=dispatcher)
        routes = _routes_with(repository=repo)
        harness = _RouteHarness.with_routes(routes)
        response = _dispatch(
            harness, "POST",
            "/api/users/u-1/sessions/sess-99/revoke",
            body={"reason": "admin_revoke"},
        )
        assert response.status == 200
        assert dispatcher.calls
        assert dispatcher.calls[0][0] == (
            "/api/users/u-1/sessions/sess-99/revoke"
        )

    def test_csrf_gate_rejects(self) -> None:
        csrf_stub = MagicMock()
        csrf_stub.header_name = "X-CSRF-Token"
        csrf_stub.verify.return_value = False
        gate = PostMutationGate(csrf=csrf_stub)
        dispatcher = _StubSecurityDispatcher()
        repo = UserSessionsRepository(security_dispatcher=dispatcher)
        routes = UserSessionsRoutes(
            mutation_gate=gate,
            repository=repo,
            actor_resolution=_StubActorResolution(),
        )
        harness = _RouteHarness.with_routes(routes)
        response = _dispatch(
            harness, "POST",
            "/api/users/u-1/sessions/sess-99/revoke",
            body={"reason": "x"},
            headers={"Cookie": "media_stack_csrf=zzz"},
        )
        assert response.status == 403
        assert dispatcher.calls == []


# ---------------------------------------------------------------------------
# Routing integration
# ---------------------------------------------------------------------------


class TestRoutingIntegration:
    def test_get_route_registered(self) -> None:
        harness = RouteDispatchHarness.with_default_router()
        registered = {
            (r.verb, r.path)
            for r in harness._dispatcher._router.registered_routes()
            if "UserSessionsRoutes" in r.display
        }
        assert registered == {
            ("GET", "/api/users/{user_id}/sessions"),
            ("POST", "/api/users/{user_id}/sessions/{session_id}/revoke"),
        }

    def test_default_constructor_wires_real_collaborators(self) -> None:
        instance = UserSessionsRoutes()
        assert isinstance(instance._gate, PostMutationGate)
        assert isinstance(instance._repo, UserSessionsRepository)
