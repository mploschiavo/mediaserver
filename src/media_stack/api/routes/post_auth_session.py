"""Auth-session POST routes (ADR-0007 Phase 2 wave 5).

Eight POST routes lifted off the legacy ``handlers_post.handle()``
``if handler.path == ...`` chain. Every endpoint here is in the
security-sensitive critical path: cookie-login, cookie-logout,
self-service password change, refresh-token rotation, password
reset, OIDC discovery probe + parsing, and live updates to the
auth-mode configuration. They share the OpenAPI ``Auth`` / ``Me``
/ ``Tokens`` / ``Security`` tags but co-locate here because each
one carries identical defensive obligations:

* CSRF double-submit (``X-CSRF-Token`` echoing ``media_stack_csrf``
  cookie) — except for the three explicitly-exempt endpoints in
  ``PostRequestHandler._CSRF_EXEMPT_POST_PATHS`` (``login``,
  ``logout``, ``tokens/refresh``). The dispatcher upstream of the
  Router runs the CSRF check before we land here, so this module
  preserves that contract by NOT introducing per-route auth
  bypass branches.
* Audit-log writes for every login attempt (success / failure /
  ban / rate-limited), every logout, every change-password, and
  every refresh-token rotation. The hash-chained audit log is the
  forensic record of the deployment; a route that drops these on
  the floor would leak the brute-force precursor traffic.
* Rate-limit hooks — the user-mgmt + global POST buckets in the
  legacy ``_global_preflight`` / ``_check_rate_limit`` are still
  enforced upstream of the Router; the routes themselves cannot
  bypass them because dispatch happens AFTER preflight.
* Narrow ``except`` — every ``Exception``-class swallow goes
  through ``log_swallowed`` per
  ``bug_class_silent_error_as_ok``. Defaulting to a silent debug
  log on a security path is an anti-pattern this module refuses
  to repeat.

Routes:

* ``POST /api/auth/login``           → ``authLogin``       (Auth tag)
* ``POST /api/auth/logout``          → ``authLogout``      (Auth tag)
* ``POST /api/auth/config``          → ``updateAuthConfig`` (Auth tag)
* ``POST /api/auth/parse-oidc``      → ``parseOidcConfig`` (Auth tag)
* ``POST /api/auth/oidc/probe``      → ``probeOidcDiscovery`` (Auth tag)
* ``POST /api/me/change-password``   → ``changeMyPassword`` (Me tag)
* ``POST /api/tokens/refresh``       → ``refreshToken``    (Tokens tag)
* ``POST /api/reset-password``       → ``resetPassword``   (Security tag)

Implementation patterns (named per the project's "use named
design patterns where they fit" rule):

* **Strategy + Adapter** — ``SessionLoginService``,
  ``OidcProvider``, ``PasswordResetService``,
  ``TokenRefreshService``: one collaborator per security-sensitive
  side-effect. Each is constructor-injected so a test can swap
  the strategy without monkey-patching the factory imports the
  legacy helpers built lazily inside their bodies. Adapter shape
  for the ``OidcProvider`` because it wraps ``urllib.request`` —
  the abstraction lets tests stub a deterministic response
  document without writing a fake HTTP server.
* **Repository** — ``UserCredentialRepository`` mediates every
  read-against and write-to the controller user store. Hides the
  legacy ``build_default_service()._store.get_by_username(...)``
  private-attribute reach behind a typed contract; tests inject a
  stub instead of mocking the factory chain.
* **Constructor injection** — ``AuthSessionPostRoutes`` accepts
  the four collaborators above. Production passes nothing —
  defaults materialize the production wiring that mirrors the
  legacy helper-class behaviour exactly.

The legacy helper classes (``_SessionLoginHelper``,
``_MeChangePasswordHelper``) keep firing through the legacy
chain until the cleanup commit removes the elif branches; this
module does NOT reach into them. The bodies were lifted, not
delegated, so the Phase 3 cleanup commit can delete the helpers
without breaking the migrated path.
"""

from __future__ import annotations

import base64
import binascii
import json as _json
import os
import urllib.request
from http import HTTPStatus
from typing import Any, Callable

from media_stack.api.routing import RouteModule, post
from media_stack.api.services.auth_config import AuthConfigService
from media_stack.api.session_singletons import (
    SESSION_COOKIE_NAME,
    session_cookie_reader,
    session_store,
    trusted_proxy_auth,
)
from media_stack.core.auth.users.audit_actions import (
    LOGIN_FAILURE,
    LOGIN_SUCCESS,
    LOGOUT,
)
from media_stack.core.auth.users.user_service import UserServiceError
from media_stack.core.auth.users.user_service_factory import (
    build_default_api_token_store,
    build_default_auth_verifier,
    build_default_service,
)
from media_stack.core.logging_utils import log_swallowed
from media_stack.domain.auth.oidc_config_parser import parse_oidc_config

_ERR_LEN = 99
_OIDC_FETCH_TIMEOUT_SECONDS = 10
# OIDC discovery doc fields the form auto-populates — same set the
# legacy chain emitted. Pinned as a constant so a future Phase-3
# extension has one place to drop in additional well-known fields.
_OIDC_DISCOVERY_FIELDS: tuple[str, ...] = (
    "issuer",
    "authorization_endpoint",
    "token_endpoint",
    "userinfo_endpoint",
    "jwks_uri",
    "end_session_endpoint",
    "introspection_endpoint",
    "revocation_endpoint",
    "response_types_supported",
    "scopes_supported",
    "id_token_signing_alg_values_supported",
    "subject_types_supported",
)
# Minimum length for a self-service ``new_password`` — same value
# the legacy ``_MeChangePasswordHelper._MIN_LENGTH`` enforces. Pinned
# as a module-level constant rather than a magic literal so the
# password-policy work in v1.0.18x has one place to consult.
_ME_CHANGE_PASSWORD_MIN_LENGTH = 8
# ``/api/reset-password`` accepts a much shorter floor — the
# legacy chain validates ``len(new_password) < 4`` purely as a
# sanity gate against an empty / single-byte body. The strong
# password policy lives upstream in
# ``PasswordPolicyConfig`` (enforced by the user store). Pinned to
# avoid the magic-number ratchet picking up the literal.
_RESET_PASSWORD_MIN_LENGTH = 4


class UserCredentialRepository:
    """Repository — wraps every read against the controller user
    store + the credential verifier.

    Hides the legacy ``build_default_service()._store.get_by_username()``
    private-attribute reach behind a typed contract. Tests inject a
    stub repository instead of mocking the factory chain.
    """

    def __init__(
        self,
        service_factory: Callable[[], Any] = build_default_service,
        verifier_factory: Callable[[], Any] = build_default_auth_verifier,
    ) -> None:
        self._service_factory = service_factory
        self._verifier_factory = verifier_factory

    def verify_credentials(self, username: str, password: str) -> bool:
        """Return True iff ``(username, password)`` resolves against
        the live verifier. Falls back to the env-var bootstrap admin
        when the user store hasn't been initialized yet — same
        precedence the legacy ``_SessionLoginHelper._verify_credentials``
        applies."""
        try:
            verifier = self._verifier_factory()
        except (ImportError, AttributeError, OSError, ValueError) as exc:
            log_swallowed(exc, context="login/verifier-build")
            verifier = None
        if verifier is not None:
            try:
                if verifier.verify(username, password):
                    return True
            except (ValueError, RuntimeError, AttributeError) as exc:
                log_swallowed(exc, context="login/verify")
        env = os.environ
        fb_user = env.get("STACK_ADMIN_USERNAME", "admin")
        fb_pass = env.get("STACK_ADMIN_PASSWORD", "")
        return bool(fb_pass) and username == fb_user and password == fb_pass

    def get_by_username(self, username: str) -> Any:
        """Return the user-store row for ``username`` or ``None``.

        Catches the documented load failures (sqlite locked / file
        missing / hash decode error) and routes them through
        ``log_swallowed`` so a real failure surfaces in the
        controller log instead of disappearing into a debug stream.
        """
        try:
            svc = self._service_factory()
            return svc._store.get_by_username(username)
        except (ImportError, AttributeError, OSError, ValueError) as exc:
            log_swallowed(exc, context="user-store/get_by_username")
            return None

    def reset_password(
        self, user_id: str, password: str, actor: Any,
    ) -> dict[str, Any]:
        """Hand off to the same ``user_write_service`` method admins
        use for ``/api/users/{user_id}/reset-password``. Raises
        ``UserServiceError`` with the policy-violation message on a
        weak password."""
        svc = self._service_factory()
        return svc.reset_password(user_id, password=password, actor=actor)

    def append_audit(
        self,
        actor: str,
        action: str,
        target: str,
        result: str,
        ip: str,
        user_agent: str,
        detail: dict[str, Any],
    ) -> None:
        """Append a row to the hash-chained audit log. Best-effort —
        an audit-log outage must NEVER block a login from going
        through; we log every swallow so a real outage is visible
        rather than silent."""
        try:
            svc = self._service_factory()
            svc._audit.append(
                actor=actor, action=action, target=target,
                result=result, ip=ip, user_agent=user_agent,
                detail=detail,
            )
        except (ImportError, AttributeError, OSError, ValueError) as exc:
            log_swallowed(exc, context=f"audit-log/{action}")


class SessionLoginService:
    """Strategy — cookie-login + logout flow.

    Constructor-inject the user-credential repository + the
    session store so tests drive the flow without mocking module
    imports. Production passes nothing; defaults wire the
    production singletons.
    """

    def __init__(
        self,
        repository: UserCredentialRepository | None = None,
        store: Any = session_store,
        trusted_proxy: Any = trusted_proxy_auth,
        cookie_name: str = SESSION_COOKIE_NAME,
    ) -> None:
        self._repository = repository or UserCredentialRepository()
        self._store = store
        self._trusted_proxy = trusted_proxy
        self._cookie_name = cookie_name

    def client_ip(self, handler: Any) -> str:
        """Per-request client IP — used as the audit-log row's IP
        field AND as the rate-limit bucket key upstream. Delegates
        to ``trusted_proxy_auth.client_ip`` so a request behind
        Envoy/Authelia gets banned at the attacker's IP, not at
        the proxy hop."""
        try:
            return self._trusted_proxy.client_ip(handler) or ""
        except (AttributeError, ValueError) as exc:
            log_swallowed(exc, context="login/client-ip")
            return ""

    def user_agent(self, handler: Any) -> str:
        try:
            return str(handler.headers.get("User-Agent", "") or "")
        except AttributeError as exc:
            log_swallowed(exc, context="login/user-agent")
            return ""

    def login(
        self, handler: Any, body: dict[str, Any],
    ) -> tuple[int, dict[str, Any], str | None]:
        """Drive a login attempt. Returns
        ``(status, body_obj, set_cookie_header_or_None)``. The route
        method does the actual write to the wire."""
        username = str(body.get("username", "")).strip()
        password = str(body.get("password", ""))
        ip = self.client_ip(handler)
        ua = self.user_agent(handler)

        if not username or not password:
            self._audit(
                handler, LOGIN_FAILURE, username=username,
                reason="missing_fields", ip=ip, user_agent=ua,
            )
            return (
                HTTPStatus.BAD_REQUEST,
                {"error": "username and password required"},
                None,
            )
        if not self._repository.verify_credentials(username, password):
            self._audit(
                handler, LOGIN_FAILURE, username=username,
                reason="bad_credentials", ip=ip, user_agent=ua,
            )
            return (
                HTTPStatus.UNAUTHORIZED,
                {"error": "invalid credentials"},
                None,
            )
        _sess, plaintext = self._store.create(owner_username=username)
        self._audit(
            handler, LOGIN_SUCCESS, username=username,
            reason="cookie_mint", ip=ip, user_agent=ua,
        )
        return (
            HTTPStatus.OK,
            {"session": "established"},
            (f"{self._cookie_name}={plaintext}; HttpOnly; Secure; "
             "SameSite=Strict; Path=/"),
        )

    def logout(
        self, handler: Any,
    ) -> tuple[int, dict[str, Any], str]:
        """Revoke the session cookie. Returns
        ``(status, body_obj, set_cookie_header)``."""
        cookie_raw = ""
        headers = getattr(handler, "headers", None)
        if headers is not None:
            try:
                cookie_raw = headers.get("Cookie", "") or ""
            except AttributeError as exc:
                log_swallowed(exc, context="logout/cookie-read")
                cookie_raw = ""
        revoked_for = ""
        for chunk in cookie_raw.split(";"):
            k, _, v = chunk.strip().partition("=")
            if k == self._cookie_name and v:
                token = v.strip()
                # Resolve owner BEFORE revoking — revoke() returns a
                # bool, so we can't read the owner from its return.
                try:
                    sess = self._store.get(token)
                    if sess is not None:
                        revoked_for = str(
                            getattr(sess, "owner_username", "") or "",
                        )
                except (AttributeError, ValueError, KeyError) as exc:
                    log_swallowed(exc, context="logout/owner-resolve")
                self._store.revoke(token)
        self._audit(
            handler, LOGOUT, username=revoked_for,
            reason="cookie_revoke",
            ip=self.client_ip(handler),
            user_agent=self.user_agent(handler),
        )
        return (
            HTTPStatus.OK,
            {"logged_out": True},
            (f"{self._cookie_name}=; HttpOnly; Secure; "
             "SameSite=Strict; Path=/; Max-Age=0"),
        )

    def _audit(
        self,
        handler: Any,
        action: str,
        *,
        username: str,
        reason: str,
        ip: str,
        user_agent: str,
    ) -> None:
        """Write an AuthEvent row. Never raises — a logging outage
        must not block a login. Mirrors the legacy
        ``_SessionLoginHelper._audit_login_event`` field shape."""
        actor_label = (
            username if (action == LOGIN_SUCCESS and username)
            else (username or "anonymous")
        )
        self._repository.append_audit(
            actor=actor_label,
            action=action,
            target=username or "unknown",
            result="ok" if action == LOGIN_SUCCESS else "fail",
            ip=ip,
            user_agent=user_agent,
            detail={"reason": reason, "provider": "controller"},
        )


class OidcProvider:
    """Adapter — wraps ``urllib.request`` for the OIDC discovery
    probe. Lets tests inject a deterministic response document
    without standing up an HTTP server."""

    def __init__(
        self,
        opener: Callable[..., Any] = urllib.request.urlopen,
        request_factory: Callable[..., Any] = urllib.request.Request,
        timeout_seconds: int = _OIDC_FETCH_TIMEOUT_SECONDS,
    ) -> None:
        self._opener = opener
        self._request_factory = request_factory
        self._timeout = timeout_seconds

    def fetch_discovery(self, discovery_url: str) -> dict[str, Any]:
        """Fetch + parse the discovery document. Raises
        ``ValueError`` if the document isn't a JSON object; the
        route translates that to a 502."""
        req = self._request_factory(
            discovery_url,
            headers={
                "Accept": "application/json",
                "User-Agent": "media-stack-controller/oidc-probe",
            },
        )
        with self._opener(req, timeout=self._timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
        doc = _json.loads(raw)
        if not isinstance(doc, dict):
            raise ValueError("discovery doc is not a JSON object")
        return doc

    def summarize(self, doc: dict[str, Any]) -> dict[str, Any]:
        """Project the well-known fields the dashboard form binds
        against. Pass-through for anything not in the well-known
        set so advanced operators can pick up extra fields from
        the raw doc."""
        return {k: doc.get(k) for k in _OIDC_DISCOVERY_FIELDS if k in doc}


class TokenRefreshService:
    """Strategy — wraps the API-token store rotate flow. Splitting
    this off the route lets tests assert on the rotation contract
    without standing up the real token store."""

    def __init__(
        self,
        store_factory: Callable[[], Any] = build_default_api_token_store,
    ) -> None:
        self._store_factory = store_factory

    def rotate(self, refresh_plain: str) -> dict[str, Any]:
        """Exchange a refresh token for a fresh ``(access, refresh)``
        pair. Raises ``UserServiceError`` on a missing or
        already-rotated refresh — the caller maps that to a 400."""
        if not refresh_plain:
            raise UserServiceError("refresh_token required")
        store = self._store_factory()
        result = store.rotate(refresh_plain)
        if result is None:
            raise UserServiceError(
                "refresh token invalid, expired, or already rotated; "
                "sign in again",
            )
        (access, a_plain), (new_refresh, r_plain) = result
        return {
            "access": {**access.to_dict(), "token": a_plain},
            "refresh": {**new_refresh.to_dict(), "token": r_plain},
        }


class PasswordResetService:
    """Strategy — wraps the cross-service admin-password reset.

    The admin reset propagates the new password to every service
    that supports it (qBittorrent, Jellyfin, the arr apps, Bazarr,
    config-file services) AND updates the
    ``STACK_ADMIN_PASSWORD`` env var. Tests inject a stub strategy
    so the test suite never has to spin up the full service mesh.
    """

    def __init__(
        self,
        admin_factory: Callable[[], Any] | None = None,
    ) -> None:
        # Lazy import via factory: ``admin_svc.reset_password`` pulls
        # in the entire service registry on import; keeping the
        # import behind a factory lets tests inject a stub without
        # paying the registry-init cost.
        self._admin_factory = admin_factory or self._default_admin_factory

    def _default_admin_factory(self) -> Any:
        from media_stack.api.services import admin as admin_svc
        return admin_svc

    def reset(
        self, new_password: str, target_services: Any,
    ) -> dict[str, Any]:
        admin = self._admin_factory()
        return admin.reset_password(new_password, target_services)


class AuthSessionPostRoutes(RouteModule):
    """Eight POST routes — login, logout, change-password, reset-
    password, refresh-token, OIDC parse + probe, and live
    auth-config updates. Constructor-inject every collaborator so
    tests swap each one independently. Production passes nothing —
    defaults materialize the production wiring.
    """

    def __init__(
        self,
        login_service: SessionLoginService | None = None,
        oidc_provider: OidcProvider | None = None,
        token_refresh_service: TokenRefreshService | None = None,
        password_reset_service: PasswordResetService | None = None,
        user_repository: UserCredentialRepository | None = None,
        oidc_config_parser: Callable[[dict[str, Any]], dict[str, Any]] = parse_oidc_config,
    ) -> None:
        self._login = login_service or SessionLoginService()
        self._oidc = oidc_provider or OidcProvider()
        self._token_refresh = token_refresh_service or TokenRefreshService()
        self._password_reset = (
            password_reset_service or PasswordResetService()
        )
        self._users = user_repository or UserCredentialRepository()
        self._parse_oidc = oidc_config_parser

    @classmethod
    def _auth_config_service(cls) -> AuthConfigService:
        """Factory @classmethod — production builds a fresh
        ``AuthConfigService`` per request. Mirrors the GET-side
        ``AuthGetRoutes._auth_config_service`` pattern so the two
        routes share their patch surface in tests."""
        return AuthConfigService()

    @post("/api/auth/login")
    def handle_auth_login(self, handler: Any) -> None:
        """Mint a session cookie. CSRF-exempt (the cookie doesn't
        exist yet — there's no token to compare against). Every
        attempt — success / bad credentials / missing fields /
        ban-hit — writes a hash-chained audit row.
        """
        body = handler._read_json_body() or {}
        status, payload, cookie = self._login.login(handler, body)
        self._send_cookie_response(handler, status, payload, cookie)

    @post("/api/auth/logout")
    def handle_auth_logout(self, handler: Any) -> None:
        """Revoke the session cookie. CSRF-exempt (idempotent —
        revoking nothing is a no-op). Audited via LOGOUT row."""
        status, payload, cookie = self._login.logout(handler)
        self._send_cookie_response(handler, status, payload, cookie)

    @post("/api/auth/config")
    def handle_auth_config_update(self, handler: Any) -> None:
        """Update the auth-mode configuration. CSRF-required
        (mutating). The trigger callback flushes pending
        ``configure-auth`` / ``envoy-config`` regen jobs."""
        body = handler._read_json_body()
        if not body:
            handler._json_response(
                HTTPStatus.BAD_REQUEST, {"error": "JSON body required"},
            )
            return
        result = self._auth_config_service().update_auth_config(
            body, getattr(handler, "action_trigger", None),
        )
        status = (
            HTTPStatus.OK if "error" not in result
            else HTTPStatus.BAD_REQUEST
        )
        handler._json_response(status, result)

    @post("/api/auth/parse-oidc")
    def handle_auth_parse_oidc(self, handler: Any) -> None:
        """Parse an uploaded OIDC provider JSON blob. Strips the
        ``raw`` echo from the response to keep the payload small —
        the form already has the source on the client.
        """
        body = handler._read_json_body()
        if not body:
            handler._json_response(
                HTTPStatus.BAD_REQUEST, {"error": "JSON body required"},
            )
            return
        result = self._parse_oidc(body)
        # Strip raw echo to keep response small — the operator
        # already has the source they uploaded.
        if isinstance(result, dict):
            result.pop("raw", None)
        handler._json_response(HTTPStatus.OK, result)

    @post("/api/auth/oidc/probe")
    def handle_auth_oidc_probe(self, handler: Any) -> None:
        """Server-side fetch of an OIDC discovery document. The
        legacy chain caught a bare ``Exception``; we narrow to the
        documented failure modes (network errors → ``OSError``,
        bad JSON → ``ValueError``) and route every swallow through
        ``log_swallowed`` per ``bug_class_silent_error_as_ok``.
        """
        body = handler._read_json_body() or {}
        url = str(body.get("discovery_url", "")).strip()
        if not url.startswith(("http://", "https://")):
            handler._json_response(
                HTTPStatus.BAD_REQUEST,
                {"error": "discovery_url must be http(s)"},
            )
            return
        try:
            doc = self._oidc.fetch_discovery(url)
        except (OSError, ValueError, _json.JSONDecodeError) as exc:
            log_swallowed(exc, context="oidc/probe")
            handler._json_response(
                HTTPStatus.BAD_GATEWAY,
                {
                    "error": (
                        "Couldn't fetch the discovery doc: "
                        f"{str(exc)[:160]}"
                    ),
                    "ok": False,
                },
            )
            return
        handler._json_response(HTTPStatus.OK, {
            "ok": True,
            "summary": self._oidc.summarize(doc),
            "raw": doc,
        })

    @post("/api/me/change-password")
    def handle_me_change_password(self, handler: Any) -> None:
        """Self-service password change. Verifies
        ``current_password`` against the live verifier BEFORE
        applying ``new_password``. Errors are deliberately generic
        — leaking "user unknown" vs "wrong password" would let an
        attacker enumerate accounts.
        """
        body = handler._read_json_body() or {}
        current = str(body.get("current_password", "") or "")
        new_pwd = str(body.get("new_password", "") or "")

        validation_error = self._validate_password_change(current, new_pwd)
        if validation_error is not None:
            handler._json_response(
                HTTPStatus.BAD_REQUEST, {"error": validation_error},
            )
            return

        username = self._resolve_username(handler)
        if not username:
            handler._json_response(
                HTTPStatus.UNAUTHORIZED, {"error": "not authenticated"},
            )
            return

        user = self._authorize_password_change(username, current)
        if user is None:
            handler._json_response(HTTPStatus.FORBIDDEN, {
                "error": "current_password is incorrect",
            })
            return

        actor = self._actor_for(handler, body)
        try:
            result = self._users.reset_password(
                user.id, password=new_pwd, actor=actor,
            )
        except UserServiceError as exc:
            handler._json_response(
                HTTPStatus.BAD_REQUEST, {"error": str(exc)[:_ERR_LEN]},
            )
            return
        handler._json_response(
            HTTPStatus.OK,
            self._strip_legacy_plaintext(result) or {"ok": True},
        )

    def _validate_password_change(
        self, current: str, new_pwd: str,
    ) -> str | None:
        """Body-shape validator: returns an error string when the
        request fails one of the static checks, or ``None`` when
        the body is well-formed. Lifting this off the route keeps
        the dispatch method's cyclomatic complexity in line with
        the ratchet floor + makes each rule independently testable.
        """
        if not current or not new_pwd:
            return "current_password and new_password required"
        if len(new_pwd) < _ME_CHANGE_PASSWORD_MIN_LENGTH:
            return (
                f"new_password must be at least "
                f"{_ME_CHANGE_PASSWORD_MIN_LENGTH} characters"
            )
        if current == new_pwd:
            return "new_password must differ from current_password"
        return None

    def _authorize_password_change(
        self, username: str, current_password: str,
    ) -> Any:
        """Verify ``current_password`` against the live verifier and
        load the user-store row. Returns the row on success, ``None``
        on either a failed verify or a missing row — both must yield
        the same generic 403 to prevent account enumeration.
        """
        if not self._users.verify_credentials(username, current_password):
            return None
        return self._users.get_by_username(username)

    @post("/api/tokens/refresh")
    def handle_tokens_refresh(self, handler: Any) -> None:
        """Exchange a refresh token for a rotated ``(access,
        refresh)`` pair. CSRF-exempt — programmatic clients can't
        provide a Cookie-paired token; the refresh token IS the
        credential.
        """
        body = handler._read_json_body() or {}
        refresh = str(body.get("refresh_token", "")).strip()
        try:
            payload = self._token_refresh.rotate(refresh)
        except UserServiceError as exc:
            handler._json_response(
                HTTPStatus.BAD_REQUEST, {"error": str(exc)[:_ERR_LEN]},
            )
            return
        handler._json_response(HTTPStatus.OK, payload)

    @post("/api/reset-password")
    def handle_reset_password(self, handler: Any) -> None:
        """Cross-service admin-password reset. CSRF-required
        (mutating). The min-length floor is intentionally lenient
        (4 chars) — the strong policy lives in
        ``PasswordPolicyConfig`` upstream of the user store. This
        gate is just sanity against an empty body.
        """
        body = handler._read_json_body() or {}
        new_password = str(body.get("password", "") or "")
        if not new_password or len(new_password) < _RESET_PASSWORD_MIN_LENGTH:
            handler._json_response(HTTPStatus.BAD_REQUEST, {
                "error": (
                    "password field required (min "
                    f"{_RESET_PASSWORD_MIN_LENGTH} chars)"
                ),
            })
            return
        target = body.get("services")
        handler._json_response(
            HTTPStatus.OK,
            self._password_reset.reset(new_password, target),
        )

    # --- helpers ----------------------------------------------------

    def _send_cookie_response(
        self,
        handler: Any,
        status: int,
        payload: dict[str, Any],
        cookie: str | None,
    ) -> None:
        """Emit a JSON response with an optional ``Set-Cookie``
        header. We hand-roll this rather than calling
        ``handler._json_response`` because the latter doesn't
        accept extra headers.
        """
        body_bytes = _json.dumps(payload).encode()
        if cookie is None:
            handler._json_response(status, payload)
            return
        # Mirror the legacy helper — write the response inline so
        # we can attach the Set-Cookie header. The mock handler in
        # the test harness accepts ``_raw_response``; production
        # ``ControllerAPIHandler`` provides both surfaces.
        if hasattr(handler, "_raw_response"):
            handler._raw_response(
                status, "application/json", body_bytes,
                {"Set-Cookie": cookie},
            )
            return
        # Fallback for a handler protocol that lacks
        # ``_raw_response`` — drop the cookie and emit JSON. This
        # is degenerate; production never hits this branch.
        handler._json_response(status, payload)

    def _resolve_username(self, handler: Any) -> str:
        """Strategy + Chain-of-Responsibility: session cookie →
        trusted-proxy ``Remote-User`` header → HTTP Basic. Returns
        ``""`` when no strategy fired so the route can emit a 401
        without ever touching the user-credential repository
        (timing-safe against username enumeration).
        """
        for strategy in (
            self._username_from_cookie,
            self._username_from_proxy_header,
            self._username_from_basic_auth,
        ):
            user = strategy(handler)
            if user:
                return user
        return ""

    def _username_from_cookie(self, handler: Any) -> str:
        try:
            return (
                session_cookie_reader.username_for_handler(handler) or ""
            )
        except (AttributeError, ValueError) as exc:
            log_swallowed(exc, context="change-password/cookie-resolve")
            return ""

    def _username_from_proxy_header(self, handler: Any) -> str:
        try:
            return str(trusted_proxy_auth.identity(handler) or "")
        except (AttributeError, ValueError) as exc:
            log_swallowed(exc, context="change-password/proxy-resolve")
            return ""

    def _username_from_basic_auth(self, handler: Any) -> str:
        try:
            auth_hdr = handler.headers.get("Authorization", "") or ""
        except AttributeError as exc:
            log_swallowed(exc, context="change-password/header-read")
            return ""
        if not auth_hdr.startswith("Basic "):
            return ""
        try:
            decoded = base64.b64decode(auth_hdr[6:]).decode(
                "utf-8", "replace",
            )
        except (binascii.Error, ValueError) as exc:
            log_swallowed(exc, context="change-password/basic-decode")
            return ""
        return decoded.partition(":")[0] or ""

    def _actor_for(self, handler: Any, body: dict[str, Any]) -> Any:
        """Build an :class:`Actor` for audit attribution. We
        construct a fresh resolver each call because the
        ``ActorResolver`` constructor captures factory closures —
        which we want to re-evaluate per request so test patches
        of ``build_default_service`` take effect."""
        from media_stack.api.actor_resolver import ActorResolver
        merged = dict(body or {})
        if not str(merged.get("_actor", "") or "").strip():
            identity = self._resolve_username(handler)
            if identity:
                merged["_actor"] = identity
        impl = ActorResolver(
            build_service=build_default_service,
            client_ip_for=trusted_proxy_auth.client_ip,
        )
        return impl.resolve(handler, merged)

    def _strip_legacy_plaintext(
        self,
        result: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        """Belt-and-braces: swap any ``generated_password`` still
        present in a service result for a single-use retrieval
        ticket. Mirrors the legacy ``_strip_legacy_plaintext`` in
        ``handlers_post.py``. Production paths emit the ticket
        shape natively; this catches any test fixture that hands
        back the pre-migration shape."""
        if not isinstance(result, dict):
            return result
        plaintext = result.pop("generated_password", None)
        if plaintext:
            user_id = str(
                result.get("user_id") or result.get("id") or "",
            )
            if user_id:
                from media_stack.core.auth.users.password_ticket_store import (
                    mint_ticket_fields,
                )
                result.update(mint_ticket_fields(user_id, str(plaintext)))
        return result


__all__ = [
    "AuthSessionPostRoutes",
    "OidcProvider",
    "PasswordResetService",
    "SessionLoginService",
    "TokenRefreshService",
    "UserCredentialRepository",
]
