"""Tests for ``api/routes/post_me.py``
(ADR-0007 Phase 2 wave 8 group 1).

Three routes:
* ``POST /api/me/revoke-others``
* ``POST /api/me/this-wasnt-me``
* ``POST /api/emergency-revoke-all``
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock

from media_stack.api.routes.post_admin_ops import PostMutationGate
from media_stack.api.routes.post_me import (
    MePostRoutes,
    SecurityHandlersRepository,
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


class _StubSecurityDispatcher:
    """Records every dispatch call. Each call writes a 200 envelope
    containing the path so the route's pass-through wiring can be
    asserted on the response too."""

    def __init__(self, *, raises: Exception | None = None) -> None:
        self._raises = raises
        self.calls: list[tuple[str, dict[str, Any], Any]] = []

    def dispatch(
        self,
        handler: Any,
        path: str,
        body: dict[str, Any],
        actor: Any,
    ) -> None:
        if self._raises is not None:
            raise self._raises
        self.calls.append((path, dict(body), actor))
        handler._json_response(200, {"ok": True, "delegated_path": path})


class _RouteHarness:
    @classmethod
    def with_routes(cls, routes: MePostRoutes) -> RouteDispatchHarness:
        DefaultDispatcher.reset_for_tests()
        router = Router()
        cls._rebind(router, routes)
        return RouteDispatchHarness(RouterDispatcher(router))

    @classmethod
    def _rebind(cls, router: Router, routes: MePostRoutes) -> None:
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
    def _maybe_replacement(route: Any, routes: MePostRoutes) -> Any:
        if "MePostRoutes" not in route.display:
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


def _routes_with(**kwargs: Any) -> MePostRoutes:
    kwargs.setdefault("mutation_gate", _AlwaysAllowGate())
    kwargs.setdefault("actor_resolution", _StubActorResolution())
    return MePostRoutes(**kwargs)


# ---------------------------------------------------------------------------
# Self-service routes
# ---------------------------------------------------------------------------


class TestRevokeOthers:
    def test_delegates_with_correct_path(self) -> None:
        dispatcher = _StubSecurityDispatcher()
        repo = SecurityHandlersRepository(dispatcher=dispatcher)
        routes = _routes_with(repository=repo)
        harness = _RouteHarness.with_routes(routes)
        response = _dispatch_post(harness, "/api/me/revoke-others")
        assert response.status == 200
        assert dispatcher.calls
        assert dispatcher.calls[0][0] == "/api/me/revoke-others"


class TestThisWasntMe:
    def test_delegates_with_full_body(self) -> None:
        dispatcher = _StubSecurityDispatcher()
        repo = SecurityHandlersRepository(dispatcher=dispatcher)
        routes = _routes_with(repository=repo)
        harness = _RouteHarness.with_routes(routes)
        response = _dispatch_post(
            harness, "/api/me/this-wasnt-me",
            body={"flagged_ip": "1.2.3.4", "login_timestamp": "2026-01-01"},
        )
        assert response.status == 200
        path, body, _actor = dispatcher.calls[0]
        assert path == "/api/me/this-wasnt-me"
        assert body == {
            "flagged_ip": "1.2.3.4", "login_timestamp": "2026-01-01",
        }


class TestEmergencyRevokeAll:
    def test_delegates_with_reason(self) -> None:
        dispatcher = _StubSecurityDispatcher()
        repo = SecurityHandlersRepository(dispatcher=dispatcher)
        routes = _routes_with(repository=repo)
        harness = _RouteHarness.with_routes(routes)
        response = _dispatch_post(
            harness, "/api/emergency-revoke-all",
            body={"reason": "key compromise"},
        )
        assert response.status == 200
        path, body, _actor = dispatcher.calls[0]
        assert path == "/api/emergency-revoke-all"
        assert body == {"reason": "key compromise"}


# ---------------------------------------------------------------------------
# CSRF
# ---------------------------------------------------------------------------


class TestCsrf:
    def test_revoke_others_blocked(self) -> None:
        csrf_stub = MagicMock()
        csrf_stub.header_name = "X-CSRF-Token"
        csrf_stub.verify.return_value = False
        gate = PostMutationGate(csrf=csrf_stub)
        dispatcher = _StubSecurityDispatcher()
        repo = SecurityHandlersRepository(dispatcher=dispatcher)
        routes = MePostRoutes(
            mutation_gate=gate,
            repository=repo,
            actor_resolution=_StubActorResolution(),
        )
        harness = _RouteHarness.with_routes(routes)
        response = _dispatch_post(
            harness, "/api/me/revoke-others",
            headers={"Cookie": "media_stack_csrf=zzz"},
        )
        assert response.status == 403
        assert dispatcher.calls == []

    def test_emergency_revoke_blocked(self) -> None:
        csrf_stub = MagicMock()
        csrf_stub.header_name = "X-CSRF-Token"
        csrf_stub.verify.return_value = False
        gate = PostMutationGate(csrf=csrf_stub)
        dispatcher = _StubSecurityDispatcher()
        repo = SecurityHandlersRepository(dispatcher=dispatcher)
        routes = MePostRoutes(
            mutation_gate=gate,
            repository=repo,
            actor_resolution=_StubActorResolution(),
        )
        harness = _RouteHarness.with_routes(routes)
        response = _dispatch_post(
            harness, "/api/emergency-revoke-all",
            body={"reason": "test"},
            headers={"Cookie": "media_stack_csrf=zzz"},
        )
        assert response.status == 403
        assert dispatcher.calls == []


# ---------------------------------------------------------------------------
# Routing integration
# ---------------------------------------------------------------------------


class TestRoutingIntegration:
    _EXPECTED = frozenset({
        "/api/me/revoke-others",
        "/api/me/this-wasnt-me",
        "/api/emergency-revoke-all",
    })

    def test_routes_registered(self) -> None:
        harness = RouteDispatchHarness.with_default_router()
        registered = {
            r.path
            for r in harness._dispatcher._router.registered_routes()
            if r.verb == "POST" and "MePostRoutes" in r.display
        }
        assert registered == self._EXPECTED

    def test_default_constructor_wires_real_collaborators(self) -> None:
        instance = MePostRoutes()
        assert isinstance(instance._gate, PostMutationGate)
        assert isinstance(instance._repo, SecurityHandlersRepository)
