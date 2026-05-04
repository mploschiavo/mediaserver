"""Tests for ``api/routes/post_bans.py`` (ADR-0007 Phase 2 wave 8 group 2).

Covers the four ban add/remove POST routes lifted off the legacy
``handlers_post`` elif chain. The route layer delegates business
logic to ``SecurityPostHandlers``, so these tests focus on the
delegation boundary:

* CSRF gate is enforced (403 when rejected, body untouched).
* Path params (``cidr`` / ``username``) are forwarded to the
  security handler with the canonical path string.
* Body parsing + actor resolution flow through.
* The four legacy paths are auto-discovered + registered with
  the Router.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock

from media_stack.api.routes.post_admin_ops import PostMutationGate
from media_stack.api.routes.post_bans import (
    BansPostRoutes,
    _ActorResolverProvider,
    _SecurityPostHandlerProvider,
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


# ---------------------------------------------------------------------------
# POST-aware mock handler
# ---------------------------------------------------------------------------


class PostMockHandler(MockControllerHandler):
    def __init__(
        self,
        *,
        path: str = "/",
        body: bytes | dict[str, Any] = b"",
        headers: dict[str, str] | None = None,
        state: Any = None,
    ) -> None:
        if isinstance(body, dict):
            body = json.dumps(body).encode("utf-8")
        merged_headers = dict(headers or {})
        if body and "Content-Length" not in merged_headers:
            merged_headers["Content-Length"] = str(len(body))
        super().__init__(
            path=path, body=body, headers=merged_headers, state=state,
        )

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
        raise AssertionError(
            "_AlwaysAllowGate.reject called — verify should have "
            "returned True",
        )


class _StubSecurityHandler:
    """Captures ``dispatch`` invocations + emits a sentinel JSON
    response so tests can assert the route layer forwarded
    correctly without exercising the full security stack."""

    def __init__(
        self,
        response_body: dict[str, Any] | None = None,
        response_status: int = 200,
    ) -> None:
        self._response_body = response_body or {"ok": True}
        self._response_status = response_status
        self.calls: list[tuple[str, dict[str, Any], Any]] = []

    def dispatch(
        self,
        handler: Any,
        path: str,
        body: dict[str, Any],
        actor: Any,
    ) -> None:
        self.calls.append((path, body, actor))
        handler._json_response(self._response_status, self._response_body)


class _StubActorResolver:
    """Returns a fixed sentinel actor object so tests can assert
    it flowed to the security handler unchanged."""

    def __init__(self, actor: Any = None) -> None:
        self.actor = actor or object()
        self.calls: list[tuple[Any, dict[str, Any]]] = []

    def resolve(self, handler: Any, body: dict[str, Any]) -> Any:
        self.calls.append((handler, body))
        return self.actor


# ---------------------------------------------------------------------------
# Harness — rebinds the auto-discovered BansPostRoutes
# ---------------------------------------------------------------------------


class _RouteHarness:
    @classmethod
    def with_routes(
        cls, routes: BansPostRoutes,
    ) -> RouteDispatchHarness:
        DefaultDispatcher.reset_for_tests()
        router = Router()
        cls._rebind(router, routes)
        return RouteDispatchHarness(RouterDispatcher(router))

    @classmethod
    def _rebind(
        cls, router: Router, routes: BansPostRoutes,
    ) -> None:
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
    def _maybe_replacement(
        route: Any, routes: BansPostRoutes,
    ) -> Any:
        if "BansPostRoutes" not in route.display:
            return None
        method_name = route.display.rsplit(".", 1)[-1]
        return getattr(routes, method_name)


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


def _routes_with(
    *, security: _StubSecurityHandler, actor: _StubActorResolver,
) -> BansPostRoutes:
    return BansPostRoutes(
        mutation_gate=_AlwaysAllowGate(),
        security_handler_provider=_SecurityPostHandlerProvider(
            getter=lambda: security,
        ),
        actor_resolver_provider=_ActorResolverProvider(
            resolver=actor,
        ),
    )


# ---------------------------------------------------------------------------
# /api/bans/ips — add IP ban
# ---------------------------------------------------------------------------


class TestAddIpBanRoute:
    def test_forwards_body_and_actor(self) -> None:
        sec = _StubSecurityHandler(response_body={
            "ok": True, "cidr": "10.0.0.0/24",
        })
        actor = _StubActorResolver()
        harness = _RouteHarness.with_routes(_routes_with(
            security=sec, actor=actor,
        ))
        response = _dispatch_post(
            harness, "/api/bans/ips",
            body={"cidr": "10.0.0.0/24", "reason": "abuse"},
        )

        assert response.status == 200
        assert json.loads(response.body) == {
            "ok": True, "cidr": "10.0.0.0/24",
        }
        assert len(sec.calls) == 1
        path, body, dispatched_actor = sec.calls[0]
        assert path == "/api/bans/ips"
        assert body == {"cidr": "10.0.0.0/24", "reason": "abuse"}
        assert dispatched_actor is actor.actor

    def test_empty_body_forwarded_as_empty_dict(self) -> None:
        sec = _StubSecurityHandler()
        actor = _StubActorResolver()
        harness = _RouteHarness.with_routes(_routes_with(
            security=sec, actor=actor,
        ))
        _dispatch_post(harness, "/api/bans/ips", body=b"")

        assert sec.calls[0][1] == {}


# ---------------------------------------------------------------------------
# /api/bans/ips/{cidr}/remove
# ---------------------------------------------------------------------------


class TestRemoveIpBanRoute:
    def test_forwards_canonical_path_with_cidr(self) -> None:
        sec = _StubSecurityHandler(response_body={"ok": True})
        actor = _StubActorResolver()
        harness = _RouteHarness.with_routes(_routes_with(
            security=sec, actor=actor,
        ))
        # Use a CIDR with no slashes — slashes would be a path-
        # delimiter conflict; the legacy code matched the regex
        # ``/api/bans/ips/(?P<cidr>[^/]+)/remove`` so the test
        # uses the same single-segment encoding (``10.0.0.0_24``-
        # style is what the UI must POST).
        response = _dispatch_post(
            harness, "/api/bans/ips/10.0.0.0_24/remove",
        )

        assert response.status == 200
        assert sec.calls[0][0] == "/api/bans/ips/10.0.0.0_24/remove"


# ---------------------------------------------------------------------------
# /api/bans/users
# ---------------------------------------------------------------------------


class TestAddUserBanRoute:
    def test_forwards_body(self) -> None:
        sec = _StubSecurityHandler(response_body={
            "ok": True, "username": "alice",
        })
        actor = _StubActorResolver()
        harness = _RouteHarness.with_routes(_routes_with(
            security=sec, actor=actor,
        ))
        response = _dispatch_post(
            harness, "/api/bans/users",
            body={"username": "alice", "reason": "policy"},
        )

        assert response.status == 200
        assert sec.calls[0][1] == {"username": "alice", "reason": "policy"}
        assert sec.calls[0][0] == "/api/bans/users"


# ---------------------------------------------------------------------------
# /api/bans/users/{username}/remove
# ---------------------------------------------------------------------------


class TestRemoveUserBanRoute:
    def test_forwards_canonical_path_with_username(self) -> None:
        sec = _StubSecurityHandler(response_body={"ok": True})
        actor = _StubActorResolver()
        harness = _RouteHarness.with_routes(_routes_with(
            security=sec, actor=actor,
        ))
        response = _dispatch_post(
            harness, "/api/bans/users/alice/remove",
        )

        assert response.status == 200
        assert sec.calls[0][0] == "/api/bans/users/alice/remove"

    def test_unicode_username_passes_through(self) -> None:
        """Path regex captures non-slash chars verbatim — pin
        that a Unicode username makes it through without
        re-encoding."""
        sec = _StubSecurityHandler()
        actor = _StubActorResolver()
        harness = _RouteHarness.with_routes(_routes_with(
            security=sec, actor=actor,
        ))
        _dispatch_post(harness, "/api/bans/users/álice/remove")

        assert sec.calls[0][0] == "/api/bans/users/álice/remove"


# ---------------------------------------------------------------------------
# CSRF gate — security regression coverage
# ---------------------------------------------------------------------------


class TestCsrfGate:
    def test_add_user_ban_blocked_when_gate_rejects(self) -> None:
        csrf_stub = MagicMock()
        csrf_stub.header_name = "X-CSRF-Token"
        csrf_stub.verify.return_value = False
        gate = PostMutationGate(csrf=csrf_stub)
        sec = _StubSecurityHandler()
        actor = _StubActorResolver()

        routes = BansPostRoutes(
            mutation_gate=gate,
            security_handler_provider=_SecurityPostHandlerProvider(
                getter=lambda: sec,
            ),
            actor_resolver_provider=_ActorResolverProvider(
                resolver=actor,
            ),
        )
        harness = _RouteHarness.with_routes(routes)
        response = _dispatch_post(
            harness, "/api/bans/users",
            body={"username": "x"},
            headers={"Cookie": "media_stack_csrf=zzz"},
        )

        assert response.status == 403
        assert sec.calls == []

    def test_remove_ip_ban_blocked_when_gate_rejects(self) -> None:
        csrf_stub = MagicMock()
        csrf_stub.header_name = "X-CSRF-Token"
        csrf_stub.verify.return_value = False
        gate = PostMutationGate(csrf=csrf_stub)
        sec = _StubSecurityHandler()
        actor = _StubActorResolver()

        routes = BansPostRoutes(
            mutation_gate=gate,
            security_handler_provider=_SecurityPostHandlerProvider(
                getter=lambda: sec,
            ),
            actor_resolver_provider=_ActorResolverProvider(
                resolver=actor,
            ),
        )
        harness = _RouteHarness.with_routes(routes)
        response = _dispatch_post(
            harness, "/api/bans/ips/x/remove",
            headers={"Cookie": "media_stack_csrf=zzz"},
        )

        assert response.status == 403
        assert sec.calls == []


# ---------------------------------------------------------------------------
# Provider default-path coverage
# ---------------------------------------------------------------------------


class TestProviderDefaultPath:
    def test_security_handler_provider_default_path(self) -> None:
        provider = _SecurityPostHandlerProvider()
        from media_stack.api.services.security_post_handlers import (
            _security_post_handlers,
        )
        assert provider.get() is _security_post_handlers

    def test_actor_resolver_provider_default_path(
        self, monkeypatch,
    ) -> None:
        from media_stack.api import handlers_post

        captured: dict[str, Any] = {}

        class _StubResolver:
            def resolve(self, handler: Any, body: dict[str, Any]) -> Any:
                captured["handler"] = handler
                captured["body"] = body
                return "ACTOR"

        monkeypatch.setattr(
            handlers_post, "_actor_resolver", _StubResolver(),
        )
        provider = _ActorResolverProvider()
        result = provider.resolve("HANDLER", {"k": "v"})
        assert result == "ACTOR"
        assert captured == {"handler": "HANDLER", "body": {"k": "v"}}


# ---------------------------------------------------------------------------
# Routing integration — auto-discovery + spec-parity
# ---------------------------------------------------------------------------


class TestRoutingIntegration:
    _EXPECTED = frozenset({
        "/api/bans/ips",
        "/api/bans/ips/{cidr}/remove",
        "/api/bans/users",
        "/api/bans/users/{username}/remove",
    })

    def test_all_bans_post_routes_registered(self) -> None:
        harness = RouteDispatchHarness.with_default_router()
        registered = {
            r.path
            for r in harness._dispatcher._router.registered_routes()
            if r.verb == "POST"
            and "BansPostRoutes" in r.display
        }
        assert registered == self._EXPECTED

    def test_default_constructor_wires_real_collaborators(self) -> None:
        instance = BansPostRoutes()
        assert isinstance(instance._gate, PostMutationGate)
        assert isinstance(
            instance._security_handler, _SecurityPostHandlerProvider,
        )
        assert isinstance(
            instance._actor_resolver, _ActorResolverProvider,
        )
