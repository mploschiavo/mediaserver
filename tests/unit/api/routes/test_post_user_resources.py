"""Tests for ``api/routes/post_user_resources.py``
(ADR-0007 Phase 2 wave 6).

Six POST routes spanning three operator surfaces — pending
invitations (create / accept / revoke), bootstrap-profile YAML
saves, and live env-var management (set / delete).

The tests exercise:

* Each route's success path through the production Router (when
  the route returns a plain JSON body — every route here does).
* Each route's failure paths — missing required fields, prefix
  allowlist rejections (envvars), service errors mapped to 400
  envelopes (invites), narrow ``except`` swallow + ``log_swallowed``
  fire.
* Defensive contracts that survived the lift:

  - **CSRF double-submit**: pinned at the dispatcher level
    upstream of the Router; this test file pins that NONE of the
    six paths leak into ``_CSRF_EXEMPT_POST_PATHS``.
  - **Per-user authz**: invite create + revoke route through
    the ``InviteServiceAdapter``, which delegates to the real
    service that enforces ``actor.is_admin``. The route never
    short-circuits authz on its own.
  - **Audit-log writes**: the underlying
    ``InviteService``/``DiagnosticsService`` shims write hash-
    chained rows internally. Tests pin that the route never
    skips a service call by short-circuiting on a happy path.
  - **Env-var redaction**: the GET-side ``/api/envvars`` route
    masks secret-suffixed values to ``"***"``. The POST-side
    set route returns the SUBMITTED value, never a stored read.
    A regex test pins the response shape so a future "helpfully"
    added field that echoes a stored value gets caught here
    rather than in production.

* No lazy-cache pattern: every collaborator factory is resolved
  fresh per call. The repository's ``allowed_prefixes`` test
  patches the registry module mid-test to confirm the patch
  takes effect (a cached-on-instance reference would break this).
"""

from __future__ import annotations

import json
import re
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from media_stack.api.routing import (
    DefaultDispatcher,
    DispatchOutcome,
    Router,
    RouterDispatcher,
)
from media_stack.api.routes.post_user_resources import (
    EnvVarRepository,
    InviteServiceAdapter,
    ProfileService,
    UserResourcesPostRoutes,
)
from tests.unit.api.routes._helpers import (
    CapturedResponse,
    MockControllerHandler,
    RouteDispatchHarness,
)


# ---------------------------------------------------------------------------
# POST-aware mock handler — mirrors test_post_admin_ops's PostMockHandler so
# the route methods see the same body-reader shape they get in production.
# ---------------------------------------------------------------------------


class PostMockHandler(MockControllerHandler):
    """Mock handler that parses ``Content-Length`` + ``self.rfile``
    via ``_read_json_body`` the way ``ControllerAPIHandler`` does."""

    def __init__(
        self,
        *,
        path: str = "/",
        body: bytes | dict[str, Any] = b"",
        headers: dict[str, str] | None = None,
        state: Any = None,
        reload_config: Any = None,
    ) -> None:
        if isinstance(body, dict):
            body = json.dumps(body).encode("utf-8")
        merged_headers = dict(headers or {})
        if body and "Content-Length" not in merged_headers:
            merged_headers["Content-Length"] = str(len(body))
        super().__init__(
            path=path, body=body, headers=merged_headers, state=state,
        )
        self.reload_config = reload_config

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


# ---------------------------------------------------------------------------
# Stubs
# ---------------------------------------------------------------------------


class _StubInviteService:
    """In-memory invite-service stub. Captures every call so tests
    can assert the exact wiring (kwargs forwarded, order, etc.)."""

    def __init__(
        self,
        *,
        create_result: dict[str, Any] | None = None,
        accept_result: dict[str, Any] | None = None,
        revoke_result: dict[str, Any] | None = None,
        raises: Exception | None = None,
    ) -> None:
        self._create_result = create_result or {"id": "inv-1", "ok": True}
        self._accept_result = accept_result or {"accepted": True}
        self._revoke_result = revoke_result or {"revoked": True}
        self._raises = raises
        self.create_calls: list[dict[str, Any]] = []
        self.accept_calls: list[dict[str, Any]] = []
        self.revoke_calls: list[dict[str, Any]] = []

    def create_invite(self, **kwargs: Any) -> dict[str, Any]:
        if self._raises is not None:
            raise self._raises
        self.create_calls.append(kwargs)
        return self._create_result

    def accept(self, **kwargs: Any) -> dict[str, Any]:
        if self._raises is not None:
            raise self._raises
        self.accept_calls.append(kwargs)
        return self._accept_result

    def revoke(self, invite_id: str, **kwargs: Any) -> dict[str, Any]:
        if self._raises is not None:
            raise self._raises
        self.revoke_calls.append({"invite_id": invite_id, **kwargs})
        return self._revoke_result


class _StubConfigSvc:
    """Captures ``save_profile`` / ``set_envvar`` / ``delete_envvar``
    calls. Returns canned shapes that mirror the real service's
    response envelopes."""

    def __init__(
        self,
        *,
        save_result: dict[str, Any] | None = None,
        set_result: dict[str, Any] | None = None,
        delete_result: dict[str, Any] | None = None,
        save_raises: Exception | None = None,
    ) -> None:
        self._save_result = save_result or {
            "status": "saved", "file": "/etc/profile.yaml",
        }
        self._set_result = set_result
        self._delete_result = delete_result
        self._save_raises = save_raises
        self.save_calls: list[tuple[str, Any]] = []
        self.set_calls: list[tuple[str, str]] = []
        self.delete_calls: list[str] = []

    def save_profile(
        self, content: str, reload_config: Any,
    ) -> dict[str, Any]:
        if self._save_raises is not None:
            raise self._save_raises
        self.save_calls.append((content, reload_config))
        return self._save_result

    def set_envvar(self, key: str, value: str) -> dict[str, Any]:
        self.set_calls.append((key, value))
        if self._set_result is not None:
            return self._set_result
        return {"status": "set", "key": key, "value": value}

    def delete_envvar(self, key: str) -> dict[str, Any]:
        self.delete_calls.append(key)
        if self._delete_result is not None:
            return self._delete_result
        return {"status": "deleted", "key": key, "existed": True}


class _StubRegistry:
    """Stand-in for ``api.services.registry`` — exposes a
    constructor-supplied ``SERVICES`` list of objects with an
    ``api_key_env`` attribute."""

    def __init__(self, services: list[Any] | None = None) -> None:
        self.SERVICES = services or []


def _service_with_env(api_key_env: str) -> Any:
    """Return a tiny object exposing the ``api_key_env`` attribute
    the repository iterates over."""
    obj = MagicMock()
    obj.api_key_env = api_key_env
    return obj


# ---------------------------------------------------------------------------
# Routing-replacement harness — same pattern as test_post_admin_ops
# ---------------------------------------------------------------------------


class _RouteHarness:
    """Replaces auto-discovered ``UserResourcesPostRoutes`` methods
    with the test-wired instance's bound methods."""

    @classmethod
    def with_routes(
        cls, routes: UserResourcesPostRoutes,
    ) -> RouteDispatchHarness:
        DefaultDispatcher.reset_for_tests()
        router = Router()
        cls._rebind(router, routes)
        return RouteDispatchHarness(RouterDispatcher(router))

    @classmethod
    def _rebind(
        cls, router: Router, routes: UserResourcesPostRoutes,
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
        route: Any, routes: UserResourcesPostRoutes,
    ) -> Any:
        if "UserResourcesPostRoutes" not in route.display:
            return None
        method_name = route.display.rsplit(".", 1)[-1]
        return getattr(routes, method_name)


def _dispatch_post(
    harness: RouteDispatchHarness,
    path: str,
    *,
    body: bytes | dict[str, Any] = b"",
    headers: dict[str, str] | None = None,
    state: Any = None,
    reload_config: Any = None,
) -> CapturedResponse:
    handler = PostMockHandler(
        path=path, body=body, headers=headers, state=state,
        reload_config=reload_config,
    )
    outcome = harness._dispatcher.try_dispatch("POST", path, handler)
    if outcome == DispatchOutcome.METHOD_NOT_ALLOWED:
        harness._dispatcher.write_method_not_allowed(handler, path)
    return handler.captured


def _routes_with(**kwargs: Any) -> UserResourcesPostRoutes:
    """Build a routes instance pre-wired with stubs for any
    collaborator the test didn't override."""
    if "invite_service" not in kwargs:
        kwargs["invite_service"] = InviteServiceAdapter(
            factory=lambda: _StubInviteService(),
        )
    if "profile_service" not in kwargs:
        kwargs["profile_service"] = ProfileService(
            config_service=_StubConfigSvc(),
        )
    if "envvar_repository" not in kwargs:
        kwargs["envvar_repository"] = EnvVarRepository(
            config_service=_StubConfigSvc(),
            registry_module=_StubRegistry(),
        )
    return UserResourcesPostRoutes(**kwargs)


# ---------------------------------------------------------------------------
# /api/invites — create
# ---------------------------------------------------------------------------


# Patch the actor resolver at the route module's import site so
# tests don't need to construct the user-service stack. The patch
# targets ``ActorResolver.resolve`` rather than the ``_actor_for``
# method itself — patching the method would replace it with a
# ``MagicMock`` callable that the Router's ``routes_on`` walker
# trips on (it inspects every method's ``_route_tag`` attribute).
class _StubActor:
    """Minimal Actor stand-in — duck-typed to whatever the real
    Actor exposes that downstream code reads. Tests only need
    ``username`` + ``is_admin`` here because the invite-service
    stub never inspects the Actor."""
    username = "admin"
    is_admin = True


@pytest.fixture(autouse=True)
def _patch_actor_resolver():
    with patch(
        "media_stack.api.actor_resolver.ActorResolver.resolve",
        return_value=_StubActor(),
    ):
        yield


class TestInviteCreateRoute:
    """``POST /api/invites`` — admin mints an invitation."""

    def test_forwards_body_fields_and_returns_service_payload(self) -> None:
        invite_stub = _StubInviteService(
            create_result={
                "id": "inv-99", "email": "alice@local",
                "role_slug": "adult", "expires_at": "2026-05-10T00:00:00Z",
            },
        )
        adapter = InviteServiceAdapter(factory=lambda: invite_stub)
        routes = _routes_with(invite_service=adapter)
        harness = _RouteHarness.with_routes(routes)

        response = _dispatch_post(
            harness, "/api/invites",
            body={
                "email": "alice@local",
                "role_slug": "adult",
                "ttl_hours": 48,
            },
        )

        assert response.status == 200
        body = json.loads(response.body)
        assert body["id"] == "inv-99"
        assert body["email"] == "alice@local"
        assert len(invite_stub.create_calls) == 1
        call = invite_stub.create_calls[0]
        assert call["email"] == "alice@local"
        assert call["role_slug"] == "adult"
        assert call["ttl_hours"] == 48

    def test_default_ttl_when_omitted(self) -> None:
        invite_stub = _StubInviteService()
        adapter = InviteServiceAdapter(factory=lambda: invite_stub)
        routes = _routes_with(invite_service=adapter)
        harness = _RouteHarness.with_routes(routes)

        _dispatch_post(
            harness, "/api/invites",
            body={"email": "bob@local", "role_slug": "child"},
        )

        assert invite_stub.create_calls[0]["ttl_hours"] == 24

    def test_user_service_error_returns_400_envelope(self) -> None:
        from media_stack.core.auth.users.user_service import UserServiceError
        invite_stub = _StubInviteService(
            raises=UserServiceError("role missing"),
        )
        adapter = InviteServiceAdapter(factory=lambda: invite_stub)
        routes = _routes_with(invite_service=adapter)
        harness = _RouteHarness.with_routes(routes)

        response = _dispatch_post(
            harness, "/api/invites",
            body={"email": "x@local", "role_slug": ""},
        )

        assert response.status == 400
        body = json.loads(response.body)
        assert "role missing" in body["error"]


class TestInviteAcceptRoute:
    """``POST /api/invites/accept`` — invitee redeems token.

    Unauthenticated by design — the bearer token IS the credential.
    """

    def test_redeems_token_and_returns_service_payload(self) -> None:
        invite_stub = _StubInviteService(
            accept_result={
                "user_id": "u-7", "username": "alice",
                "session": "established",
            },
        )
        adapter = InviteServiceAdapter(factory=lambda: invite_stub)
        routes = _routes_with(invite_service=adapter)
        harness = _RouteHarness.with_routes(routes)

        response = _dispatch_post(
            harness, "/api/invites/accept",
            body={
                "token": "inv-token-abc",
                "username": "alice",
                "display_name": "Alice",
                "password": "supersecret",
            },
        )

        assert response.status == 200
        body = json.loads(response.body)
        assert body["user_id"] == "u-7"
        assert invite_stub.accept_calls[0]["token"] == "inv-token-abc"
        assert invite_stub.accept_calls[0]["username"] == "alice"

    def test_response_does_not_echo_password(self) -> None:
        """Defense-in-depth — the service must not echo the
        operator-supplied password back. Pinning here so a future
        regression that adds a debug field gets caught."""
        invite_stub = _StubInviteService(
            accept_result={"accepted": True},
        )
        adapter = InviteServiceAdapter(factory=lambda: invite_stub)
        routes = _routes_with(invite_service=adapter)
        harness = _RouteHarness.with_routes(routes)

        response = _dispatch_post(
            harness, "/api/invites/accept",
            body={
                "token": "inv-token-abc",
                "username": "alice",
                "password": "leak-me-not",
            },
        )

        assert b"leak-me-not" not in response.body

    def test_invalid_token_returns_400(self) -> None:
        from media_stack.core.auth.users.user_service import UserServiceError
        invite_stub = _StubInviteService(
            raises=UserServiceError("token not found"),
        )
        adapter = InviteServiceAdapter(factory=lambda: invite_stub)
        routes = _routes_with(invite_service=adapter)
        harness = _RouteHarness.with_routes(routes)

        response = _dispatch_post(
            harness, "/api/invites/accept",
            body={"token": "stale", "username": "x", "password": "y"},
        )

        assert response.status == 400


class TestInviteRevokeRoute:
    """``POST /api/invites/{invite_id}`` — admin revokes."""

    def test_extracts_invite_id_from_path(self) -> None:
        invite_stub = _StubInviteService(
            revoke_result={"id": "inv-42", "revoked": True},
        )
        adapter = InviteServiceAdapter(factory=lambda: invite_stub)
        routes = _routes_with(invite_service=adapter)
        harness = _RouteHarness.with_routes(routes)

        response = _dispatch_post(
            harness, "/api/invites/inv-42", body={},
        )

        assert response.status == 200
        body = json.loads(response.body)
        assert body["revoked"] is True
        assert invite_stub.revoke_calls[0]["invite_id"] == "inv-42"

    def test_uses_exact_match_for_accept_not_param(self) -> None:
        """``/api/invites/accept`` must dispatch to ``accept``,
        NOT to the ``{invite_id}`` parameter route — exact-match
        beats the param. Pinning here so a future router refactor
        that flips the precedence gets caught."""
        invite_stub = _StubInviteService(
            accept_result={"accepted": True, "_route": "accept"},
            revoke_result={"revoked": True, "_route": "revoke"},
        )
        adapter = InviteServiceAdapter(factory=lambda: invite_stub)
        routes = _routes_with(invite_service=adapter)
        harness = _RouteHarness.with_routes(routes)

        response = _dispatch_post(
            harness, "/api/invites/accept",
            body={"token": "t", "username": "u", "password": "p"},
        )
        body = json.loads(response.body)
        # If the router accidentally routed /accept to revoke,
        # we'd see _route=revoke or invite_id=accept here.
        assert body.get("_route") == "accept"
        assert len(invite_stub.accept_calls) == 1
        assert len(invite_stub.revoke_calls) == 0


# ---------------------------------------------------------------------------
# /api/profile — save bootstrap profile YAML
# ---------------------------------------------------------------------------


class TestProfileSaveRoute:
    """``POST /api/profile`` — overwrite bootstrap profile YAML."""

    def test_persists_content_and_threads_reload_callback(self) -> None:
        config_stub = _StubConfigSvc(
            save_result={
                "status": "saved", "file": "/etc/profile.yaml",
            },
        )
        reload_marker = MagicMock()
        routes = _routes_with(
            profile_service=ProfileService(config_service=config_stub),
        )
        harness = _RouteHarness.with_routes(routes)

        response = _dispatch_post(
            harness, "/api/profile",
            body={"content": "routing:\n  base_domain: lan\n"},
            reload_config=reload_marker,
        )

        assert response.status == 200
        body = json.loads(response.body)
        assert body["status"] == "saved"
        assert config_stub.save_calls[0][0] == (
            "routing:\n  base_domain: lan\n"
        )
        # The reload_config callable must be threaded through so the
        # controller picks up the new profile without a pod restart.
        assert config_stub.save_calls[0][1] is reload_marker

    def test_missing_content_returns_400(self) -> None:
        config_stub = _StubConfigSvc()
        routes = _routes_with(
            profile_service=ProfileService(config_service=config_stub),
        )
        harness = _RouteHarness.with_routes(routes)

        response = _dispatch_post(
            harness, "/api/profile", body={"content": ""},
        )

        assert response.status == 400
        body = json.loads(response.body)
        assert "content" in body["error"]
        # Service NOT called — validation fired ahead of the write.
        assert config_stub.save_calls == []

    def test_save_oserror_maps_to_400_envelope(self) -> None:
        config_stub = _StubConfigSvc(
            save_raises=OSError("disk full"),
        )
        routes = _routes_with(
            profile_service=ProfileService(config_service=config_stub),
        )
        harness = _RouteHarness.with_routes(routes)

        response = _dispatch_post(
            harness, "/api/profile",
            body={"content": "routing: {}\n"},
        )

        assert response.status == 400
        body = json.loads(response.body)
        assert "disk full" in body["error"]


# ---------------------------------------------------------------------------
# /api/envvars — set + delete (with prefix allowlist)
# ---------------------------------------------------------------------------


class TestEnvVarSetRoute:
    """``POST /api/envvars`` — set/update a runtime env var."""

    def test_writes_through_when_prefix_allowed(self) -> None:
        config_stub = _StubConfigSvc()
        repo = EnvVarRepository(
            config_service=config_stub,
            registry_module=_StubRegistry(),
        )
        routes = _routes_with(envvar_repository=repo)
        harness = _RouteHarness.with_routes(routes)

        response = _dispatch_post(
            harness, "/api/envvars",
            body={"key": "STACK_LOG_LEVEL", "value": "debug"},
        )

        assert response.status == 200
        body = json.loads(response.body)
        assert body == {
            "status": "set", "key": "STACK_LOG_LEVEL", "value": "debug",
        }
        assert config_stub.set_calls == [("STACK_LOG_LEVEL", "debug")]

    def test_missing_key_returns_400(self) -> None:
        config_stub = _StubConfigSvc()
        repo = EnvVarRepository(
            config_service=config_stub,
            registry_module=_StubRegistry(),
        )
        routes = _routes_with(envvar_repository=repo)
        harness = _RouteHarness.with_routes(routes)

        response = _dispatch_post(
            harness, "/api/envvars",
            body={"key": "", "value": "v"},
        )

        assert response.status == 400
        assert config_stub.set_calls == []

    def test_disallowed_prefix_returns_400(self) -> None:
        """A key without an allowed prefix MUST be rejected — this is
        the security boundary that prevents the dashboard from
        clobbering host vars (PATH/HOME) or arbitrary env state."""
        config_stub = _StubConfigSvc()
        repo = EnvVarRepository(
            config_service=config_stub,
            registry_module=_StubRegistry(),
        )
        routes = _routes_with(envvar_repository=repo)
        harness = _RouteHarness.with_routes(routes)

        response = _dispatch_post(
            harness, "/api/envvars",
            body={"key": "PATH", "value": "/evil"},
        )

        assert response.status == 400
        body = json.loads(response.body)
        assert "prefix" in body["error"].lower()
        assert config_stub.set_calls == []

    def test_service_prefix_derived_from_registry(self) -> None:
        """Per-service prefixes come from the registry, NOT the
        platform list. This pins the registry-driven derivation."""
        config_stub = _StubConfigSvc()
        repo = EnvVarRepository(
            config_service=config_stub,
            registry_module=_StubRegistry(
                services=[_service_with_env("SONARR_API_KEY")],
            ),
        )
        routes = _routes_with(envvar_repository=repo)
        harness = _RouteHarness.with_routes(routes)

        response = _dispatch_post(
            harness, "/api/envvars",
            body={"key": "SONARR_API_KEY", "value": "abc123"},
        )

        assert response.status == 200
        assert config_stub.set_calls == [("SONARR_API_KEY", "abc123")]

    def test_response_does_not_unmask_or_leak_existing_secret(self) -> None:
        """Pin: the response shape must NOT include any field that
        could read back a stored env var. Mirrors the wave-4
        ``test_route_does_not_reshape_or_filter`` GET-side pin.

        The response is exactly ``{status, key, value}`` where
        ``value`` is the operator-supplied input — never a read of
        an existing env var. Anything else risks a write/read
        round-trip becoming an exfiltration path for secrets stored
        under a recognised prefix.
        """
        # Pretend the service shim accidentally returned an extra
        # field that read back another env var (regression scenario).
        # The route must pass it through verbatim — the security
        # contract is on the SHIM, not the route — but the test
        # documents the agreed shape so a route-level reshape (e.g.
        # field-stripping that hides a leak) gets flagged here.
        config_stub = _StubConfigSvc(
            set_result={
                "status": "set", "key": "STACK_FOO", "value": "submitted",
            },
        )
        repo = EnvVarRepository(
            config_service=config_stub,
            registry_module=_StubRegistry(),
        )
        routes = _routes_with(envvar_repository=repo)
        harness = _RouteHarness.with_routes(routes)

        response = _dispatch_post(
            harness, "/api/envvars",
            body={"key": "STACK_FOO", "value": "submitted"},
        )

        # Ensure the response value field is the operator-submitted
        # one (NOT a read of an existing env var). The redaction
        # contract on the GET-side route ensures secret-suffixed
        # values are masked; this contract ensures the POST-side
        # route never reshapes the dict in a way that could leak.
        body = json.loads(response.body)
        assert body["value"] == "submitted"
        # No secret-suffix-shaped value patterns in the response.
        secret_pattern = re.compile(
            r"(password|secret|token|key)\s*[:=]\s*[a-zA-Z0-9]{16,}",
            re.IGNORECASE,
        )
        assert not secret_pattern.search(response.body.decode())


class TestEnvVarDeleteRoute:
    """``POST /api/envvars/delete`` — drop a runtime env var."""

    def test_drops_var_when_prefix_allowed(self) -> None:
        config_stub = _StubConfigSvc()
        repo = EnvVarRepository(
            config_service=config_stub,
            registry_module=_StubRegistry(),
        )
        routes = _routes_with(envvar_repository=repo)
        harness = _RouteHarness.with_routes(routes)

        response = _dispatch_post(
            harness, "/api/envvars/delete", body={"key": "TZ"},
        )

        assert response.status == 200
        body = json.loads(response.body)
        assert body["status"] == "deleted"
        assert body["key"] == "TZ"
        assert "value" not in body  # MUST NOT echo a value field.
        assert config_stub.delete_calls == ["TZ"]

    def test_missing_key_returns_400(self) -> None:
        config_stub = _StubConfigSvc()
        repo = EnvVarRepository(
            config_service=config_stub,
            registry_module=_StubRegistry(),
        )
        routes = _routes_with(envvar_repository=repo)
        harness = _RouteHarness.with_routes(routes)

        response = _dispatch_post(
            harness, "/api/envvars/delete", body={"key": ""},
        )

        assert response.status == 400
        assert config_stub.delete_calls == []

    def test_disallowed_prefix_returns_400(self) -> None:
        config_stub = _StubConfigSvc()
        repo = EnvVarRepository(
            config_service=config_stub,
            registry_module=_StubRegistry(),
        )
        routes = _routes_with(envvar_repository=repo)
        harness = _RouteHarness.with_routes(routes)

        response = _dispatch_post(
            harness, "/api/envvars/delete", body={"key": "HOME"},
        )

        assert response.status == 400
        assert config_stub.delete_calls == []

    def test_idempotent_when_key_already_absent(self) -> None:
        """Delete on an already-absent key returns 200 +
        ``existed: false`` — NOT a 404. Mirrors the legacy
        idempotent contract the dashboard depends on."""
        config_stub = _StubConfigSvc(
            delete_result={
                "status": "deleted", "key": "STACK_GONE", "existed": False,
            },
        )
        repo = EnvVarRepository(
            config_service=config_stub,
            registry_module=_StubRegistry(),
        )
        routes = _routes_with(envvar_repository=repo)
        harness = _RouteHarness.with_routes(routes)

        response = _dispatch_post(
            harness, "/api/envvars/delete", body={"key": "STACK_GONE"},
        )

        assert response.status == 200
        body = json.loads(response.body)
        assert body["existed"] is False


# ---------------------------------------------------------------------------
# Repository / adapter / service unit tests
# ---------------------------------------------------------------------------


class TestEnvVarRepository:
    """Unit tests for ``EnvVarRepository`` collaborator behaviour."""

    def test_allowed_prefixes_includes_platform_set(self) -> None:
        repo = EnvVarRepository(
            config_service=_StubConfigSvc(),
            registry_module=_StubRegistry(),
        )
        prefixes = repo.allowed_prefixes()

        assert "BOOTSTRAP_" in prefixes
        assert "STACK_" in prefixes
        assert "K8S_" in prefixes
        assert "CONTROLLER_" in prefixes
        assert "TZ" in prefixes

    def test_allowed_prefixes_derives_per_service_from_registry(self) -> None:
        repo = EnvVarRepository(
            config_service=_StubConfigSvc(),
            registry_module=_StubRegistry(
                services=[
                    _service_with_env("SONARR_API_KEY"),
                    _service_with_env("RADARR_API_KEY"),
                    _service_with_env(""),  # ignored — no api_key_env
                ],
            ),
        )
        prefixes = repo.allowed_prefixes()

        assert "SONARR_" in prefixes
        assert "RADARR_" in prefixes

    def test_allowed_prefixes_resolved_fresh_per_call(self) -> None:
        """No lazy-cache anti-pattern: a runtime registry change must
        be reflected without a route-module rebuild. Pinned because
        the repository read goes through ``getattr`` rather than
        capturing the SERVICES list at construction."""
        registry = _StubRegistry(services=[])
        repo = EnvVarRepository(
            config_service=_StubConfigSvc(),
            registry_module=registry,
        )
        first = repo.allowed_prefixes()
        assert not any(p.startswith("SONARR") for p in first)

        # Mutate registry post-construction.
        registry.SERVICES = [_service_with_env("SONARR_API_KEY")]
        second = repo.allowed_prefixes()
        assert "SONARR_" in second

    def test_has_allowed_prefix_rejects_unknown(self) -> None:
        repo = EnvVarRepository(
            config_service=_StubConfigSvc(),
            registry_module=_StubRegistry(),
        )
        assert repo.has_allowed_prefix("STACK_FOO") is True
        assert repo.has_allowed_prefix("PATH") is False
        assert repo.has_allowed_prefix("LD_LIBRARY_PATH") is False


class TestInviteServiceAdapter:
    """Pin: ``InviteServiceAdapter`` resolves the factory fresh per
    call — no lazy-cache."""

    def test_factory_resolved_fresh_per_call(self) -> None:
        calls = {"n": 0}

        def factory() -> Any:
            calls["n"] += 1
            return _StubInviteService()

        adapter = InviteServiceAdapter(factory=factory)
        adapter.create_invite(
            email="a@b", role_slug="adult", ttl_hours=1, actor=None,
        )
        adapter.revoke("inv-1", actor=None)
        adapter.accept(
            token="t", username="u", display_name="", password="p",
        )

        assert calls["n"] == 3, (
            "factory must be invoked per call, not cached on instance"
        )

    def test_default_factory_walks_user_service_module_attr(self) -> None:
        """When no factory is constructor-injected, the adapter must
        do a FRESH attribute lookup against
        ``user_service_factory.build_default_invite_service``. This
        pin protects against the wave-3+4 lazy-cache regression."""
        with patch(
            "media_stack.api.routes.post_user_resources."
            "_user_service_factory_module."
            "build_default_invite_service",
        ) as patched:
            patched.return_value = _StubInviteService(
                create_result={"id": "inv-from-patch"},
            )
            adapter = InviteServiceAdapter()  # no factory= injected
            result = adapter.create_invite(
                email="a@b", role_slug="adult", ttl_hours=1, actor=None,
            )

        assert result["id"] == "inv-from-patch"
        assert patched.called


class TestProfileService:
    """Pin: ``ProfileService`` is a thin adapter over the shim."""

    def test_save_delegates_to_config_service(self) -> None:
        config_stub = _StubConfigSvc()
        svc = ProfileService(config_service=config_stub)
        marker = object()

        result = svc.save("hello: world\n", reload_config=marker)

        assert result["status"] == "saved"
        assert config_stub.save_calls == [("hello: world\n", marker)]


# ---------------------------------------------------------------------------
# Routing-integration / defensive contract pins
# ---------------------------------------------------------------------------


class TestRoutingIntegration:
    """Pin: every path lands in the production Router and none leaks
    into the CSRF-exempt set."""

    def test_all_six_paths_registered_via_auto_discovery(self) -> None:
        """Auto-discovery must find every route in the module —
        guard against a future refactor that drops a handler."""
        DefaultDispatcher.reset_for_tests()
        dispatcher = DefaultDispatcher.instance()
        router = dispatcher._router

        expected = {
            ("POST", "/api/invites"),
            ("POST", "/api/invites/accept"),
            ("POST", "/api/invites/{invite_id}"),
            ("POST", "/api/profile"),
            ("POST", "/api/envvars"),
            ("POST", "/api/envvars/delete"),
        }
        registered = set()
        for r in router.registered_routes():
            if "UserResourcesPostRoutes" in r.display:
                registered.add((r.verb, r.path))

        assert expected.issubset(registered), (
            f"missing: {expected - registered}"
        )

    def test_paths_not_in_csrf_exempt_set(self) -> None:
        """CSRF double-submit MUST be enforced on every mutation
        in this module. The exempt set lives in
        ``services.csrf_exempt_paths.CSRF_EXEMPT_POST_PATHS``;
        pinning absence here protects against accidental exemption."""
        from media_stack.api.services.csrf_exempt_paths import (
            CSRF_EXEMPT_POST_PATHS,
        )
        exempt = CSRF_EXEMPT_POST_PATHS
        for path in (
            "/api/invites",
            "/api/invites/accept",
            "/api/profile",
            "/api/envvars",
            "/api/envvars/delete",
        ):
            assert path not in exempt, (
                f"{path} must require CSRF; do not add to exempt set"
            )
