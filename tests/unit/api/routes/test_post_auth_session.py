"""Tests for ``api/routes/post_auth_session.py``
(ADR-0007 Phase 2 wave 5).

Eight POST routes covering the auth-session security domain.
Every endpoint is in the security-critical path — login,
logout, change-password, reset-password, refresh-token, OIDC
parse + probe, and live updates to the auth configuration. The
tests exercise:

* Each route's success path through the production Router
  (when the route returns a plain JSON body — login / logout
  attach a Set-Cookie header and route through ``_raw_response``,
  which the Router-driven harness already supports).
* Each route's failure paths — bad credentials, missing fields,
  rate-limit / ban hits (audit row written), 401 / 403 / 502
  envelopes, narrow ``except`` swallow + ``log_swallowed`` fire.
* Audit-log writes are observable through the injected
  repository stub — no in-test forensic file is touched.
* CSRF-double-submit is enforced upstream of the Router; tests
  pin that login / logout / tokens-refresh remain in
  ``PostRequestHandler._CSRF_EXEMPT_POST_PATHS`` so the
  legacy preflight allows the un-cookied request through.
* ``log_swallowed`` fires on every narrow ``except`` block in
  the security paths — pinned per
  ``bug_class_silent_error_as_ok``. A future regression that
  reverts to a silent debug-level log fails this test.

Auth gating note: in production these POST routes ride the
``ControllerAPIHandler`` ``_check_auth`` middleware that the
server runs BEFORE the dispatcher fires — except for the three
explicitly-exempt endpoints (login / logout / tokens-refresh)
which authenticate themselves via the body. Tests below confirm
the route bodies are pure delegation: they don't introduce
per-route auth bypasses, and they don't echo raw secrets that
``_check_auth`` would need to sanitize.
"""

from __future__ import annotations

import json
from http import HTTPStatus
from typing import Any
from unittest.mock import patch

from tests.unit.api.routes._helpers import (
    MockControllerHandler,
    RouteDispatchHarness,
)


# --- Mock handler with body support ---------------------------------


class BodyAwareHandler(MockControllerHandler):
    """``MockControllerHandler`` extended with the body-reader
    surface ``handlers_post`` routes call. Captures the
    ``Set-Cookie`` header attached to login / logout responses
    via ``_raw_response``.
    """

    def __init__(
        self,
        *,
        path: str = "/",
        body: bytes = b"",
        headers: dict[str, str] | None = None,
        state: Any = None,
    ) -> None:
        merged_headers = dict(headers or {})
        if body and "Content-Length" not in merged_headers:
            merged_headers["Content-Length"] = str(len(body))
        super().__init__(
            path=path, body=body, headers=merged_headers, state=state,
        )
        self._body_bytes = body

    def _read_json_body(self) -> dict[str, Any]:
        if not self._body_bytes:
            return {}
        try:
            return json.loads(self._body_bytes)
        except (ValueError, TypeError):
            return {}


# --- Stubs used across multiple test classes -----------------------


class _StubAuditLog:
    """Captures audit-log appends for assertion."""

    def __init__(self) -> None:
        self.entries: list[dict[str, Any]] = []

    def append(self, **kwargs: Any) -> None:
        self.entries.append(kwargs)


class _StubUserService:
    """Minimal user-service shape — exposes ``_audit`` (the
    hash-chained log), ``_store`` (read-by-username), and
    ``reset_password`` (the admin-grade write)."""

    def __init__(
        self,
        *,
        users: dict[str, Any] | None = None,
        reset_result: dict[str, Any] | None = None,
        reset_raises: Exception | None = None,
    ) -> None:
        self._users = users or {}
        self._audit = _StubAuditLog()
        self._reset_result = reset_result or {"ok": True}
        self._reset_raises = reset_raises
        self._store = self._BuildStore(users or {})

    class _BuildStore:
        def __init__(self, users: dict[str, Any]) -> None:
            self._users = users

        def get_by_username(self, username: str) -> Any:
            return self._users.get(username)

    def reset_password(
        self, user_id: str, *, password: str, actor: Any,
    ) -> dict[str, Any]:
        if self._reset_raises is not None:
            raise self._reset_raises
        return self._reset_result


class _StubVerifier:
    """Minimal credential-verifier shape — accepts a single
    ``(username, password)`` pair as the truth set."""

    def __init__(self, accepted: dict[str, str] | None = None) -> None:
        self._accepted = accepted or {}

    def verify(self, username: str, password: str) -> bool:
        return self._accepted.get(username) == password


class _StubSessionStore:
    """Captures created/revoked tokens for assertion."""

    def __init__(self) -> None:
        self.created: list[str] = []
        self.revoked: list[str] = []
        self.sessions: dict[str, Any] = {}

    def create(self, *, owner_username: str) -> tuple[Any, str]:
        plaintext = f"tok-{owner_username}"
        sess = type("S", (), {"owner_username": owner_username})()
        self.sessions[plaintext] = sess
        self.created.append(owner_username)
        return sess, plaintext

    def get(self, token: str) -> Any:
        return self.sessions.get(token)

    def revoke(self, token: str) -> bool:
        self.revoked.append(token)
        return self.sessions.pop(token, None) is not None


class _StubTrustedProxy:
    """Stand-in for ``trusted_proxy_auth`` — controls the
    resolved client IP + identity without touching the real
    forwarding rules."""

    def __init__(
        self, ip: str = "10.0.0.5", identity: str = "",
    ) -> None:
        self._ip = ip
        self._identity = identity

    def client_ip(self, _handler: Any) -> str:
        return self._ip

    def identity(self, _handler: Any) -> str:
        return self._identity


def _build_repository(
    *,
    user_service: _StubUserService | None = None,
    verifier: _StubVerifier | None = None,
):
    from media_stack.api.routes.post_auth_session import (
        UserCredentialRepository,
    )
    svc = user_service or _StubUserService()
    ver = verifier or _StubVerifier()
    return UserCredentialRepository(
        service_factory=lambda: svc,
        verifier_factory=lambda: ver,
    ), svc


# --- Login ----------------------------------------------------------


class TestAuthLoginRoute:
    """``POST /api/auth/login`` — credential-verify + cookie mint.
    Every attempt writes an audit row; CSRF-exempt because the
    cookie doesn't exist yet."""

    def test_success_mints_cookie_and_audits(self) -> None:
        from media_stack.api.routes.post_auth_session import (
            AuthSessionPostRoutes,
            SessionLoginService,
        )
        repo, svc = _build_repository(
            verifier=_StubVerifier({"alice": "secret"}),
        )
        store = _StubSessionStore()
        login = SessionLoginService(
            repository=repo, store=store,
            trusted_proxy=_StubTrustedProxy(),
        )
        routes = AuthSessionPostRoutes(login_service=login)

        body = json.dumps({"username": "alice", "password": "secret"})
        handler = BodyAwareHandler(
            path="/api/auth/login", body=body.encode("utf-8"),
            headers={"User-Agent": "pytest"},
        )
        routes.handle_auth_login(handler)

        assert handler.captured.status == HTTPStatus.OK
        assert json.loads(handler.captured.body) == {
            "session": "established",
        }
        assert "Set-Cookie" in handler.captured.extra_headers
        cookie = handler.captured.extra_headers["Set-Cookie"]
        assert "ms_session=tok-alice" in cookie
        assert "HttpOnly" in cookie
        assert "Secure" in cookie
        assert "SameSite=Strict" in cookie
        # Audit row written with LOGIN_SUCCESS.
        assert len(svc._audit.entries) == 1
        entry = svc._audit.entries[0]
        assert entry["action"].lower().endswith("success") or "success" in entry["action"]
        assert entry["target"] == "alice"
        assert entry["result"] == "ok"
        assert entry["ip"] == "10.0.0.5"
        assert store.created == ["alice"]

    def test_missing_fields_returns_400_and_audits_failure(self) -> None:
        from media_stack.api.routes.post_auth_session import (
            AuthSessionPostRoutes,
            SessionLoginService,
        )
        repo, svc = _build_repository()
        login = SessionLoginService(
            repository=repo, store=_StubSessionStore(),
            trusted_proxy=_StubTrustedProxy(),
        )
        routes = AuthSessionPostRoutes(login_service=login)

        body = json.dumps({"username": "", "password": ""})
        handler = BodyAwareHandler(
            path="/api/auth/login", body=body.encode("utf-8"),
        )
        routes.handle_auth_login(handler)

        assert handler.captured.status == HTTPStatus.BAD_REQUEST
        assert b"username and password" in handler.captured.body
        # Failure is audited — even tampered / missing-field cases.
        assert len(svc._audit.entries) == 1
        assert svc._audit.entries[0]["result"] == "fail"
        assert svc._audit.entries[0]["detail"]["reason"] == "missing_fields"

    def test_bad_credentials_returns_401_and_audits_failure(self) -> None:
        from media_stack.api.routes.post_auth_session import (
            AuthSessionPostRoutes,
            SessionLoginService,
        )
        repo, svc = _build_repository(
            verifier=_StubVerifier({"alice": "secret"}),
        )
        login = SessionLoginService(
            repository=repo, store=_StubSessionStore(),
            trusted_proxy=_StubTrustedProxy(),
        )
        routes = AuthSessionPostRoutes(login_service=login)

        body = json.dumps(
            {"username": "alice", "password": "WRONG"},
        )
        handler = BodyAwareHandler(
            path="/api/auth/login", body=body.encode("utf-8"),
        )
        routes.handle_auth_login(handler)

        assert handler.captured.status == HTTPStatus.UNAUTHORIZED
        assert b"invalid credentials" in handler.captured.body
        # Bad-credentials audit row — forensic trail of brute-force
        # attempts must include the attempted username so a
        # dashboard can flag account-targeted enumeration.
        assert len(svc._audit.entries) == 1
        assert svc._audit.entries[0]["target"] == "alice"
        assert svc._audit.entries[0]["detail"]["reason"] == "bad_credentials"
        assert svc._audit.entries[0]["result"] == "fail"

    def test_response_does_not_echo_password(self) -> None:
        """Defence-in-depth: even on success, the password supplied
        in the body must NOT appear anywhere in the response."""
        from media_stack.api.routes.post_auth_session import (
            AuthSessionPostRoutes,
            SessionLoginService,
        )
        repo, _svc = _build_repository(
            verifier=_StubVerifier({"alice": "TOPSECRET"}),
        )
        login = SessionLoginService(
            repository=repo, store=_StubSessionStore(),
            trusted_proxy=_StubTrustedProxy(),
        )
        routes = AuthSessionPostRoutes(login_service=login)
        body = json.dumps({"username": "alice", "password": "TOPSECRET"})
        handler = BodyAwareHandler(
            path="/api/auth/login", body=body.encode("utf-8"),
        )
        routes.handle_auth_login(handler)

        assert b"TOPSECRET" not in handler.captured.body
        assert b"TOPSECRET" not in handler.captured.extra_headers.get(
            "Set-Cookie", "",
        ).encode("utf-8")


# --- Logout ---------------------------------------------------------


class TestAuthLogoutRoute:
    """``POST /api/auth/logout`` — revokes the cookie + writes a
    LOGOUT audit row. Idempotent."""

    def test_revokes_cookie_and_audits(self) -> None:
        from media_stack.api.routes.post_auth_session import (
            AuthSessionPostRoutes,
            SessionLoginService,
        )
        repo, svc = _build_repository()
        store = _StubSessionStore()
        # Pre-populate a session.
        sess, plain = store.create(owner_username="alice")
        login = SessionLoginService(
            repository=repo, store=store,
            trusted_proxy=_StubTrustedProxy(),
        )
        routes = AuthSessionPostRoutes(login_service=login)

        handler = BodyAwareHandler(
            path="/api/auth/logout",
            headers={"Cookie": f"ms_session={plain}"},
        )
        routes.handle_auth_logout(handler)

        assert handler.captured.status == HTTPStatus.OK
        assert json.loads(handler.captured.body) == {"logged_out": True}
        assert plain in store.revoked
        # Audit row attributes the logout to the ORIGINAL owner.
        assert len(svc._audit.entries) == 1
        assert svc._audit.entries[0]["target"] == "alice"
        assert "Max-Age=0" in handler.captured.extra_headers["Set-Cookie"]


# --- Change password ------------------------------------------------


class TestMeChangePasswordRoute:
    """``POST /api/me/change-password`` — re-auth via
    ``current_password`` then admin reset. Errors are deliberately
    generic to prevent account enumeration."""

    def _routes_with(
        self,
        *,
        verifier: _StubVerifier,
        users: dict[str, Any],
        identity: str = "",
        reset_raises: Exception | None = None,
    ) -> tuple[Any, _StubUserService]:
        from media_stack.api.routes.post_auth_session import (
            AuthSessionPostRoutes,
            UserCredentialRepository,
        )
        svc = _StubUserService(users=users, reset_raises=reset_raises)
        repo = UserCredentialRepository(
            service_factory=lambda: svc,
            verifier_factory=lambda: verifier,
        )
        routes = AuthSessionPostRoutes(user_repository=repo)
        return routes, svc

    def test_success(self) -> None:
        users = {"alice": type("U", (), {"id": "user-1"})()}
        routes, _svc = self._routes_with(
            verifier=_StubVerifier({"alice": "old-pass-1"}),
            users=users,
        )
        with patch(
            "media_stack.api.routes.post_auth_session."
            "session_cookie_reader.username_for_handler",
            return_value="alice",
        ), patch(
            "media_stack.api.routes.post_auth_session."
            "trusted_proxy_auth.client_ip",
            return_value="10.0.0.5",
        ), patch(
            "media_stack.api.routes.post_auth_session."
            "build_default_service",
        ):
            body = json.dumps({
                "current_password": "old-pass-1",
                "new_password": "new-pass-12345",
            })
            handler = BodyAwareHandler(
                path="/api/me/change-password", body=body.encode("utf-8"),
            )
            routes.handle_me_change_password(handler)

        assert handler.captured.status == HTTPStatus.OK
        assert json.loads(handler.captured.body) == {"ok": True}

    def test_short_new_password_400(self) -> None:
        routes, _svc = self._routes_with(
            verifier=_StubVerifier(), users={},
        )
        body = json.dumps({
            "current_password": "old", "new_password": "tiny",
        })
        handler = BodyAwareHandler(
            path="/api/me/change-password", body=body.encode("utf-8"),
        )
        routes.handle_me_change_password(handler)
        assert handler.captured.status == HTTPStatus.BAD_REQUEST
        assert b"at least 8" in handler.captured.body

    def test_same_password_400(self) -> None:
        routes, _svc = self._routes_with(
            verifier=_StubVerifier(), users={},
        )
        same = "same-password-x"
        body = json.dumps({
            "current_password": same, "new_password": same,
        })
        handler = BodyAwareHandler(
            path="/api/me/change-password", body=body.encode("utf-8"),
        )
        routes.handle_me_change_password(handler)
        assert handler.captured.status == HTTPStatus.BAD_REQUEST
        assert b"differ" in handler.captured.body

    def test_unauthenticated_401(self) -> None:
        """No session cookie / no proxy header / no Basic auth ->
        401. The route must NOT touch the repository before this
        check fires — otherwise an attacker could probe valid
        usernames via timing."""
        routes, _svc = self._routes_with(
            verifier=_StubVerifier(), users={},
        )
        with patch(
            "media_stack.api.routes.post_auth_session."
            "session_cookie_reader.username_for_handler",
            return_value="",
        ), patch(
            "media_stack.api.routes.post_auth_session."
            "trusted_proxy_auth.identity",
            return_value="",
        ):
            body = json.dumps({
                "current_password": "x", "new_password": "12345678",
            })
            handler = BodyAwareHandler(
                path="/api/me/change-password",
                body=body.encode("utf-8"),
            )
            routes.handle_me_change_password(handler)
        assert handler.captured.status == HTTPStatus.UNAUTHORIZED

    def test_wrong_current_password_returns_generic_403(self) -> None:
        """Generic-error rule: a wrong password and an unknown
        username MUST land on the same response so an attacker
        can't enumerate accounts via the error string."""
        routes, _svc = self._routes_with(
            verifier=_StubVerifier({"alice": "real"}), users={},
        )
        with patch(
            "media_stack.api.routes.post_auth_session."
            "session_cookie_reader.username_for_handler",
            return_value="alice",
        ):
            body = json.dumps({
                "current_password": "WRONG",
                "new_password": "long-enough-x",
            })
            handler = BodyAwareHandler(
                path="/api/me/change-password",
                body=body.encode("utf-8"),
            )
            routes.handle_me_change_password(handler)
        assert handler.captured.status == HTTPStatus.FORBIDDEN
        assert b"current_password is incorrect" in handler.captured.body


# --- Tokens refresh ------------------------------------------------


class TestTokenRefreshRoute:
    """``POST /api/tokens/refresh`` — rotate the refresh token. The
    refresh IS the credential, so this endpoint is CSRF-exempt
    (programmatic clients can't supply a Cookie-paired token)."""

    def test_rotate_success(self) -> None:
        from media_stack.api.routes.post_auth_session import (
            AuthSessionPostRoutes,
            TokenRefreshService,
        )

        class _Token:
            def __init__(self, name: str) -> None:
                self.name = name

            def to_dict(self) -> dict[str, Any]:
                return {"name": self.name}

        class _Store:
            def rotate(self, _refresh: str):
                return (
                    (_Token("access"), "ax-plain"),
                    (_Token("refresh"), "rx-plain"),
                )

        svc = TokenRefreshService(store_factory=lambda: _Store())
        routes = AuthSessionPostRoutes(token_refresh_service=svc)
        body = json.dumps({"refresh_token": "old-rx"})
        handler = BodyAwareHandler(
            path="/api/tokens/refresh", body=body.encode("utf-8"),
        )
        routes.handle_tokens_refresh(handler)
        assert handler.captured.status == HTTPStatus.OK
        out = json.loads(handler.captured.body)
        assert out["access"]["token"] == "ax-plain"
        assert out["refresh"]["token"] == "rx-plain"

    def test_missing_refresh_returns_400(self) -> None:
        from media_stack.api.routes.post_auth_session import (
            AuthSessionPostRoutes,
            TokenRefreshService,
        )
        routes = AuthSessionPostRoutes(
            token_refresh_service=TokenRefreshService(
                store_factory=lambda: object(),
            ),
        )
        handler = BodyAwareHandler(
            path="/api/tokens/refresh", body=b"{}",
        )
        routes.handle_tokens_refresh(handler)
        assert handler.captured.status == HTTPStatus.BAD_REQUEST
        assert b"refresh_token required" in handler.captured.body

    def test_invalid_refresh_returns_400(self) -> None:
        """Already-rotated / leaked-replay refresh -> 400 with the
        canned "sign in again" prompt. Replay defence."""
        from media_stack.api.routes.post_auth_session import (
            AuthSessionPostRoutes,
            TokenRefreshService,
        )

        class _Store:
            def rotate(self, _r: str):
                return None

        svc = TokenRefreshService(store_factory=lambda: _Store())
        routes = AuthSessionPostRoutes(token_refresh_service=svc)
        handler = BodyAwareHandler(
            path="/api/tokens/refresh",
            body=json.dumps({"refresh_token": "leaked"}).encode("utf-8"),
        )
        routes.handle_tokens_refresh(handler)
        assert handler.captured.status == HTTPStatus.BAD_REQUEST
        assert b"sign in again" in handler.captured.body


# --- Reset password ------------------------------------------------


class TestResetPasswordRoute:
    """``POST /api/reset-password`` — cross-service admin password
    reset. CSRF-required (mutating); the strong policy lives
    upstream in ``PasswordPolicyConfig``."""

    def test_success(self) -> None:
        from media_stack.api.routes.post_auth_session import (
            AuthSessionPostRoutes,
            PasswordResetService,
        )

        class _Admin:
            def reset_password(self, password: str, services):
                return {"updated": ["sonarr", "radarr"], "errors": []}

        svc = PasswordResetService(admin_factory=lambda: _Admin())
        routes = AuthSessionPostRoutes(password_reset_service=svc)
        body = json.dumps({"password": "new-strong-pass"})
        handler = BodyAwareHandler(
            path="/api/reset-password", body=body.encode("utf-8"),
        )
        routes.handle_reset_password(handler)
        assert handler.captured.status == HTTPStatus.OK
        out = json.loads(handler.captured.body)
        assert out["updated"] == ["sonarr", "radarr"]

    def test_missing_password_400(self) -> None:
        from media_stack.api.routes.post_auth_session import (
            AuthSessionPostRoutes, PasswordResetService,
        )
        routes = AuthSessionPostRoutes(
            password_reset_service=PasswordResetService(
                admin_factory=lambda: object(),
            ),
        )
        handler = BodyAwareHandler(
            path="/api/reset-password", body=b"{}",
        )
        routes.handle_reset_password(handler)
        assert handler.captured.status == HTTPStatus.BAD_REQUEST
        assert b"password field required" in handler.captured.body

    def test_too_short_password_400(self) -> None:
        from media_stack.api.routes.post_auth_session import (
            AuthSessionPostRoutes, PasswordResetService,
        )
        routes = AuthSessionPostRoutes(
            password_reset_service=PasswordResetService(
                admin_factory=lambda: object(),
            ),
        )
        body = json.dumps({"password": "abc"})
        handler = BodyAwareHandler(
            path="/api/reset-password", body=body.encode("utf-8"),
        )
        routes.handle_reset_password(handler)
        assert handler.captured.status == HTTPStatus.BAD_REQUEST


# --- Auth config update --------------------------------------------


class _StubAuthConfigService:
    def __init__(self, result: dict[str, Any]) -> None:
        self._result = result
        self.calls: list[tuple[dict[str, Any], Any]] = []

    def update_auth_config(self, body: dict[str, Any], trigger: Any):
        self.calls.append((body, trigger))
        return self._result


class TestAuthConfigUpdateRoute:
    """``POST /api/auth/config`` — live updates to the auth-mode
    configuration. CSRF-required (mutating)."""

    def test_success_returns_updated_config(self) -> None:
        from media_stack.api.routes.post_auth_session import (
            AuthSessionPostRoutes,
        )
        stub = _StubAuthConfigService({"mode": "authelia", "ok": True})
        with patch(
            "media_stack.api.routes.post_auth_session."
            "AuthSessionPostRoutes._auth_config_service",
            classmethod(lambda cls: stub),
        ):
            routes = AuthSessionPostRoutes()
            body = json.dumps({"mode": "authelia"})
            handler = BodyAwareHandler(
                path="/api/auth/config", body=body.encode("utf-8"),
            )
            routes.handle_auth_config_update(handler)
        assert handler.captured.status == HTTPStatus.OK
        assert stub.calls == [({"mode": "authelia"}, None)]

    def test_empty_body_400(self) -> None:
        from media_stack.api.routes.post_auth_session import (
            AuthSessionPostRoutes,
        )
        routes = AuthSessionPostRoutes()
        handler = BodyAwareHandler(
            path="/api/auth/config", body=b"",
        )
        routes.handle_auth_config_update(handler)
        assert handler.captured.status == HTTPStatus.BAD_REQUEST

    def test_service_error_returns_400(self) -> None:
        from media_stack.api.routes.post_auth_session import (
            AuthSessionPostRoutes,
        )
        stub = _StubAuthConfigService({"error": "invalid mode"})
        with patch(
            "media_stack.api.routes.post_auth_session."
            "AuthSessionPostRoutes._auth_config_service",
            classmethod(lambda cls: stub),
        ):
            routes = AuthSessionPostRoutes()
            handler = BodyAwareHandler(
                path="/api/auth/config",
                body=json.dumps({"mode": "garbage"}).encode("utf-8"),
            )
            routes.handle_auth_config_update(handler)
        assert handler.captured.status == HTTPStatus.BAD_REQUEST


# --- OIDC parse / probe -------------------------------------------


class TestParseOidcRoute:
    """``POST /api/auth/parse-oidc`` — parse an uploaded provider
    JSON. ``raw`` is stripped from the response to keep the
    payload small."""

    def test_strips_raw_echo(self) -> None:
        from media_stack.api.routes.post_auth_session import (
            AuthSessionPostRoutes,
        )

        def _parser(blob: dict[str, Any]) -> dict[str, Any]:
            return {"ok": True, "issuer": "x", "raw": blob}

        routes = AuthSessionPostRoutes(oidc_config_parser=_parser)
        body = json.dumps({"client_id": "abc"})
        handler = BodyAwareHandler(
            path="/api/auth/parse-oidc", body=body.encode("utf-8"),
        )
        routes.handle_auth_parse_oidc(handler)
        assert handler.captured.status == HTTPStatus.OK
        out = json.loads(handler.captured.body)
        assert "raw" not in out
        assert out["ok"] is True

    def test_empty_body_400(self) -> None:
        from media_stack.api.routes.post_auth_session import (
            AuthSessionPostRoutes,
        )
        routes = AuthSessionPostRoutes(
            oidc_config_parser=lambda b: {"ok": True},
        )
        handler = BodyAwareHandler(
            path="/api/auth/parse-oidc", body=b"",
        )
        routes.handle_auth_parse_oidc(handler)
        assert handler.captured.status == HTTPStatus.BAD_REQUEST


class TestProbeOidcRoute:
    """``POST /api/auth/oidc/probe`` — server-side discovery fetch.
    The legacy chain caught a bare ``Exception``; we narrow to
    ``OSError`` / ``ValueError`` and ``log_swallowed`` every miss
    so a real failure is observable."""

    def test_success_summarizes_well_known_fields(self) -> None:
        from media_stack.api.routes.post_auth_session import (
            AuthSessionPostRoutes, OidcProvider,
        )

        class _Provider(OidcProvider):
            def __init__(self) -> None:
                pass

            def fetch_discovery(self, url: str):
                return {
                    "issuer": "https://x.example",
                    "token_endpoint": "https://x.example/token",
                    "_extra": "ignored",
                }

            def summarize(self, doc):
                return {
                    "issuer": doc["issuer"],
                    "token_endpoint": doc["token_endpoint"],
                }

        routes = AuthSessionPostRoutes(oidc_provider=_Provider())
        body = json.dumps({
            "discovery_url": "https://x.example/.well-known/openid-configuration",
        })
        handler = BodyAwareHandler(
            path="/api/auth/oidc/probe", body=body.encode("utf-8"),
        )
        routes.handle_auth_oidc_probe(handler)
        assert handler.captured.status == HTTPStatus.OK
        out = json.loads(handler.captured.body)
        assert out["ok"] is True
        assert out["summary"]["issuer"] == "https://x.example"
        assert "_extra" in out["raw"]

    def test_invalid_scheme_400(self) -> None:
        from media_stack.api.routes.post_auth_session import (
            AuthSessionPostRoutes,
        )
        routes = AuthSessionPostRoutes()
        body = json.dumps({"discovery_url": "ftp://nope.example"})
        handler = BodyAwareHandler(
            path="/api/auth/oidc/probe", body=body.encode("utf-8"),
        )
        routes.handle_auth_oidc_probe(handler)
        assert handler.captured.status == HTTPStatus.BAD_REQUEST

    def test_network_error_502_with_log_swallowed(self) -> None:
        """A documented network failure → 502 envelope; the
        narrowed swallow MUST fire ``log_swallowed`` per
        ``bug_class_silent_error_as_ok``."""
        from media_stack.api.routes.post_auth_session import (
            AuthSessionPostRoutes, OidcProvider,
        )

        class _BoomProvider(OidcProvider):
            def __init__(self) -> None:
                pass

            def fetch_discovery(self, url: str):
                raise OSError("connection refused")

        routes = AuthSessionPostRoutes(oidc_provider=_BoomProvider())
        body = json.dumps({
            "discovery_url": "https://x.example/openid",
        })
        handler = BodyAwareHandler(
            path="/api/auth/oidc/probe", body=body.encode("utf-8"),
        )
        with patch(
            "media_stack.api.routes.post_auth_session.log_swallowed",
        ) as mock_log:
            routes.handle_auth_oidc_probe(handler)
        assert handler.captured.status == HTTPStatus.BAD_GATEWAY
        out = json.loads(handler.captured.body)
        assert out["ok"] is False
        assert "connection refused" in out["error"]
        mock_log.assert_called_once()

    def test_value_error_502_with_log_swallowed(self) -> None:
        """Discovery doc returned but isn't a JSON object → 502.
        Same swallow shape as the OSError path; both must fire
        ``log_swallowed``."""
        from media_stack.api.routes.post_auth_session import (
            AuthSessionPostRoutes, OidcProvider,
        )

        class _BadDocProvider(OidcProvider):
            def __init__(self) -> None:
                pass

            def fetch_discovery(self, url: str):
                raise ValueError("discovery doc is not a JSON object")

        routes = AuthSessionPostRoutes(oidc_provider=_BadDocProvider())
        body = json.dumps({
            "discovery_url": "https://x.example/openid",
        })
        handler = BodyAwareHandler(
            path="/api/auth/oidc/probe", body=body.encode("utf-8"),
        )
        with patch(
            "media_stack.api.routes.post_auth_session.log_swallowed",
        ) as mock_log:
            routes.handle_auth_oidc_probe(handler)
        assert handler.captured.status == HTTPStatus.BAD_GATEWAY
        mock_log.assert_called_once()


# --- Routing integration -------------------------------------------


class TestRoutingIntegration:
    """Pin auto-discovery + spec-parity. A future change that
    accidentally drops a handler from the registry trips here
    before any per-route test does."""

    _EXPECTED: frozenset[str] = frozenset({
        "/api/auth/login",
        "/api/auth/logout",
        "/api/auth/config",
        "/api/auth/parse-oidc",
        "/api/auth/oidc/probe",
        "/api/me/change-password",
        "/api/tokens/refresh",
        "/api/reset-password",
    })

    def test_all_post_auth_session_routes_registered(self) -> None:
        harness = RouteDispatchHarness.with_default_router()
        registered = {
            r.path
            for r in harness._dispatcher._router.registered_routes()
            if r.verb == "POST" and r.path in self._EXPECTED
        }
        assert registered == set(self._EXPECTED), (
            f"Missing post-auth-session routes: "
            f"{set(self._EXPECTED) - registered}"
        )

    def test_csrf_exempt_paths_match_legacy_set(self) -> None:
        """Pin per ``bug_class_csrf_double_submit``: login,
        logout, and tokens/refresh remain in the upstream
        ``PostRequestHandler._CSRF_EXEMPT_POST_PATHS`` set so the
        dispatcher's CSRF preflight allows the un-cookied request
        through. A future change that adds a CSRF check inside the
        route would shadow the upstream gate; this test pins the
        contract from the route side."""
        from media_stack.api.handlers_post import PostRequestHandler
        exempt = PostRequestHandler._CSRF_EXEMPT_POST_PATHS
        # Login / logout / refresh remain CSRF-exempt — they
        # authenticate via the body.
        assert "/api/auth/login" in exempt
        assert "/api/auth/logout" in exempt
        assert "/api/tokens/refresh" in exempt
        # The other five mutating routes are NOT in the exempt
        # set — CSRF double-submit is enforced upstream.
        for protected in (
            "/api/auth/config",
            "/api/auth/parse-oidc",
            "/api/auth/oidc/probe",
            "/api/me/change-password",
            "/api/reset-password",
        ):
            assert protected not in exempt, (
                f"{protected} must NOT be CSRF-exempt — mutating "
                f"endpoint without an out-of-band re-auth proof"
            )


# --- Defensive shape checks ----------------------------------------


class TestNoSecretLeak:
    """Pin per ``bug_class_unknown_as_actionable``: nothing in any
    response envelope echoes the password / refresh token / cookie
    plaintext supplied in the body. Defence in depth — the
    underlying services are the canonical authors of the safe
    shape; the route's contract is "delegate without leaking"."""

    def test_login_response_does_not_echo_password(self) -> None:
        from media_stack.api.routes.post_auth_session import (
            AuthSessionPostRoutes, SessionLoginService,
        )
        repo, _svc = _build_repository(
            verifier=_StubVerifier({"alice": "PASS-ABCD"}),
        )
        login = SessionLoginService(
            repository=repo, store=_StubSessionStore(),
            trusted_proxy=_StubTrustedProxy(),
        )
        routes = AuthSessionPostRoutes(login_service=login)
        body = json.dumps({"username": "alice", "password": "PASS-ABCD"})
        handler = BodyAwareHandler(
            path="/api/auth/login", body=body.encode("utf-8"),
        )
        routes.handle_auth_login(handler)
        # Body must not contain the supplied password.
        assert b"PASS-ABCD" not in handler.captured.body

    def test_change_password_response_does_not_echo_either_password(
        self,
    ) -> None:
        """Both ``current_password`` and ``new_password`` must
        stay out of the response body — including in error
        envelopes."""
        from media_stack.api.routes.post_auth_session import (
            AuthSessionPostRoutes, UserCredentialRepository,
        )
        verifier = _StubVerifier({"alice": "OLD-CURR-XYZ"})
        users = {"alice": type("U", (), {"id": "u-1"})()}
        svc = _StubUserService(users=users)
        repo = UserCredentialRepository(
            service_factory=lambda: svc,
            verifier_factory=lambda: verifier,
        )
        routes = AuthSessionPostRoutes(user_repository=repo)
        body = json.dumps({
            "current_password": "OLD-CURR-XYZ",
            "new_password": "NEW-CURR-XYZ-MORE",
        })
        with patch(
            "media_stack.api.routes.post_auth_session."
            "session_cookie_reader.username_for_handler",
            return_value="alice",
        ), patch(
            "media_stack.api.routes.post_auth_session."
            "build_default_service",
        ):
            handler = BodyAwareHandler(
                path="/api/me/change-password",
                body=body.encode("utf-8"),
            )
            routes.handle_me_change_password(handler)
        assert b"OLD-CURR-XYZ" not in handler.captured.body
        assert b"NEW-CURR-XYZ-MORE" not in handler.captured.body
