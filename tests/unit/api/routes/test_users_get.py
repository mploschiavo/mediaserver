"""Tests for ``api/routes/users_get.py`` (ADR-0007 Phase 2 wave 5).

Each route gets its own test class. Routes are exercised through
the production ``Router`` via ``RouteDispatchHarness.with_default_router()``
where possible (proves auto-discovery + spec-parity); the
parameterized + delegating routes also have direct-instantiation
tests where a stub repository is more ergonomic than patching the
``UserService`` factory.

Patching strategy:

* Module-level ``user_service_factory`` (re-exported via
  ``media_stack.core.auth.users``) is patched per-test so the
  production ``UserRepository`` -> ``build_default_service()``
  chain stays exercised. The route module never caches a resolved
  factory reference; the ``UserRepository._service()`` /
  ``_invites()`` / ``_tokens()`` helpers do fresh attribute reads.
* The login-history route delegates to the existing
  ``_SessionVisibilityGetHelper`` so the actor-resolution +
  security-report-service wiring is preserved 1:1; tests inject a
  stub helper at the route's constructor boundary so the security
  subsystem is not exercised here.

Security-relevant assertions are folded into the per-route classes
(audit-log error envelope, login-history rate-limit, ``/api/me``
``needs_rotation`` gate, tokens-list shape).
"""

from __future__ import annotations

import base64
import json
from typing import Any
from unittest.mock import patch

from tests.unit.api.routes._helpers import (
    MockControllerHandler,
    RouteDispatchHarness,
)


# --- Stub collaborators ---------------------------------------------


class _StubUserService:
    """Minimal ``UserService``-shaped stub for the routes that go
    through ``UserRepository._service()``.

    Tests pass a kwargs dict per method to drive the response shape
    without recreating the full ``UserService`` lifecycle (which
    would require sqlite + audit-log fixtures)."""

    def __init__(
        self,
        *,
        users: list[dict[str, Any]] | None = None,
        roles: list[dict[str, Any]] | None = None,
        providers: list[dict[str, Any]] | None = None,
        reconcile: list[dict[str, Any]] | None = None,
        audit_entries: list[dict[str, Any]] | None = None,
        audit_stats: dict[str, Any] | None = None,
        details: dict[str, dict[str, Any]] | None = None,
        audit_stats_exc: Exception | None = None,
    ) -> None:
        self._users = users or []
        self._roles = roles or []
        self._providers = providers or []
        self._reconcile = reconcile or []
        self._audit_entries = audit_entries or []
        self._details = details or {}
        self._audit = _StubAudit(audit_stats, audit_stats_exc)

    def list_users(self, include_deleted: bool = False) -> list[dict[str, Any]]:
        return list(self._users)

    def list_roles(self) -> list[dict[str, Any]]:
        return list(self._roles)

    def provider_health(self) -> list[dict[str, Any]]:
        return list(self._providers)

    def reconcile_report(self) -> list[dict[str, Any]]:
        return list(self._reconcile)

    def audit_recent(
        self, *, limit: int = 100, action_filter: str = "",
    ) -> list[dict[str, Any]]:
        rows = self._audit_entries
        if action_filter:
            rows = [r for r in rows if r.get("action") == action_filter]
        return list(rows[:limit])

    def user_detail(self, user_id: str) -> dict[str, Any] | None:
        return self._details.get(user_id)


class _StubAudit:
    def __init__(
        self,
        stats: dict[str, Any] | None,
        stats_exc: Exception | None,
    ) -> None:
        self._stats = stats or {}
        self._stats_exc = stats_exc

    def stats(self) -> dict[str, Any]:
        if self._stats_exc is not None:
            raise self._stats_exc
        return dict(self._stats)


class _StubInviteService:
    def __init__(self, invites: list[dict[str, Any]]) -> None:
        self._invites = invites

    def list_pending(self) -> list[dict[str, Any]]:
        return list(self._invites)


class _StubTokenStore:
    def __init__(self, tokens: list[dict[str, Any]]) -> None:
        self._tokens = [_StubToken(t) for t in tokens]

    def list_all(self) -> list[Any]:
        return list(self._tokens)


class _StubToken:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def to_dict(self) -> dict[str, Any]:
        return dict(self._payload)


def _patch_factories(
    *,
    service: _StubUserService | None = None,
    invites: _StubInviteService | None = None,
    tokens: _StubTokenStore | None = None,
) -> Any:
    """Patch the three factory entry points the routes use.

    Returns a context-manager-equivalent (an ``ExitStack``) so a
    test can ``with _patch_factories(...) as _: ...``.
    """
    from contextlib import ExitStack
    stack = ExitStack()
    # Patch at the module the route imports THROUGH: the
    # ``core.auth.users.user_service_factory`` shim copies the
    # symbols onto its own globals at import time, so a patch
    # against the ``application`` module wouldn't propagate. The
    # route does fresh attribute lookups on the ``core`` module
    # each call, so patching here wins.
    target = "media_stack.core.auth.users.user_service_factory"
    if service is not None:
        stack.enter_context(patch(
            f"{target}.build_default_service",
            return_value=service,
        ))
    if invites is not None:
        stack.enter_context(patch(
            f"{target}.build_default_invite_service",
            return_value=invites,
        ))
    if tokens is not None:
        stack.enter_context(patch(
            f"{target}.build_default_api_token_store",
            return_value=tokens,
        ))
    return stack


# --- /api/users -----------------------------------------------------


class TestListUsersRoute:
    def test_returns_users_envelope(self) -> None:
        users = [
            {"id": "u1", "username": "alice", "email": "a@x",
             "display_name": "Alice", "role_slug": "adult"},
            {"id": "u2", "username": "bob", "email": "b@x",
             "display_name": "Bob", "role_slug": "kid"},
        ]
        with _patch_factories(service=_StubUserService(users=users)):
            harness = RouteDispatchHarness.with_default_router()
            response = harness.dispatch("GET", "/api/users")

        assert response.status == 200
        body = json.loads(response.body)
        assert body == {"users": users}


# --- /api/roles -----------------------------------------------------


class TestListRolesRoute:
    def test_returns_roles_envelope(self) -> None:
        roles = [
            {"slug": "superadmin", "name": "Super Admin"},
            {"slug": "adult", "name": "Adult"},
        ]
        with _patch_factories(service=_StubUserService(roles=roles)):
            harness = RouteDispatchHarness.with_default_router()
            response = harness.dispatch("GET", "/api/roles")

        assert response.status == 200
        body = json.loads(response.body)
        assert body == {"roles": roles}


# --- /api/user-providers --------------------------------------------


class TestUserProvidersRoute:
    def test_returns_providers_envelope(self) -> None:
        providers = [
            {"name": "authelia", "ok": True, "detail": ""},
            {"name": "jellyfin", "ok": True, "detail": "status=200"},
        ]
        with _patch_factories(
            service=_StubUserService(providers=providers),
        ):
            harness = RouteDispatchHarness.with_default_router()
            response = harness.dispatch("GET", "/api/user-providers")

        assert response.status == 200
        body = json.loads(response.body)
        assert body == {"providers": providers}


# --- /api/users-reconcile -------------------------------------------


class TestUsersReconcileRoute:
    def test_returns_diffs_envelope(self) -> None:
        diffs = [
            {"provider": "authelia", "matched": 2,
             "orphans": [], "ghosts": []},
        ]
        with _patch_factories(service=_StubUserService(reconcile=diffs)):
            harness = RouteDispatchHarness.with_default_router()
            response = harness.dispatch("GET", "/api/users-reconcile")

        assert response.status == 200
        body = json.loads(response.body)
        assert body == {"diffs": diffs}


# --- /api/invites ---------------------------------------------------


class TestListInvitesRoute:
    def test_returns_invites_envelope(self) -> None:
        invites = [{"id": "inv-1", "email": "alice@x"}]
        with _patch_factories(
            service=_StubUserService(),
            invites=_StubInviteService(invites=invites),
        ):
            harness = RouteDispatchHarness.with_default_router()
            response = harness.dispatch("GET", "/api/invites")

        assert response.status == 200
        body = json.loads(response.body)
        assert body == {"invites": invites}


# --- /api/tokens ----------------------------------------------------


class TestListTokensRoute:
    def test_returns_tokens_envelope_with_to_dict_shape(self) -> None:
        """Pin that the response is built from ``token.to_dict()``
        — raw token values are scrubbed there and never appear
        on the wire. A future bug that bypassed ``to_dict`` would
        regress that contract."""
        token_dicts = [
            {"id": "t1", "name": "ci", "kind": "long_lived",
             "scope": "admin", "revoked": False},
        ]
        with _patch_factories(
            service=_StubUserService(),
            tokens=_StubTokenStore(tokens=token_dicts),
        ):
            harness = RouteDispatchHarness.with_default_router()
            response = harness.dispatch("GET", "/api/tokens")

        assert response.status == 200
        body = json.loads(response.body)
        assert body == {"tokens": token_dicts}

    def test_response_carries_no_raw_token_strings(self) -> None:
        """Defence-in-depth: ``to_dict`` is the canonical scrubber,
        but the route must not pass anything raw through. We feed
        it scrubbed shapes and pin no token-shaped string slips."""
        import re
        scrubbed = [{"id": "t1", "name": "ci", "kind": "long_lived",
                     "scope": "admin", "revoked": False}]
        with _patch_factories(
            service=_StubUserService(),
            tokens=_StubTokenStore(tokens=scrubbed),
        ):
            harness = RouteDispatchHarness.with_default_router()
            response = harness.dispatch("GET", "/api/tokens")

        assert response.status == 200
        body_text = response.body.decode("utf-8")
        # 32+ hex run is the typical raw-token shape.
        assert not re.search(r"[a-f0-9]{32,}", body_text)


# --- /api/me --------------------------------------------------------


class TestMeRoute:
    """Identity resolution + ``needs_rotation`` gating."""

    def test_anonymous_caller_returns_authenticated_false(self) -> None:
        with _patch_factories(service=_StubUserService(users=[])):
            harness = RouteDispatchHarness.with_default_router()
            response = harness.dispatch("GET", "/api/me")
        assert response.status == 200
        assert json.loads(response.body) == {"authenticated": False}

    def test_basic_auth_resolves_identity_and_envelope(self) -> None:
        """Basic auth is the third strategy; with no cookie / proxy
        in the headers it should still hydrate the record from the
        store."""
        users = [{
            "id": "u1", "username": "alice", "email": "alice@x",
            "display_name": "Alice", "role_slug": "adult",
            "source": "store", "last_login_at": "",
        }]
        creds = base64.b64encode(b"alice:not-checked-here").decode()
        with _patch_factories(service=_StubUserService(users=users)):
            harness = RouteDispatchHarness.with_default_router()
            response = harness.dispatch(
                "GET", "/api/me",
                headers={"Authorization": f"Basic {creds}"},
            )
        assert response.status == 200
        body = json.loads(response.body)
        assert body["authenticated"] is True
        assert body["username"] == "alice"
        assert body["id"] == "u1"
        assert body["needs_rotation"] is False

    def test_bootstrap_credential_triggers_needs_rotation(self) -> None:
        """``source=env-legacy`` sets ``needs_rotation=True`` unless
        the explicit skip env var is set — pin both halves."""
        users = [{
            "id": "u1", "username": "admin", "email": "admin@x",
            "display_name": "Administrator", "role_slug": "superadmin",
            "source": "env-legacy", "last_login_at": "",
        }]
        creds = base64.b64encode(b"admin:not-checked").decode()
        with _patch_factories(service=_StubUserService(users=users)):
            with patch.dict(
                "os.environ",
                {"STACK_ADMIN_SKIP_FORCED_ROTATION": ""},
                clear=False,
            ):
                harness = RouteDispatchHarness.with_default_router()
                response = harness.dispatch(
                    "GET", "/api/me",
                    headers={"Authorization": f"Basic {creds}"},
                )
        body = json.loads(response.body)
        assert body["needs_rotation"] is True

        with _patch_factories(service=_StubUserService(users=users)):
            with patch.dict(
                "os.environ",
                {"STACK_ADMIN_SKIP_FORCED_ROTATION": "1"},
                clear=False,
            ):
                harness = RouteDispatchHarness.with_default_router()
                response = harness.dispatch(
                    "GET", "/api/me",
                    headers={"Authorization": f"Basic {creds}"},
                )
        body = json.loads(response.body)
        assert body["needs_rotation"] is False


# --- /api/audit-log -------------------------------------------------


class TestAuditLogRoute:
    def test_returns_entries_envelope_with_default_limit(self) -> None:
        entries = [
            {"action": "login_success", "actor": "admin"},
            {"action": "brute_force_alert", "actor": "auth-watchdog"},
        ]
        with _patch_factories(
            service=_StubUserService(audit_entries=entries),
        ):
            harness = RouteDispatchHarness.with_default_router()
            # No query string -> default limit (100) applies.
            response = harness.dispatch("GET", "/api/audit-log")

        assert response.status == 200
        body = json.loads(response.body)
        assert body == {"entries": entries}

    def test_action_filter_is_applied(self) -> None:
        """Direct-instantiation test: route reads ``handler.path``
        for the query string. The harness uses the registered
        path verbatim, so we instantiate the route + pass a
        handler with the query suffix."""
        from media_stack.api.routes.users_get import (
            UserRepository,
            UsersGetRoutes,
        )

        entries = [
            {"action": "login_success", "actor": "admin"},
            {"action": "brute_force_alert", "actor": "auth-watchdog"},
        ]
        stub_svc = _StubUserService(audit_entries=entries)

        class _Repo(UserRepository):
            def _service(self_inner: Any) -> Any:
                return stub_svc

        routes = UsersGetRoutes(repository=_Repo())
        handler = MockControllerHandler(
            path="/api/audit-log?action=login_success",
        )
        routes.handle_audit_log(handler)

        body = json.loads(handler.captured.body)
        assert body == {"entries": [
            {"action": "login_success", "actor": "admin"},
        ]}

    def test_invalid_limit_falls_back_to_default(self) -> None:
        """Defensive: a malformed ``?limit=`` must not crash the
        route — the legacy chain's ``int(...)`` call would have
        raised a 500. Fix: log + fall back to default."""
        from media_stack.api.routes.users_get import (
            UserRepository,
            UsersGetRoutes,
        )

        entries = [{"action": "login_success", "actor": "admin"}]
        stub_svc = _StubUserService(audit_entries=entries)

        class _Repo(UserRepository):
            def _service(self_inner: Any) -> Any:
                return stub_svc

        routes = UsersGetRoutes(repository=_Repo())
        handler = MockControllerHandler(
            path="/api/audit-log?limit=not-a-number",
        )
        with patch(
            "media_stack.api.routes.users_get.log_swallowed",
        ) as mock_log:
            routes.handle_audit_log(handler)

        assert handler.captured.status == 200
        body = json.loads(handler.captured.body)
        assert body == {"entries": entries}
        mock_log.assert_called_once()


# --- /api/audit-log/stats -------------------------------------------


class TestAuditLogStatsRoute:
    def test_returns_stats_payload(self) -> None:
        stats = {
            "entry_count": 1234,
            "disk_bytes": 5_242_880,
            "oldest_at": "2026-03-01T00:00:00+00:00",
            "newest_at": "2026-04-25T08:59:41+00:00",
            "archive_count": 3,
            "rotation_policy": {"max_bytes": 10_485_760},
        }
        with _patch_factories(
            service=_StubUserService(audit_stats=stats),
        ):
            harness = RouteDispatchHarness.with_default_router()
            response = harness.dispatch("GET", "/api/audit-log/stats")

        assert response.status == 200
        body = json.loads(response.body)
        assert body == stats

    def test_oserror_yields_500_envelope_with_zero_counts(self) -> None:
        """Filesystem error in ``_audit.stats()`` is surfaced as a
        500 with a short error string + zeroed counts so the UI's
        defensive bind never sees ``undefined``."""
        with _patch_factories(
            service=_StubUserService(
                audit_stats_exc=OSError("audit-log unreadable"),
            ),
        ):
            with patch(
                "media_stack.api.routes.users_get.log_swallowed",
            ) as mock_log:
                harness = RouteDispatchHarness.with_default_router()
                response = harness.dispatch(
                    "GET", "/api/audit-log/stats",
                )

        assert response.status == 500
        body = json.loads(response.body)
        assert body["error"].startswith("audit-log unreadable")
        assert body["entry_count"] == 0
        assert body["disk_bytes"] == 0
        mock_log.assert_called_once()

    def test_unexpected_exception_propagates(self) -> None:
        """``RuntimeError`` is NOT caught — silent swallow on an
        unknown exception class would mask real bugs."""
        with _patch_factories(
            service=_StubUserService(
                audit_stats_exc=RuntimeError("unexpected"),
            ),
        ):
            harness = RouteDispatchHarness.with_default_router()
            try:
                harness.dispatch("GET", "/api/audit-log/stats")
            except RuntimeError as exc:
                assert "unexpected" in str(exc)
            else:
                raise AssertionError(
                    "RuntimeError must propagate, not be swallowed",
                )


# --- /api/users/{user_id} -------------------------------------------


class TestUserDetailRoute:
    def test_returns_user_record_when_present(self) -> None:
        record = {
            "id": "u1", "username": "alice", "email": "a@x",
            "display_name": "Alice", "role_slug": "adult",
            "state": "active",
        }
        with _patch_factories(
            service=_StubUserService(details={"u1": record}),
        ):
            harness = RouteDispatchHarness.with_default_router()
            response = harness.dispatch("GET", "/api/users/u1")

        assert response.status == 200
        body = json.loads(response.body)
        assert body == record

    def test_unknown_user_yields_404_envelope(self) -> None:
        with _patch_factories(
            service=_StubUserService(details={}),
        ):
            harness = RouteDispatchHarness.with_default_router()
            response = harness.dispatch("GET", "/api/users/nope")

        assert response.status == 404
        body = json.loads(response.body)
        assert body == {"error": "user nope not found"}

    def test_user_id_does_not_match_sub_path_segments(self) -> None:
        """Pin: the path-parameter regex matches ``[^/]+`` exactly,
        so ``/api/users/u1/sessions`` resolves to the dedicated
        ``/api/users/{user_id}/sessions`` route (wave-8) and NOT
        to ``/api/users/{user_id}`` with ``user_id='u1/sessions'``.

        Confirms the regex compilation in ``_RouteCompiler._compile_pattern``
        produces ``(?P<user_id>[^/]+)`` (single-segment), preventing
        greedy slash-spanning matches even when sibling routes exist.
        """
        from media_stack.api.routing import DispatchOutcome
        from media_stack.api.routes.post_user_sessions import (
            UserSessionsRoutes,
        )
        harness = RouteDispatchHarness.with_default_router()
        match = harness._dispatcher._router.match(
            "GET", "/api/users/u1/sessions",
        )
        assert match is not None, (
            "Expected /api/users/u1/sessions to match the "
            "/api/users/{user_id}/sessions route after wave-8."
        )
        # Confirm the matched route is the sessions route, not a
        # greedy `/api/users/{user_id}` match with slash in the param.
        assert match.params == {"user_id": "u1"}, (
            f"Expected single-segment user_id capture; "
            f"got params={match.params}"
        )
        assert isinstance(match.route.handler.__self__, UserSessionsRoutes)


# --- /api/users/{user_id}/login-history -----------------------------


class _AlwaysAllowLimiter:
    def allow(self, handler: Any) -> bool:
        return True


class _AlwaysDenyLimiter:
    def allow(self, handler: Any) -> bool:
        return False


class _StubLoginHistoryHelper:
    """Captures the path the route delegates with."""

    def __init__(self, *, payload: dict[str, Any]) -> None:
        self._payload = payload
        self.dispatch_calls: list[tuple[Any, str]] = []

    def dispatch(self, handler: Any, path: str) -> None:
        self.dispatch_calls.append((handler, path))
        handler._json_response(200, self._payload)


class TestUserLoginHistoryRoute:
    def test_rate_limit_exceeded_emits_429_without_dispatch(self) -> None:
        """Security-relevant: when the security-read bucket is
        exhausted, the route must NOT fall through to the legacy
        helper — that would let an attacker enumerate per-user
        history at full speed."""
        from media_stack.api.routes.users_get import UsersGetRoutes

        helper = _StubLoginHistoryHelper(payload={"entries": []})
        routes = UsersGetRoutes(
            login_history_limiter=_AlwaysDenyLimiter(),
            login_history_helper=helper,
        )
        handler = MockControllerHandler(
            path="/api/users/u1/login-history",
        )
        routes.handle_user_login_history(handler, user_id="u1")

        assert handler.captured.status == 429
        body = json.loads(handler.captured.body)
        assert body["error"] == "rate_limit_exceeded"
        assert "security-read" in body["detail"]
        assert helper.dispatch_calls == [], (
            "delegate must not be called when the rate-limit gate "
            "denies — otherwise the bucket is bypassed"
        )

    def test_allowed_request_delegates_to_session_visibility(
        self,
    ) -> None:
        """Happy path: gate permits, helper takes over. The route
        passes the canonical ``/api/users/{user_id}/login-history``
        path to the helper — NOT ``handler.path`` — so the helper's
        suffix-match in ``dispatch`` doesn't get confused by a
        ``?limit=`` query string."""
        from media_stack.api.routes.users_get import UsersGetRoutes

        payload = {"entries": [
            {"timestamp": "2026-04-25T08:42:11Z", "ip": "192.168.1.42",
             "succeeded": True},
        ]}
        helper = _StubLoginHistoryHelper(payload=payload)
        routes = UsersGetRoutes(
            login_history_limiter=_AlwaysAllowLimiter(),
            login_history_helper=helper,
        )
        handler = MockControllerHandler(
            path="/api/users/u1/login-history?limit=50",
        )
        routes.handle_user_login_history(handler, user_id="u1")

        assert handler.captured.status == 200
        body = json.loads(handler.captured.body)
        assert body == payload
        # Pin the path the route delegates with — the legacy
        # helper's ``dispatch`` keys its suffix-match off the bare
        # path; query strings would break the match.
        assert len(helper.dispatch_calls) == 1
        _, dispatched_path = helper.dispatch_calls[0]
        assert dispatched_path == "/api/users/u1/login-history"


# --- LoginHistoryRateLimitAdapter ----------------------------------


class TestLoginHistoryRateLimitAdapter:
    """Direct-tests for the adapter so the rate-limit gate behaves
    deterministically. The adapter holds ONE limiter for the
    lifetime of the route module — the bucket has to persist for
    the cap to be meaningful."""

    def test_uses_constructor_injected_limiter(self) -> None:
        from media_stack.api.routes.users_get import (
            LoginHistoryRateLimitAdapter,
        )

        calls: list[tuple[str, str]] = []

        class _Limiter:
            def allow(self, *, client_id: str, bucket: str) -> bool:
                calls.append((client_id, bucket))
                return True

        adapter = LoginHistoryRateLimitAdapter(
            limiter=_Limiter(),
            client_id_resolver=lambda h: "1.2.3.4",
        )
        handler = MockControllerHandler(path="/api/users/u/login-history")
        assert adapter.allow(handler) is True
        assert calls == [("1.2.3.4", "security-read")]

    def test_default_client_id_falls_back_when_resolver_blank(
        self,
    ) -> None:
        """Per-IP keying: when the trusted-proxy reader returns
        empty, the adapter substitutes ``-`` so the limiter still
        records the request and one anonymous burst can't slip
        past via a "no client id" path."""
        from media_stack.api.routes.users_get import (
            LoginHistoryRateLimitAdapter,
        )

        seen: list[str] = []

        class _Limiter:
            def allow(self, *, client_id: str, bucket: str) -> bool:
                seen.append(client_id)
                return True

        adapter = LoginHistoryRateLimitAdapter(
            limiter=_Limiter(),
            client_id_resolver=lambda h: "",
        )
        handler = MockControllerHandler(path="/x")
        adapter.allow(handler)
        assert seen == ["-"]


# --- Routing-integration --------------------------------------------


class TestRoutingIntegration:
    """Auto-discovery + spec-parity sanity checks."""

    def test_all_users_routes_registered(self) -> None:
        harness = RouteDispatchHarness.with_default_router()
        expected = {
            "/api/users",
            "/api/users/{user_id}",
            "/api/me",
            "/api/users-reconcile",
            "/api/invites",
            "/api/tokens",
            "/api/roles",
            "/api/user-providers",
            "/api/audit-log",
            "/api/audit-log/stats",
            "/api/users/{user_id}/login-history",
        }
        registered = {
            r.path
            for r in harness._dispatcher._router.registered_routes()
            if r.path in expected
        }
        assert registered == expected, (
            f"Missing users-domain routes: {expected - registered}"
        )

    def test_post_to_users_routed_to_post_module(
        self,
    ) -> None:
        """POST /api/users (createUser) was migrated in wave-8
        (``post_users.py``). The GET-side users module here MUST
        NOT claim the POST verb. Confirm dispatch matches a route
        owned by a different module than this file's GET routes."""
        from media_stack.api.routing import DispatchOutcome
        from media_stack.api.routes.post_users import UsersPostRoutes
        harness = RouteDispatchHarness.with_default_router()
        match = harness._dispatcher._router.match("POST", "/api/users")
        assert match is not None, (
            "Expected POST /api/users to be registered after wave-8."
        )
        assert isinstance(match.route.handler.__self__, UsersPostRoutes), (
            "Expected POST /api/users to route to UsersPostRoutes; "
            f"got {type(match.route.handler.__self__).__name__}"
        )
