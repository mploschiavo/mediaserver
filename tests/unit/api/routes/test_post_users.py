"""Tests for ``api/routes/post_users.py`` (ADR-0007 Phase 2 wave 8 group 1).

Covers the eight POST routes lifted off the legacy
``handlers_post.PostRequestHandler._dispatch_user_mgmt`` chain.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock

import pytest

from media_stack.api.routes.post_admin_ops import PostMutationGate
from media_stack.api.routes.post_users import (
    ActorResolution,
    LegacyHelperAdapter,
    UserMgmtRepository,
    UsersPostRoutes,
    _strip_legacy_plaintext,
)
from media_stack.api.routing import (
    DefaultDispatcher,
    DispatchOutcome,
    Router,
    RouterDispatcher,
)
from media_stack.core.auth.users.models import UserState
from media_stack.core.auth.users.user_service import UserServiceError
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
        raise AssertionError("reject should not be called")


class _StubActorResolution:
    """Returns a fake actor without any resolver wiring."""

    def __init__(self, actor: Any = None) -> None:
        self._actor = actor or MagicMock(username="alice", is_admin=True)

    def resolve(self, handler: Any, body: dict[str, Any]) -> Any:
        return self._actor


class _StubUserService:
    """Captures every call. Tests pin via ``raises`` /
    ``return_value`` and assert call records."""

    def __init__(
        self,
        *,
        result: Any = None,
        raises: Exception | None = None,
    ) -> None:
        self._result = result if result is not None else {"ok": True}
        self._raises = raises
        self.calls: list[dict[str, Any]] = []

    def _record(self, method: str, **kwargs: Any) -> Any:
        if self._raises is not None:
            raise self._raises
        self.calls.append({"method": method, **kwargs})
        return self._result

    def create_user(self, **kwargs: Any) -> Any:
        return self._record("create_user", **kwargs)

    def import_orphan(self, **kwargs: Any) -> Any:
        return self._record("import_orphan", **kwargs)

    def unlink_ghost(self, **kwargs: Any) -> Any:
        return self._record("unlink_ghost", **kwargs)

    def delete_user(self, user_id: str, *, actor: Any) -> Any:
        return self._record(
            "delete_user", user_id=user_id, actor=actor,
        )

    def reset_password(
        self, user_id: str, *, password: str, actor: Any,
    ) -> Any:
        return self._record(
            "reset_password", user_id=user_id,
            password=password, actor=actor,
        )

    def set_role(self, user_id: str, role_slug: str, *, actor: Any) -> Any:
        return self._record(
            "set_role", user_id=user_id,
            role_slug=role_slug, actor=actor,
        )

    def set_state(
        self, user_id: str, state: UserState, *, actor: Any,
    ) -> Any:
        return self._record(
            "set_state", user_id=user_id, state=state, actor=actor,
        )


class _StubLegacyHelper:
    def __init__(self, *, raises: Exception | None = None) -> None:
        self._raises = raises
        self.bulk_calls: list[Any] = []
        self.revoke_calls: list[Any] = []

    def bulk_import(
        self, svc: Any, body: dict[str, Any], actor: Any,
    ) -> dict[str, Any]:
        if self._raises is not None:
            raise self._raises
        self.bulk_calls.append((svc, dict(body), actor))
        return {
            "imported": [{"email": "x@example.com", "user_id": "u1"}],
            "errors": [], "count": 1,
        }

    def revoke_sessions(
        self, svc: Any, user_id: str, actor: Any,
    ) -> dict[str, Any]:
        if self._raises is not None:
            raise self._raises
        self.revoke_calls.append((svc, user_id, actor))
        return {"user_id": user_id, "providers": {"jellyfin": "ok"}}


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------


class _RouteHarness:
    @classmethod
    def with_routes(cls, routes: UsersPostRoutes) -> RouteDispatchHarness:
        DefaultDispatcher.reset_for_tests()
        router = Router()
        cls._rebind(router, routes)
        return RouteDispatchHarness(RouterDispatcher(router))

    @classmethod
    def _rebind(cls, router: Router, routes: UsersPostRoutes) -> None:
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
    def _maybe_replacement(route: Any, routes: UsersPostRoutes) -> Any:
        if "UsersPostRoutes" not in route.display:
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


def _routes_with(**kwargs: Any) -> UsersPostRoutes:
    kwargs.setdefault("mutation_gate", _AlwaysAllowGate())
    kwargs.setdefault("actor_resolution", _StubActorResolution())
    return UsersPostRoutes(**kwargs)


# ---------------------------------------------------------------------------
# /api/users (create)
# ---------------------------------------------------------------------------


class TestUserCreate:
    def test_happy_path_forwards_full_body(self) -> None:
        svc = _StubUserService(result={"id": "u1", "email": "a@b.com"})
        routes = _routes_with(
            repository=UserMgmtRepository(service_builder=lambda: svc),
        )
        harness = _RouteHarness.with_routes(routes)
        response = _dispatch_post(
            harness, "/api/users",
            body={
                "email": "a@b.com", "username": "alice",
                "display_name": "Alice", "role_slug": "adult",
                "password": "secret",
            },
        )

        assert response.status == 200
        body = json.loads(response.body)
        assert body == {"id": "u1", "email": "a@b.com"}
        assert svc.calls[0]["method"] == "create_user"
        assert svc.calls[0]["email"] == "a@b.com"
        assert svc.calls[0]["username"] == "alice"
        assert svc.calls[0]["role_slug"] == "adult"
        assert svc.calls[0]["password"] == "secret"

    def test_user_service_error_returns_400(self) -> None:
        svc = _StubUserService(raises=UserServiceError("bad email"))
        routes = _routes_with(
            repository=UserMgmtRepository(service_builder=lambda: svc),
        )
        harness = _RouteHarness.with_routes(routes)
        response = _dispatch_post(
            harness, "/api/users", body={"email": ""},
        )
        assert response.status == 400
        assert json.loads(response.body) == {"error": "bad email"}

    def test_strips_legacy_plaintext_to_ticket(self) -> None:
        from unittest.mock import patch
        svc = _StubUserService(result={
            "id": "u1", "user_id": "u1",
            "generated_password": "leak-me",
        })
        routes = _routes_with(
            repository=UserMgmtRepository(service_builder=lambda: svc),
        )
        harness = _RouteHarness.with_routes(routes)
        with patch(
            "media_stack.core.auth.users.password_ticket_store"
            ".mint_ticket_fields",
            return_value={
                "password_ticket": "tkt-1",
                "ticket_expires_at": "2026-01-01",
            },
        ):
            response = _dispatch_post(
                harness, "/api/users", body={"email": "a@b.com"},
            )
        body = json.loads(response.body)
        assert "generated_password" not in body
        assert body["password_ticket"] == "tkt-1"

    def test_csrf_gate_rejects(self) -> None:
        csrf_stub = MagicMock()
        csrf_stub.header_name = "X-CSRF-Token"
        csrf_stub.verify.return_value = False
        gate = PostMutationGate(csrf=csrf_stub)
        svc = _StubUserService()
        routes = UsersPostRoutes(
            mutation_gate=gate,
            repository=UserMgmtRepository(service_builder=lambda: svc),
            actor_resolution=_StubActorResolution(),
        )
        harness = _RouteHarness.with_routes(routes)
        response = _dispatch_post(
            harness, "/api/users",
            body={"email": "x@y.com"},
            headers={"Cookie": "media_stack_csrf=zzz"},
        )
        assert response.status == 403
        assert svc.calls == []


# ---------------------------------------------------------------------------
# /api/users-bulk-import
# ---------------------------------------------------------------------------


class TestBulkImport:
    def test_happy_path(self) -> None:
        helper = _StubLegacyHelper()
        svc = _StubUserService()
        routes = _routes_with(
            repository=UserMgmtRepository(service_builder=lambda: svc),
            legacy_helper=helper,
        )
        harness = _RouteHarness.with_routes(routes)
        response = _dispatch_post(
            harness, "/api/users-bulk-import",
            body={"users": [{"email": "x@example.com"}]},
        )
        assert response.status == 200
        body = json.loads(response.body)
        assert body["count"] == 1
        assert helper.bulk_calls[0][1] == {
            "users": [{"email": "x@example.com"}],
        }

    def test_user_service_error_returns_400(self) -> None:
        helper = _StubLegacyHelper(raises=UserServiceError("bad input"))
        svc = _StubUserService()
        routes = _routes_with(
            repository=UserMgmtRepository(service_builder=lambda: svc),
            legacy_helper=helper,
        )
        harness = _RouteHarness.with_routes(routes)
        response = _dispatch_post(
            harness, "/api/users-bulk-import", body={"users": []},
        )
        assert response.status == 400


# ---------------------------------------------------------------------------
# /api/users-reconcile/{import,unlink}
# ---------------------------------------------------------------------------


class TestReconcileImport:
    def test_happy_path(self) -> None:
        svc = _StubUserService(result={"linked": True})
        routes = _routes_with(
            repository=UserMgmtRepository(service_builder=lambda: svc),
        )
        harness = _RouteHarness.with_routes(routes)
        response = _dispatch_post(
            harness, "/api/users-reconcile/import",
            body={
                "provider_name": "jellyfin",
                "external_id": "ext-1",
                "role_slug": "adult",
            },
        )
        assert response.status == 200
        assert json.loads(response.body) == {"linked": True}
        call = svc.calls[0]
        assert call["method"] == "import_orphan"
        assert call["provider_name"] == "jellyfin"
        assert call["external_id"] == "ext-1"


class TestReconcileUnlink:
    def test_happy_path(self) -> None:
        svc = _StubUserService(result={"unlinked": True})
        routes = _routes_with(
            repository=UserMgmtRepository(service_builder=lambda: svc),
        )
        harness = _RouteHarness.with_routes(routes)
        response = _dispatch_post(
            harness, "/api/users-reconcile/unlink",
            body={"user_id": "u1", "provider_name": "jellyfin"},
        )
        assert response.status == 200
        assert json.loads(response.body) == {"unlinked": True}
        assert svc.calls[0]["method"] == "unlink_ghost"
        assert svc.calls[0]["user_id"] == "u1"


# ---------------------------------------------------------------------------
# /api/users/{user_id}/{delete,reset-password,revoke-sessions,role,state}
# ---------------------------------------------------------------------------


class TestUserDelete:
    def test_happy_path_passes_user_id_from_path(self) -> None:
        svc = _StubUserService(result={"deleted": True})
        routes = _routes_with(
            repository=UserMgmtRepository(service_builder=lambda: svc),
        )
        harness = _RouteHarness.with_routes(routes)
        response = _dispatch_post(harness, "/api/users/u-42/delete")
        assert response.status == 200
        assert svc.calls[0]["user_id"] == "u-42"
        assert svc.calls[0]["method"] == "delete_user"


class TestUserResetPassword:
    def test_happy_path(self) -> None:
        svc = _StubUserService(result={"reset": True})
        routes = _routes_with(
            repository=UserMgmtRepository(service_builder=lambda: svc),
        )
        harness = _RouteHarness.with_routes(routes)
        response = _dispatch_post(
            harness, "/api/users/u-99/reset-password",
            body={"password": "newpass"},
        )
        assert response.status == 200
        assert svc.calls[0]["method"] == "reset_password"
        assert svc.calls[0]["user_id"] == "u-99"
        assert svc.calls[0]["password"] == "newpass"

    def test_strips_legacy_plaintext(self) -> None:
        from unittest.mock import patch
        svc = _StubUserService(result={
            "user_id": "u-99",
            "generated_password": "should-not-leak",
        })
        routes = _routes_with(
            repository=UserMgmtRepository(service_builder=lambda: svc),
        )
        harness = _RouteHarness.with_routes(routes)
        with patch(
            "media_stack.core.auth.users.password_ticket_store"
            ".mint_ticket_fields",
            return_value={"password_ticket": "tkt"},
        ):
            response = _dispatch_post(
                harness, "/api/users/u-99/reset-password", body={},
            )
        body = json.loads(response.body)
        assert "generated_password" not in body


class TestUserRevokeSessions:
    def test_happy_path(self) -> None:
        helper = _StubLegacyHelper()
        svc = _StubUserService()
        routes = _routes_with(
            repository=UserMgmtRepository(service_builder=lambda: svc),
            legacy_helper=helper,
        )
        harness = _RouteHarness.with_routes(routes)
        response = _dispatch_post(
            harness, "/api/users/u-1/revoke-sessions",
        )
        assert response.status == 200
        body = json.loads(response.body)
        assert body == {"user_id": "u-1", "providers": {"jellyfin": "ok"}}
        assert helper.revoke_calls[0][1] == "u-1"


class TestUserSetRole:
    def test_happy_path(self) -> None:
        svc = _StubUserService(result={"role": "child"})
        routes = _routes_with(
            repository=UserMgmtRepository(service_builder=lambda: svc),
        )
        harness = _RouteHarness.with_routes(routes)
        response = _dispatch_post(
            harness, "/api/users/u-1/role",
            body={"role_slug": "child"},
        )
        assert response.status == 200
        assert svc.calls[0]["method"] == "set_role"
        assert svc.calls[0]["role_slug"] == "child"

    def test_user_service_error_400(self) -> None:
        svc = _StubUserService(raises=UserServiceError("unknown role"))
        routes = _routes_with(
            repository=UserMgmtRepository(service_builder=lambda: svc),
        )
        harness = _RouteHarness.with_routes(routes)
        response = _dispatch_post(
            harness, "/api/users/u-1/role",
            body={"role_slug": "nope"},
        )
        assert response.status == 400


class TestUserSetState:
    def test_happy_path_active(self) -> None:
        svc = _StubUserService(result={"state": "active"})
        routes = _routes_with(
            repository=UserMgmtRepository(service_builder=lambda: svc),
        )
        harness = _RouteHarness.with_routes(routes)
        response = _dispatch_post(
            harness, "/api/users/u-1/state",
            body={"state": "active"},
        )
        assert response.status == 200
        assert svc.calls[0]["method"] == "set_state"
        assert isinstance(svc.calls[0]["state"], UserState)

    def test_invalid_state_returns_400(self) -> None:
        svc = _StubUserService()
        routes = _routes_with(
            repository=UserMgmtRepository(service_builder=lambda: svc),
        )
        harness = _RouteHarness.with_routes(routes)
        response = _dispatch_post(
            harness, "/api/users/u-1/state",
            body={"state": "not-a-real-state"},
        )
        # ValueError on UserState() construction is narrow-caught
        # and returns 400 with the parse error.
        assert response.status == 400


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class TestStripLegacyPlaintext:
    def test_swaps_plaintext_for_ticket(self) -> None:
        from unittest.mock import patch
        with patch(
            "media_stack.core.auth.users.password_ticket_store"
            ".mint_ticket_fields",
            return_value={"password_ticket": "tkt"},
        ):
            result = _strip_legacy_plaintext({
                "user_id": "u1",
                "generated_password": "secret",
            })
        assert result == {"user_id": "u1", "password_ticket": "tkt"}

    def test_passthrough_when_no_plaintext(self) -> None:
        result = _strip_legacy_plaintext({"id": "u1", "ok": True})
        assert result == {"id": "u1", "ok": True}

    def test_passthrough_when_not_dict(self) -> None:
        assert _strip_legacy_plaintext(None) is None


# ---------------------------------------------------------------------------
# Routing integration
# ---------------------------------------------------------------------------


class TestRoutingIntegration:
    _EXPECTED = frozenset({
        "/api/users",
        "/api/users-bulk-import",
        "/api/users-reconcile/import",
        "/api/users-reconcile/unlink",
        "/api/users/{user_id}/delete",
        "/api/users/{user_id}/reset-password",
        "/api/users/{user_id}/revoke-sessions",
        "/api/users/{user_id}/role",
        "/api/users/{user_id}/state",
    })

    def test_all_routes_registered(self) -> None:
        harness = RouteDispatchHarness.with_default_router()
        registered = {
            r.path
            for r in harness._dispatcher._router.registered_routes()
            if r.verb == "POST" and "UsersPostRoutes" in r.display
        }
        assert registered == self._EXPECTED, (
            f"missing: {self._EXPECTED - registered}, "
            f"unexpected: {registered - self._EXPECTED}"
        )

    def test_default_constructor_wires_real_collaborators(self) -> None:
        instance = UsersPostRoutes()
        assert isinstance(instance._gate, PostMutationGate)
        assert isinstance(instance._repo, UserMgmtRepository)
        assert isinstance(instance._actor, ActorResolution)
        assert isinstance(instance._helper, LegacyHelperAdapter)
