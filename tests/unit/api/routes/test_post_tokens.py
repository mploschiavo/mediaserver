"""Tests for ``api/routes/post_tokens.py``
(ADR-0007 Phase 2 wave 8 group 1).

Three routes:
* ``POST /api/tokens``
* ``POST /api/tokens/revoke-family``
* ``POST /api/tokens/{token_id}``
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock

from media_stack.api.routes.post_admin_ops import PostMutationGate
from media_stack.api.routes.post_tokens import (
    ApiTokenRepository,
    TokensPostRoutes,
)
from media_stack.api.routing import (
    DefaultDispatcher,
    DispatchOutcome,
    Router,
    RouterDispatcher,
)
from media_stack.core.auth.users.user_service import UserServiceError
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
    def __init__(self, username: str = "alice") -> None:
        self._actor = MagicMock(username=username, is_admin=True)

    def resolve(self, handler: Any, body: dict[str, Any]) -> Any:
        return self._actor


class _FakeToken:
    def __init__(self, token_id: str = "tok-1", **extra: Any) -> None:
        self._id = token_id
        self._extra = extra

    def to_dict(self) -> dict[str, Any]:
        return {"id": self._id, **self._extra}


class _StubTokenStore:
    def __init__(
        self,
        *,
        create_result: tuple[Any, str] | None = None,
        mint_pair_result: tuple[tuple[Any, str], tuple[Any, str]] | None = None,
        revoke_result: bool = True,
        revoke_family_result: int = 1,
        raises: Exception | None = None,
    ) -> None:
        self._create_result = create_result or (
            _FakeToken("tok-1", owner_username="alice"), "plaintext-1",
        )
        self._mint_pair_result = mint_pair_result or (
            (_FakeToken("acc-1", kind="access"), "access-plain"),
            (_FakeToken("ref-1", kind="refresh"), "refresh-plain"),
        )
        self._revoke_result = revoke_result
        self._revoke_family_result = revoke_family_result
        self._raises = raises
        self.create_calls: list[dict[str, Any]] = []
        self.mint_pair_calls: list[dict[str, Any]] = []
        self.revoke_calls: list[str] = []
        self.revoke_family_calls: list[str] = []

    def create(self, **kwargs: Any) -> Any:
        if self._raises is not None:
            raise self._raises
        self.create_calls.append(kwargs)
        return self._create_result

    def mint_pair(self, **kwargs: Any) -> Any:
        if self._raises is not None:
            raise self._raises
        self.mint_pair_calls.append(kwargs)
        return self._mint_pair_result

    def revoke(self, token_id: str) -> bool:
        if self._raises is not None:
            raise self._raises
        self.revoke_calls.append(token_id)
        return self._revoke_result

    def revoke_family(self, family_id: str) -> int:
        if self._raises is not None:
            raise self._raises
        self.revoke_family_calls.append(family_id)
        return self._revoke_family_result


class _RouteHarness:
    @classmethod
    def with_routes(cls, routes: TokensPostRoutes) -> RouteDispatchHarness:
        DefaultDispatcher.reset_for_tests()
        router = Router()
        cls._rebind(router, routes)
        return RouteDispatchHarness(RouterDispatcher(router))

    @classmethod
    def _rebind(cls, router: Router, routes: TokensPostRoutes) -> None:
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
    def _maybe_replacement(route: Any, routes: TokensPostRoutes) -> Any:
        if "TokensPostRoutes" not in route.display:
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


def _routes_with(**kwargs: Any) -> TokensPostRoutes:
    kwargs.setdefault("mutation_gate", _AlwaysAllowGate())
    kwargs.setdefault("actor_resolution", _StubActorResolution())
    return TokensPostRoutes(**kwargs)


# ---------------------------------------------------------------------------
# /api/tokens
# ---------------------------------------------------------------------------


class TestTokenCreate:
    def test_long_lived_default_path(self) -> None:
        store = _StubTokenStore()
        repo = ApiTokenRepository(store_builder=lambda: store)
        routes = _routes_with(repository=repo)
        harness = _RouteHarness.with_routes(routes)
        response = _dispatch_post(
            harness, "/api/tokens",
            body={"name": "ci-bot", "scope": "admin", "ttl_seconds": 3600},
        )
        assert response.status == 200
        body = json.loads(response.body)
        assert body["token"] == "plaintext-1"
        assert body["id"] == "tok-1"
        assert store.create_calls
        call = store.create_calls[0]
        assert call["name"] == "ci-bot"
        assert call["scope"] == "admin"
        assert call["ttl_seconds"] == 3600
        assert call["owner_username"] == "alice"

    def test_owner_username_from_body(self) -> None:
        store = _StubTokenStore()
        repo = ApiTokenRepository(store_builder=lambda: store)
        routes = _routes_with(repository=repo)
        harness = _RouteHarness.with_routes(routes)
        _dispatch_post(
            harness, "/api/tokens",
            body={"owner_username": "bob", "name": "bot"},
        )
        assert store.create_calls[0]["owner_username"] == "bob"

    def test_default_name_and_scope(self) -> None:
        store = _StubTokenStore()
        repo = ApiTokenRepository(store_builder=lambda: store)
        routes = _routes_with(repository=repo)
        harness = _RouteHarness.with_routes(routes)
        _dispatch_post(harness, "/api/tokens", body={})
        assert store.create_calls[0]["name"] == "api-token"
        assert store.create_calls[0]["scope"] == "admin"

    def test_refresh_pair_path(self) -> None:
        store = _StubTokenStore()
        repo = ApiTokenRepository(store_builder=lambda: store)
        routes = _routes_with(repository=repo)
        harness = _RouteHarness.with_routes(routes)
        response = _dispatch_post(
            harness, "/api/tokens",
            body={"kind": "refresh_pair", "name": "bot"},
        )
        assert response.status == 200
        body = json.loads(response.body)
        assert body["access"]["token"] == "access-plain"
        assert body["refresh"]["token"] == "refresh-plain"
        assert store.mint_pair_calls
        assert store.create_calls == []

    def test_invalid_ttl_seconds_falls_back_to_zero(self) -> None:
        store = _StubTokenStore()
        repo = ApiTokenRepository(store_builder=lambda: store)
        routes = _routes_with(repository=repo)
        harness = _RouteHarness.with_routes(routes)
        _dispatch_post(
            harness, "/api/tokens",
            body={"ttl_seconds": "not-a-number"},
        )
        assert store.create_calls[0]["ttl_seconds"] == 0

    def test_user_service_error_returns_400(self) -> None:
        store = _StubTokenStore(raises=UserServiceError("limit reached"))
        repo = ApiTokenRepository(store_builder=lambda: store)
        routes = _routes_with(repository=repo)
        harness = _RouteHarness.with_routes(routes)
        response = _dispatch_post(harness, "/api/tokens", body={})
        assert response.status == 400


# ---------------------------------------------------------------------------
# /api/tokens/revoke-family
# ---------------------------------------------------------------------------


class TestTokenFamilyRevoke:
    def test_happy_path(self) -> None:
        store = _StubTokenStore(revoke_family_result=3)
        repo = ApiTokenRepository(store_builder=lambda: store)
        routes = _routes_with(repository=repo)
        harness = _RouteHarness.with_routes(routes)
        response = _dispatch_post(
            harness, "/api/tokens/revoke-family",
            body={"family_id": "fam-1"},
        )
        assert response.status == 200
        body = json.loads(response.body)
        assert body == {
            "family_id": "fam-1", "revoked_count": 3, "actor": "alice",
        }
        assert store.revoke_family_calls == ["fam-1"]

    def test_missing_family_id_returns_400(self) -> None:
        store = _StubTokenStore()
        repo = ApiTokenRepository(store_builder=lambda: store)
        routes = _routes_with(repository=repo)
        harness = _RouteHarness.with_routes(routes)
        response = _dispatch_post(
            harness, "/api/tokens/revoke-family", body={"family_id": ""},
        )
        assert response.status == 400
        assert json.loads(response.body) == {"error": "family_id required"}
        assert store.revoke_family_calls == []


# ---------------------------------------------------------------------------
# /api/tokens/{token_id}
# ---------------------------------------------------------------------------


class TestTokenRevoke:
    def test_revoke_existing(self) -> None:
        store = _StubTokenStore(revoke_result=True)
        repo = ApiTokenRepository(store_builder=lambda: store)
        routes = _routes_with(repository=repo)
        harness = _RouteHarness.with_routes(routes)
        response = _dispatch_post(harness, "/api/tokens/tok-99")
        assert response.status == 200
        body = json.loads(response.body)
        assert body == {
            "token_id": "tok-99", "revoked": True, "actor": "alice",
        }
        assert store.revoke_calls == ["tok-99"]

    def test_revoke_unknown_returns_revoked_false(self) -> None:
        store = _StubTokenStore(revoke_result=False)
        repo = ApiTokenRepository(store_builder=lambda: store)
        routes = _routes_with(repository=repo)
        harness = _RouteHarness.with_routes(routes)
        response = _dispatch_post(harness, "/api/tokens/ghost")
        assert response.status == 200
        body = json.loads(response.body)
        assert body["revoked"] is False


# ---------------------------------------------------------------------------
# CSRF
# ---------------------------------------------------------------------------


class TestCsrf:
    def test_create_blocked_when_gate_rejects(self) -> None:
        csrf_stub = MagicMock()
        csrf_stub.header_name = "X-CSRF-Token"
        csrf_stub.verify.return_value = False
        gate = PostMutationGate(csrf=csrf_stub)
        store = _StubTokenStore()
        repo = ApiTokenRepository(store_builder=lambda: store)
        routes = TokensPostRoutes(
            mutation_gate=gate,
            repository=repo,
            actor_resolution=_StubActorResolution(),
        )
        harness = _RouteHarness.with_routes(routes)
        response = _dispatch_post(
            harness, "/api/tokens",
            body={"name": "x"},
            headers={"Cookie": "media_stack_csrf=zzz"},
        )
        assert response.status == 403
        assert store.create_calls == []


# ---------------------------------------------------------------------------
# Routing integration
# ---------------------------------------------------------------------------


class TestRoutingIntegration:
    _EXPECTED = frozenset({
        "/api/tokens",
        "/api/tokens/revoke-family",
        "/api/tokens/{token_id}",
    })

    def test_routes_registered(self) -> None:
        harness = RouteDispatchHarness.with_default_router()
        registered = {
            r.path
            for r in harness._dispatcher._router.registered_routes()
            if r.verb == "POST" and "TokensPostRoutes" in r.display
        }
        assert registered == self._EXPECTED

    def test_default_constructor_wires_real_collaborators(self) -> None:
        instance = TokensPostRoutes()
        assert isinstance(instance._gate, PostMutationGate)
        assert isinstance(instance._repo, ApiTokenRepository)
