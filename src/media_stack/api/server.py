"""Controller HTTP API server — thin routing layer over service modules.

Handles URL dispatch, auth, SSE streaming, and response formatting.
Business logic lives in api/services/*.py modules.
Route handling lives in api/handlers_get.py and api/handlers_post.py.
"""

from __future__ import annotations


from media_stack.core.logging_utils import log_swallowed
import base64
import json
import logging
import os
import signal
import threading
import time
import urllib.request
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable

from .state import ControllerState
from . import handlers_get
from . import handlers_post

try:
    from media_stack.core.auth.users.user_service_factory import (
        build_default_auth_verifier as _build_auth_verifier,
        build_default_scheduled_reconciler as _build_sched_reconciler,
        build_default_api_token_store as _build_token_store,
        build_default_service as _build_user_service,
        build_default_audit_chain_verifier as _build_audit_verifier,
    )
except ImportError:
    _build_auth_verifier = None
    _build_sched_reconciler = None
    _build_token_store = None
    _build_user_service = None
    _build_audit_verifier = None

from media_stack.core.auth.failed_login_tracker import FailedLoginTracker
from media_stack.core.observability.security_counters import security_counters
from media_stack.api.session_singletons import (
    SESSION_COOKIE_NAME as _SESSION_COOKIE_NAME,
    session_cookie_reader,
    session_store as _session_store,
    trusted_proxy_auth as _trusted_proxy_auth,
)
from media_stack.api.login_page import LOGIN_HTML as _LOGIN_HTML
from media_stack.core.auth.csrf import CsrfProtector as _CsrfProtector
from media_stack.core.auth.security_headers import (
    LEGACY_DASHBOARD_POLICY as _LEGACY_DASHBOARD_POLICY_BASE,
    apply_policy as _apply_security_policy,
)

# ControllerAPIHandler already emits ``Server: media-stack`` via its
# ``version_string`` override on every send_response. Emitting it a
# SECOND time through the policy would land two ``Server`` lines in
# the wire response. Strip the banner bit from the policy so the
# handler's built-in path owns that header.
_LEGACY_DASHBOARD_POLICY = _LEGACY_DASHBOARD_POLICY_BASE.with_overrides(
    strip_server_banner=False,
)

_csrf_issuer = _CsrfProtector()


_LOOPBACK_IPS = frozenset({"127.0.0.1", "::1", "localhost"})


def _is_private_or_loopback(client_ip: str) -> bool:
    """True when the IP is loopback OR an RFC 1918 private range.

    The IP lockout exists to slow internet-origin brute-force. Every
    'private' origin — the dev's loopback, the docker bridge gateway
    (browser→localhost:9100 shows up as 172.21.0.1 inside the
    container), same-LAN clients — is categorically NOT a brute-force
    threat model. Locking them out turns routine dev/LAN use into a
    'dashboard is 429' paper cut. Internet-facing deployments sit
    behind a reverse proxy that rewrites X-Forwarded-For; the lockout
    is still effective against real attackers there."""
    if not client_ip or client_ip in _LOOPBACK_IPS:
        return True
    try:
        import ipaddress
        ip = ipaddress.ip_address(client_ip)
        return ip.is_loopback or ip.is_private or ip.is_link_local
    except ValueError:
        return False


def _should_reject_for_ip_lockout(client_ip: str) -> bool:
    """True when the tracker says this IP is locked AND the IP isn't
    a loopback/private address."""
    if _is_private_or_loopback(client_ip):
        return False
    return _ip_failure_tracker.is_locked(client_ip)


def _issue_csrf_if_missing(handler) -> None:
    """Free-standing Set-Cookie emitter for the double-submit CSRF
    token. Called from _json_response / _html_response on GETs. Not a
    method on ControllerAPIHandler to keep its method count under the
    class-size ratchet. See CsrfProtector for cookie name / format."""
    if getattr(handler, "command", "") != "GET":
        return
    try:
        cookie_header = handler.headers.get("Cookie", "") if getattr(
            handler, "headers", None) else ""
    except AttributeError:
        cookie_header = ""
    if _csrf_issuer.extract_cookie(cookie_header):
        return
    xfp = ""
    try:
        xfp = (handler.headers.get("X-Forwarded-Proto", "") or "").strip().lower()
    except AttributeError:
        logging.getLogger("media_stack").debug("[DEBUG] Swallowed exception", exc_info=True)
    token = _csrf_issuer.issue_token()
    handler.send_header(
        "Set-Cookie",
        _csrf_issuer.build_set_cookie(token, secure=xfp == "https"),
    )

# Per-IP lockout: after _IP_LOCKOUT_THRESHOLD failures within
# _IP_LOCKOUT_WINDOW seconds, the IP is rejected outright for
# _IP_LOCKOUT_COOLDOWN. Keyed by source IP, so credential-stuffing
# across many usernames still trips the lock. Independent from the
# per-account tracker used by BasicAuthVerifier.
_IP_LOCKOUT_THRESHOLD = 20
_IP_LOCKOUT_WINDOW = 5 * 60
_IP_LOCKOUT_COOLDOWN = 15 * 60
_ip_failure_tracker = FailedLoginTracker(
    threshold=_IP_LOCKOUT_THRESHOLD,
    window_seconds=_IP_LOCKOUT_WINDOW,
    cooldown_seconds=_IP_LOCKOUT_COOLDOWN,
)


_H_CONTENT_LENGTH = "Content-Length"
_H_AUTHORIZATION = "Authorization"
# Cap request body at 1 MiB. Bulk CSV imports and config uploads stay
# well under this; anything larger is either a mistake or an attack.
_MAX_BODY_BYTES = 1 * (2 ** 20)

# Forward-auth integration: the trusted_proxy_auth singleton (imported
# below from session_singletons) accepts Remote-User from Authelia via
# Envoy ext_authz, but only when the request came from a CIDR listed
# in CONTROLLER_TRUSTED_PROXY_CIDRS. Without a trusted-proxy config,
# these headers are ignored so an attacker can't spoof identity by
# setting the header themselves.


class _AuthPolicy:
    """Encapsulates the auth decision + bearer-token verification.

    Extracted out of ControllerAPIHandler so that class stays under the
    class-method ratchet. Instance methods take the live handler so they
    can read headers/command/path without ControllerAPIHandler having to
    carry the logic."""

    _PUBLIC_PATHS = frozenset({
        "/healthz", "/readyz", "/webhooks/arr",
        # Sonarr's CustomImport poller hits this over internal docker
        # DNS with no Authorization header. The feed is derived from
        # TVMaze — no secrets exposed. Requiring auth here causes
        # Sonarr to log "BaseUrl: Authentication Failure" and drop
        # the list from the active set. (v1.0.143.)
        "/api/discovery/popular-tv",
    })

    def __init__(self) -> None:
        self._env = os.environ

    def is_public(self, handler, path: str) -> bool:
        if path in self._PUBLIC_PATHS:
            return True
        command = getattr(handler, "command", "")
        if path == "/api/invites/accept" and command == "POST":
            return True
        # Login endpoint MUST be public — otherwise users have no way
        # to obtain a session cookie. It has its own rate limit + CSRF
        # exemption (see handlers_post._CSRF_EXEMPT_POST_PATHS).
        if path == "/api/auth/login" and command == "POST":
            return True
        # Logout is idempotent and safe to hit without auth.
        if path == "/api/auth/logout" and command == "POST":
            return True
        # Token refresh carries its own credential (the refresh token),
        # so no Authorization header is required to present it.
        if path == "/api/tokens/refresh" and command == "POST":
            return True
        return False

    def decision(self, handler, path: str, password: str) -> str:
        auth_mode = self._env.get("CONTROLLER_AUTH", "").strip().lower()
        if not auth_mode:
            auth_mode = "all" if password else "none"
        if auth_mode == "none":
            return "allow"
        is_sensitive = (
            path.startswith("/api/")
            or path == "/metrics"
            or path.startswith("/logs/")
        )
        if auth_mode == "write" and handler.command == "GET" and not is_sensitive:
            return "allow"
        return "require"

    def verify_session_cookie(self, handler) -> str:
        return session_cookie_reader.username_for_handler(handler)

    def verify_bearer(self, handler, plaintext: str) -> bool:
        if _build_token_store is None or not plaintext:
            return False
        try:
            tok = _build_token_store().verify(plaintext)
        except Exception as exc:  # noqa: BLE001
            logging.getLogger("media_stack").debug(
                "[DEBUG] token verify failed: %s", exc,
            )
            return False
        if tok is None:
            return False
        if tok.scope == "read" and handler.command != "GET":
            return False
        return True

    def send_401(self, handler) -> None:
        """Emit an unauthenticated response.

        For browsers we NEVER return WWW-Authenticate: Basic — that
        triggers the ugly native credential popup. Instead we either
        (a) 302 to an upstream OIDC login when CONTROLLER_OIDC_LOGIN_REDIRECT
        is set, or (b) render a styled in-page login form that POSTs
        to /api/auth/login and cookies the response.

        Scripts and API clients (Accept: application/json, non-HTML,
        non-GET) still get a 401 with WWW-Authenticate so curl and
        other tooling behave predictably.
        """
        is_browser = self._is_browser_navigation(handler)
        login_url = self._env.get("CONTROLLER_OIDC_LOGIN_REDIRECT", "").strip()
        if is_browser and login_url:
            handler.send_response(HTTPStatus.FOUND)
            handler.send_header("Location", login_url)
            handler.send_header(_H_CONTENT_LENGTH, "0")
            handler.end_headers()
            return
        if is_browser:
            self._send_login_page(handler)
            return
        handler.send_response(HTTPStatus.UNAUTHORIZED)
        handler.send_header(
            "WWW-Authenticate", 'Basic realm="Media Stack Controller"',
        )
        handler.send_header(_H_CONTENT_LENGTH, "0")
        handler.end_headers()

    def _send_login_page(self, handler) -> None:
        """Render a minimal styled login form. Submits to /api/auth/login
        which sets the session cookie; on success the browser reloads
        into the dashboard.

        Also emits the CSRF cookie — the login form itself doesn't use
        it, but once the user is signed in and hits any dashboard POST,
        the cookie must already be present or the POST 403s with
        ``CSRF token missing or invalid``. Setting it here makes the
        cookie available across the login transition."""
        rd = getattr(handler, "path", "/") or "/"
        html = _LOGIN_HTML.replace("__RD__", rd)
        payload = html.encode("utf-8")
        handler.send_response(HTTPStatus.UNAUTHORIZED)
        handler.send_header("Content-Type", "text/html; charset=utf-8")
        handler.send_header(_H_CONTENT_LENGTH, str(len(payload)))
        # Don't send WWW-Authenticate here — it forces the browser's
        # native credential dialog and our form goes unused.
        self.emit_security_headers(handler)
        _issue_csrf_if_missing(handler)
        handler.end_headers()
        handler.wfile.write(payload)

    def _is_browser_navigation(self, handler) -> bool:
        """True for GET requests whose Accept header prefers HTML.
        API clients (curl/fetch with Accept: application/json) fail this
        check so they still see a proper 401 rather than a confusing
        302 that leads nowhere they can follow."""
        if getattr(handler, "command", "") != "GET":
            return False
        headers = getattr(handler, "headers", None)
        if headers is None:
            return False
        try:
            accept = (headers.get("Accept", "") or "").lower()
        except AttributeError:
            return False
        return "text/html" in accept

    def canonicalize_path(self, handler) -> None:
        raw = getattr(handler, "path", None)
        if not isinstance(raw, str):
            return
        qmark = raw.find("?")
        path = raw if qmark < 0 else raw[:qmark]
        query = "" if qmark < 0 else raw[qmark:]
        if (path.startswith("/api/") and len(path) > len("/api/")
                and path.endswith("/")):
            handler.path = path.rstrip("/") + query

    def check_body_size(self, handler) -> bool:
        headers = getattr(handler, "headers", None)
        if headers is None:
            return True  # tests with mocked handlers skip the cap
        try:
            raw = headers.get(_H_CONTENT_LENGTH, "") or ""
        except AttributeError:
            return True
        try:
            length = int(raw) if raw else 0
        except ValueError:
            length = 0
        if length <= _MAX_BODY_BYTES:
            return True
        body = json.dumps({
            "error": f"request body too large ({length} bytes); "
                     f"max {_MAX_BODY_BYTES}",
        }).encode()
        handler.send_response(HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
        handler.send_header("Content-Type", "application/json")
        handler.send_header(_H_CONTENT_LENGTH, str(len(body)))
        self.emit_security_headers(handler)
        handler.end_headers()
        handler.wfile.write(body)
        return False

    def emit_security_headers(self, handler) -> None:
        """Send hardening headers on every response.

        Delegates to ``core.auth.security_headers.apply_policy`` so the
        full set — CSP, HSTS, COOP, CORP, Cache-Control, Permissions-
        Policy, X-Frame, X-CTO, Referrer-Policy, Server-banner scrub —
        is emitted from a single canonical preset. The legacy preset
        preserves the Envoy same-origin Referrer behaviour documented
        at this method's historical location and also flips
        Cache-Control to ``no-store`` so auth-gated responses never
        land in a shared cache.
        """
        _apply_security_policy(handler, _LEGACY_DASHBOARD_POLICY)


_auth_policy = _AuthPolicy()


class _ControllerRBAC:
    """Per-user authorization check on top of authentication.

    Even when auth passes (basic, bearer, or trusted-proxy), the
    authenticated identity's role is consulted. If the role's
    ``controller_admin`` flag is False, POST/PUT/DELETE mutations are
    refused with 403; GETs pass so read-only users can still load the
    dashboard and see their own profile.

    Fallback: if no local user matches the authenticated username (for
    example, the env-var STACK_ADMIN fallback or a brand-new user who
    isn't yet reconciled), the request is treated as admin — otherwise
    we'd lock the admin out on day zero before any user store exists.
    """

    _MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

    def allows(self, handler) -> bool:
        command = getattr(handler, "command", "GET")
        if command not in self._MUTATING_METHODS:
            return True  # reads aren't gated by controller_admin
        identity = self._identity_of(handler)
        if not identity:
            return True  # no identity resolved (no auth context) — upstream already gated
        role = self._role_for_identity(identity)
        if role is None:
            return True  # unknown user — assume admin rather than lock out
        return bool(getattr(role, "controller_admin", True))

    def _identity_of(self, handler) -> str:
        remote = _trusted_proxy_auth.identity(handler)
        if remote:
            return remote
        auth = (handler.headers.get(_H_AUTHORIZATION, "") if
                getattr(handler, "headers", None) else "") or ""
        if auth.startswith("Basic "):
            try:
                decoded = base64.b64decode(auth[6:]).decode(
                    "utf-8", "replace")
                return decoded.partition(":")[0]
            except Exception as exc:  # noqa: BLE001
                logging.getLogger("media_stack").debug(
                    "[DEBUG] _identity_of Basic decode: %s", exc,
                )
                return ""
        if auth.startswith("Bearer "):
            try:
                tok = _build_token_store().verify(
                    auth[len("Bearer "):].strip(),
                ) if _build_token_store else None
                return tok.owner_username if tok else ""
            except Exception as exc:  # noqa: BLE001
                logging.getLogger("media_stack").debug(
                    "[DEBUG] _identity_of Bearer verify: %s", exc,
                )
                return ""
        return ""

    def _role_for_identity(self, username: str):
        if _build_user_service is None:
            return None
        try:
            svc = _build_user_service()
            user = svc._store.get_by_username(username)
            if user is None:
                return None
            return svc._roles.get(user.role_slug)
        except Exception as exc:  # noqa: BLE001
            logging.getLogger("media_stack").debug(
                "[DEBUG] role lookup for %s: %s", username, exc,
            )
            return None


_controller_rbac = _ControllerRBAC()


class _SudoGate:
    """Re-authentication gate for high-risk endpoints.

    Certain POSTs are dangerous enough that a session cookie or bearer
    token alone isn't acceptable \u2014 we want proof the human at the
    keyboard still holds the password. On a matching path, we require
    an ``X-Sudo-Password`` header whose value verifies against the
    authenticated user's password (via BasicAuthVerifier). If the
    request already uses Basic auth, the password is on every request
    anyway \u2014 we skip the re-check to avoid double-prompting.

    Default sensitive paths:
      /api/rotate-keys                 rotate admin API keys
      /api/reset-password              reset service-admin passwords
      /api/auth/config                 auth mode change
      /api/users/*/reset-password      per-user password reset
      /api/users/*/delete              user deletion (_method=DELETE)
      /api/tokens/revoke-family        burn refresh-token chain
      /api/envvars                     env var edit (secrets lurk here)

    Opt out or extend via CONTROLLER_SUDO_EXTRA_PATHS (CSV).
    """

    _DEFAULT_SUDO_PATHS = frozenset({
        "/api/rotate-keys",
        "/api/reset-password",
        "/api/auth/config",
        "/api/envvars",
        "/api/tokens/revoke-family",
        "/api/tls/certificate",
        "/api/tls/certificate/regenerate",
        # Weakening the password policy is a privilege-escalation
        # vector (admin could lower min_length to 4 then create a
        # trivially-guessable account). Require re-auth.
        "/api/password-policy",
    })
    _DEFAULT_SUDO_PREFIXES = (
        "/api/users/",  # matches /api/users/{id}/reset-password etc.
    )

    def __init__(self) -> None:
        self._env = os.environ

    def requires_sudo(self, handler, path: str) -> bool:
        if getattr(handler, "command", "") != "POST":
            return False
        if path in self._DEFAULT_SUDO_PATHS:
            return True
        # Only the destructive per-user endpoints require sudo, not
        # list/create; match /api/users/{id}/{action} where action is
        # in a small allowlist.
        parts = path.split("/")
        if (len(parts) >= 5 and parts[1] == "api" and parts[2] == "users"
                and parts[4] in ("reset-password", "delete", "role",
                                 "revoke-sessions")):
            return True
        extra = (self._env.get("CONTROLLER_SUDO_EXTRA_PATHS", "") or "")
        for chunk in extra.split(","):
            if chunk.strip() and path == chunk.strip():
                return True
        return False

    def allows(self, handler, path: str) -> bool:
        if not self.requires_sudo(handler, path):
            return True
        # If no admin password is configured, the whole system is in
        # "no-auth" mode — there's nothing to re-verify against, so
        # the sudo gate becomes a no-op. Matches _check_auth()'s
        # behaviour in that mode.
        if not self._env.get("STACK_ADMIN_PASSWORD", ""):
            return True
        # If the request already uses Basic auth, the password is
        # validated by _check_auth() on every call — continuous
        # re-auth, no need for an extra X-Sudo-Password header.
        auth_hdr = ""
        try:
            auth_hdr = handler.headers.get(_H_AUTHORIZATION, "") or ""
        except AttributeError:
            logging.getLogger("media_stack").debug("[DEBUG] Swallowed exception", exc_info=True)
        if auth_hdr.startswith("Basic "):
            return True
        # Bearer / cookie / trusted-proxy \u2014 require the extra header.
        sudo_pw = ""
        try:
            sudo_pw = (handler.headers.get("X-Sudo-Password", "") or "").strip()
        except AttributeError:
            logging.getLogger("media_stack").debug("[DEBUG] Swallowed exception", exc_info=True)
        if not sudo_pw:
            return False
        # Re-auth can succeed under either:
        #   (a) password matches the identity the request is acting as
        #       (bearer owner / Remote-User / cookie owner), OR
        #   (b) password matches the STACK_ADMIN account (root override).
        # (a) catches the common case "I'm logged in as alice and I
        # want to delete bob's session"; (b) is a break-glass so an
        # admin with a bearer under a non-human label ("ci-runner")
        # can still escalate.
        candidates: list[str] = []
        identity = _controller_rbac._identity_of(handler)
        if identity:
            candidates.append(identity)
        admin = self._env.get("STACK_ADMIN_USERNAME", "admin")
        if admin and admin not in candidates:
            candidates.append(admin)
        for user in candidates:
            if self._credential_matches(user, sudo_pw):
                return True
        return False

    def _credential_matches(self, username: str, password: str) -> bool:
        if _build_auth_verifier is None:
            fb = self._env.get("STACK_ADMIN_PASSWORD", "")
            return bool(fb) and password == fb
        try:
            return bool(_build_auth_verifier().verify(username, password))
        except Exception as exc:  # noqa: BLE001
            logging.getLogger("media_stack").debug(
                "[DEBUG] sudo verify raised: %s", exc,
            )
            return False


_sudo_gate = _SudoGate()


def _start_auto_heal_loop() -> None:
    """Spawn a daemon thread that runs the auto-heal cycle on an
    interval. The default is 60s — fast enough to catch crashloops
    before the user notices, infrequent enough that the disk reads
    don't show up in iostat. Override with
    ``CONTROLLER_AUTO_HEAL_INTERVAL_SECONDS``."""
    try:
        interval = max(15, int(os.environ.get(
            "CONTROLLER_AUTO_HEAL_INTERVAL_SECONDS", "60",
        )))
    except ValueError:
        interval = 60

    def _loop() -> None:
        # Lazy import so a broken auto-heal module doesn't take the
        # whole server down on boot.
        from .services import auto_heal as autoheal_svc
        while True:
            try:
                autoheal_svc.run_cycle()
            except Exception as exc:  # noqa: BLE001
                logging.getLogger("media_stack").debug(
                    "[DEBUG] auto-heal cycle raised: %s", exc,
                )
            time.sleep(interval)

    threading.Thread(
        target=_loop, daemon=True, name="auto-heal-loop",
    ).start()


def _audit_actor_from(handler) -> str:
    """Best-effort actor identity for audit entries.

    Tries (in order):
      - Authelia Remote-User forwarded by a trusted proxy
      - The username half of a Basic auth header
      - 'bearer-token' when a Bearer header was presented
      - 'anonymous' as the final fallback
    """
    try:
        remote = _trusted_proxy_auth.identity(handler)
        if remote:
            return remote
    except Exception as exc:  # noqa: BLE001
        logging.getLogger("media_stack").debug(
            "[DEBUG] _audit_actor_from trusted_proxy_auth raised: %s", exc,
        )
    auth = (handler.headers.get(_H_AUTHORIZATION, "") if
            getattr(handler, "headers", None) else "") or ""
    if auth.startswith("Basic "):
        try:
            decoded = base64.b64decode(auth[6:]).decode("utf-8", "replace")
            return decoded.partition(":")[0] or "anonymous"
        except Exception as exc:  # noqa: BLE001
            logging.getLogger("media_stack").debug(
                "[DEBUG] _audit_actor_from Basic decode failed: %s", exc,
            )
            return "anonymous"
    if auth.startswith("Bearer "):
        return "bearer-token"
    return "anonymous"


# Paths whose POST traffic we audit. GET is not audited (reads don't
# change state); the user-mgmt service has its own finer-grained audit
# that runs in addition to this so detail-rich entries still happen.
_AUDIT_SKIP_POST_PATHS = frozenset({
    "/healthz", "/readyz", "/webhooks/arr",
})


def _audit_mutation(handler) -> None:
    """Emit an audit-log entry for a 2xx mutating POST.

    Runs AFTER the handler returns so the logged result includes the
    status the business logic chose (400/404/500 mutations aren't
    audited as successful changes). Falls back silently on any error
    so a misbehaving audit path can't block a real request.
    """
    if _build_user_service is None:
        return
    try:
        status = int(getattr(handler, "_last_status", 0))
        if not (HTTPStatus.OK <= status < HTTPStatus.MULTIPLE_CHOICES):
            return
        path = (getattr(handler, "path", "") or "").split("?")[0]
        if path in _AUDIT_SKIP_POST_PATHS:
            return
        # User-mgmt endpoints write their own audit entries with more
        # detail; skip here to avoid duplicate rows.
        if (path == "/api/users" or path.startswith("/api/users/")
                or path.startswith("/api/invites")
                or path.startswith("/api/roles/")
                or path == "/api/users-bulk-import"
                or path == "/api/users-reconcile/import"
                or path == "/api/users-reconcile/unlink"):
            return
        svc = _build_user_service()
        svc._audit.append(
            actor=_audit_actor_from(handler),
            action="api_mutation",
            target=path,
            result="ok",
            detail={
                "method": getattr(handler, "command", "POST"),
                "status": status,
                "client": _trusted_proxy_auth.client_ip(handler),
            },
        )
    except Exception as exc:  # noqa: BLE001
        logging.getLogger("media_stack").debug(
            "[DEBUG] _audit_mutation failed: %s", exc,
        )


def _verify_basic_auth(auth_header: str, fb_user: str, fb_pass: str) -> bool:
    """Verify basic-auth. Prefer the store-backed verifier so password
    resets in the UI take effect immediately; fall back to env creds.
    """
    if not auth_header.startswith("Basic "):
        return False
    try:
        decoded = base64.b64decode(auth_header[6:]).decode("utf-8")
    except Exception as exc:  # noqa: BLE001
        logging.getLogger("media_stack").debug("[DEBUG] bad auth header: %s", exc)
        return False
    provided_user, _, provided_pass = decoded.partition(":")
    if _build_auth_verifier is not None:
        try:
            if _build_auth_verifier().verify(provided_user, provided_pass):
                return True
        except Exception as exc:  # noqa: BLE001
            logging.getLogger("media_stack").debug(
                "[DEBUG] store-backed verifier failed: %s", exc,
            )
    return provided_user == fb_user and provided_pass == fb_pass

# Re-export for backward compatibility — other modules import these from server.py
from .webhooks import _fire_webhooks  # noqa: F401
from .cache import api_cache as _api_cache  # noqa: F401
from .handlers_get import _build_openapi_servers  # noqa: F401

logger = logging.getLogger("controller_api")

ActionTriggerFn = Callable[[str, dict[str, Any]], None]


# Re-export constants that other modules import from server.py
KNOWN_ACTIONS = handlers_post.KNOWN_ACTIONS

# Lower number = higher priority. Used by PriorityQueue in the dispatch loop.
# Core action priorities (configure-* jobs get priority from their contract).
_CORE_ACTION_PRIORITY: dict[str, int] = {
    "bootstrap":     10,
    "configure-media-server": 15,
    "validate-credentials": 20,
    "envoy-config":  30,
    "restart-apps":  40,
    "post-setup":      45,
    "reconcile":     50,
    "push-indexers": 60,
    "discover-indexers": 70,
}

def _build_action_priority() -> dict[str, int]:
    """Build ACTION_PRIORITY from core + contract-discovered jobs."""
    priorities = dict(_CORE_ACTION_PRIORITY)
    try:
        from media_stack.services.jobs.framework import discover_jobs_from_contracts
        _PHASE_BASE = {"media_server": 40, "download_clients": 50, "default": 55, "post": 75}
        for job in discover_jobs_from_contracts():
            base = _PHASE_BASE.get(job["phase"], 55)
            priorities.setdefault(job["name"], base + job.get("priority", 50) // 10)
    except Exception as exc:
        log_swallowed(exc)
    return priorities

ACTION_PRIORITY: dict[str, int] = _build_action_priority()
DEFAULT_ACTION_PRIORITY = 50


# ---------------------------------------------------------------------------
# Auth + known actions
# ---------------------------------------------------------------------------

_AUTH_REQUIRED_PATHS = frozenset({
    "/api/rotate-keys", "/api/reset-password", "/api/routing",
    "/api/batch-restart", "/api/profile", "/api/envvars",
    "/api/guardrails", "/webhooks/test", "/config", "/cancel",
})
_AUTH_REQUIRED_PREFIXES = ("/actions/", "/api/restart/", "/api/stack/")


# ---------------------------------------------------------------------------
# Request Handler
# ---------------------------------------------------------------------------

class ControllerAPIHandler(BaseHTTPRequestHandler):
    """HTTP request handler for controller API endpoints."""

    state: ControllerState
    _callbacks: dict[str, Any] = {}

    # BaseHTTPRequestHandler defaults to "BaseHTTP/0.x Python/3.y.z",
    # which leaks the Python version + a recognisable stdlib banner
    # for fingerprinting attackers. Override to a static opaque string.
    # We use "media-stack" (NOT an empty value) because several proxies
    # (notably older nginx + Envoy access-log parsers) log a warning
    # and synthesise a placeholder when they see a blank Server header,
    # which is worse than a static label for triage noise.
    # ``server_version`` is what BaseHTTPRequestHandler uses to build
    # ``version_string()``; ``sys_version`` is the stdlib-appended
    # "Python/x.y.z" suffix we want gone.
    server_version = "media-stack"
    sys_version = ""

    def version_string(self) -> str:
        # Defensive override: even if a subclass or monkey-patch twiddles
        # server_version / sys_version above, this returns a stable label.
        return "media-stack"

    @property
    def action_trigger(self) -> ActionTriggerFn | None:
        return self._callbacks.get("action_trigger")

    @property
    def reload_config(self) -> Callable[[], None] | None:
        return self._callbacks.get("reload_config")

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        ts = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        logger.debug("[%s] %s %s", ts, self.command, self.path)

    # --- Auth ---

    def _check_auth(self) -> bool:
        """Check authentication (trusted proxy, bearer token, or basic auth).

        Order:
          0. IP-lockout check — an IP that's burned through its failure
             budget is rejected 429 immediately, no auth path tried.
          1. Trusted-proxy — Remote-User from an allowlisted CIDR wins.
          2. Bearer token — programmatic clients.
          3. Basic auth — browser + curl.

        Auth is skipped for public endpoints and when CONTROLLER_AUTH
        resolves to ``none``.
        """
        path = self.path.split("?")[0]
        if _auth_policy.is_public(self, path):
            return True
        username = os.environ.get("STACK_ADMIN_USERNAME", "admin")
        password = os.environ.get("STACK_ADMIN_PASSWORD", "")
        if _auth_policy.decision(self, path, password) == "allow":
            return True
        client_ip = _trusted_proxy_auth.client_ip(self)
        if _should_reject_for_ip_lockout(client_ip):
            security_counters.incr("ip_lockout_trip")
            self.send_response(HTTPStatus.TOO_MANY_REQUESTS)
            self.send_header("Content-Type", "application/json")
            self.send_header(_H_CONTENT_LENGTH, "0")
            self.end_headers()
            return False
        if _trusted_proxy_auth.identity(self):
            return True
        # Session cookie — preferred path for browsers after a POST /api/auth/login.
        if _auth_policy.verify_session_cookie(self):
            return True
        if not password:
            return True
        auth_header = self.headers.get(_H_AUTHORIZATION, "")
        if auth_header.startswith("Bearer "):
            if _auth_policy.verify_bearer(
                self, auth_header[len("Bearer "):].strip(),
            ):
                return True
        elif _verify_basic_auth(auth_header, username, password):
            return True
        if client_ip:
            _ip_failure_tracker.register_failure(client_ip)
        security_counters.incr("auth_fail")
        _auth_policy.send_401(self)
        return False

    # --- Response helpers ---

    def _safe_write(self, payload: bytes) -> None:
        """Write to the response socket, swallowing client-side
        disconnects.

        BrokenPipeError / ConnectionResetError mean the browser has
        already closed the connection — typically because a React
        component unmounted mid-fetch, the user navigated away, or a
        Tanstack Query observer was cancelled. The server has nothing
        useful to do about it; the previous behavior raised the
        exception out to socketserver, which logged a noisy multi-line
        traceback for every cancelled request. Quietly drop the write
        and let the request finish.
        """
        try:
            self.wfile.write(payload)
        except (BrokenPipeError, ConnectionResetError) as exc:
            # Debug-level only — these are normal in any SPA that
            # cancels in-flight requests on route change.
            logger.debug(
                "Client disconnected before response finished: %s %s (%s)",
                self.command,
                self.path,
                exc,
            )

    def _json_response(self, status: int, body: dict[str, Any]) -> None:
        payload = json.dumps(body, default=str).encode("utf-8")
        self._last_status = int(status)
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header(_H_CONTENT_LENGTH, str(len(payload)))
        _auth_policy.emit_security_headers(self)
        _issue_csrf_if_missing(self)
        self.end_headers()
        self._safe_write(payload)

    def _html_response(self, status: int, html: str) -> None:
        payload = html.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header(_H_CONTENT_LENGTH, str(len(payload)))
        _auth_policy.emit_security_headers(self)
        _issue_csrf_if_missing(self)
        self.end_headers()
        self._safe_write(payload)

    def _raw_response(self, status: int, content_type: str, payload: bytes, headers: dict[str, str] | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header(_H_CONTENT_LENGTH, str(len(payload)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        _auth_policy.emit_security_headers(self)
        self.end_headers()
        self._safe_write(payload)

    def _read_json_body(self) -> dict[str, Any]:
        length = int(self.headers.get(_H_CONTENT_LENGTH, 0))
        if length <= 0:
            return {}
        # Defensive: clamp to the cap even if do_POST preflight was bypassed.
        length = min(length, _MAX_BODY_BYTES)
        try:
            return json.loads(self.rfile.read(length))
        except (json.JSONDecodeError, ValueError) as exc:
            logger.warning("Malformed JSON body on %s %s: %s", self.command, self.path, exc)
            return {}

    # --- SSE ---

    def _sse_response(self) -> None:
        """Send Server-Sent Events stream of log lines."""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()

        after_seq = 0
        if "?" in self.path:
            qs = self.path.split("?", 1)[1]
            for part in qs.split("&"):
                if part.startswith("after_seq="):
                    try:
                        after_seq = int(part.split("=", 1)[1])
                    except ValueError:
                        logging.getLogger("media_stack").debug("[DEBUG] Swallowed exception", exc_info=True)

        try:
            while True:
                entries = self.state.get_logs_since(after_seq)
                for seq, ts, msg, action, *_ in entries:
                    ts_str = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(ts))
                    data = json.dumps({"seq": seq, "ts": ts_str, "msg": msg, "action": action})
                    self.wfile.write(f"id: {seq}\ndata: {data}\n\n".encode())
                    after_seq = seq
                self.wfile.flush()
                self.state.wait_for_log(timeout=30.0)
        except (BrokenPipeError, ConnectionResetError, OSError):
            logging.getLogger("media_stack").debug("[DEBUG] Swallowed exception", exc_info=True)

    # --- Action dispatch ---

    def _handle_action(self, action_name: str) -> None:
        body = self._read_json_body()
        overrides = body if body else {}
        # Capture who triggered this action
        auth_header = self.headers.get(_H_AUTHORIZATION, "")
        triggered_by = "system"
        if auth_header.startswith("Basic "):
            try:
                import base64
                decoded = base64.b64decode(auth_header[6:]).decode("utf-8")
                triggered_by = decoded.partition(":")[0] or "user"
            except Exception:
                triggered_by = "user"
        overrides["_triggered_by"] = triggered_by
        # Tag this run as operator-triggered so the
        # ``/api/jobs.history`` ``source`` field reads ``"manual"``
        # rather than ``"unknown"``. The actor's username (parsed
        # from the Basic auth header above) is propagated as
        # ``_actor_username`` so the dashboard can show "ran by
        # alice" alongside the badge. ``_dispatch_action`` strips
        # both fields out before forwarding overrides to
        # ``_apply_overrides`` — they're control-plane metadata,
        # not user-set toggles.
        overrides["_source"] = "manual"
        if triggered_by and triggered_by != "system":
            overrides["_actor_username"] = triggered_by
        if self.action_trigger:
            self.action_trigger(action_name, overrides)
        priority = ACTION_PRIORITY.get(action_name, DEFAULT_ACTION_PRIORITY)
        self._json_response(200, {
            "status": "accepted",
            "action": action_name,
            "priority": priority,
            "overrides": overrides,
        })

    # --- Plugin loader ---

    def _load_plugins(self) -> str:
        """Load custom JS/CSS from config mount.

        Reads from ``<CONFIG_ROOT>/.controller/plugins/`` (the PVC-backed
        state directory). Pre-v1.0.169 this was ``controller/plugins/``
        without the dot prefix — which on k8s landed on the pod's
        ephemeral overlay instead of the PVC, so any plugin an operator
        dropped there vanished at the next pod restart. The legacy
        no-dot location is still honoured as a fallback so operators
        with compose bind-mounts mid-migration don't lose their plugins
        on the day they upgrade."""
        config_root = os.environ.get("CONFIG_ROOT", "/srv-config")
        plugin_dir = Path(config_root) / ".controller" / "plugins"
        if not plugin_dir.is_dir():
            legacy = Path(config_root) / "controller" / "plugins"
            if legacy.is_dir():
                plugin_dir = legacy
            else:
                return ""
        parts: list[str] = []
        for f in sorted(plugin_dir.iterdir()):
            if f.suffix == ".js" and f.is_file():
                parts.append(f"<script>{f.read_text(encoding='utf-8')}</script>")
            elif f.suffix == ".css" and f.is_file():
                parts.append(f"<style>{f.read_text(encoding='utf-8')}</style>")
        return "\n".join(parts)

    # --- Webhook test ---

    def _test_webhook(self) -> dict[str, Any]:
        urls = list(self.state.webhook_urls)
        if not urls:
            return {"status": "no_webhooks", "tested": 0}
        results: dict[str, str] = {}
        data = json.dumps({"event": "test", "status": "ok"}).encode("utf-8")
        for url in urls:
            try:
                req = urllib.request.Request(url, data=data, method="POST", headers={"Content-Type": "application/json"})
                with urllib.request.urlopen(req, timeout=5) as resp:
                    results[url] = f"ok ({resp.status})"
            except Exception as exc:
                results[url] = f"error: {str(exc)[:60]}"
        return {"status": "tested", "results": results, "tested": len(results)}

    # --- OpenAPI spec ---

    def _get_openapi_spec(self) -> dict[str, Any]:
        get_endpoints = [
            ("/healthz", "Liveness probe"),
            ("/readyz", "Readiness probe"),
            ("/status", "Full controller state"),
            ("/apps", "All app statuses"),
            ("/apps/{name}", "Single app status"),
            ("/config", "Runtime configuration"),
            ("/webhooks", "List webhook URLs"),
            ("/logs/stream", "Real-time log stream (SSE)"),
            ("/api/services", "List managed services"),
            ("/api/services/categories", "Service categories"),
            ("/api/services/{id}/api-key", "API key status for a service"),
            ("/api/health", "Live service health probes"),
            ("/api/health-history", "Health history and SLA metrics"),
            ("/api/versions", "Service versions"),
            ("/api/downloads", "Active downloads"),
            ("/api/stats", "Library counts"),
            ("/api/indexers", "Indexer manager entries"),
            ("/api/indexer-stats", "Indexer performance stats"),
            ("/api/download-history", "Recent download history"),
            ("/api/quality-profiles", "Quality profiles"),
            ("/api/import-lists", "Import/discovery lists"),
            ("/api/libraries", "Media server libraries"),
            ("/api/recent", "Recently added items"),
            ("/api/keys", "All API keys and admin credentials"),
            ("/api/disk", "Disk usage + guardrails"),
            ("/api/cleanup-preview", "Guardrail cleanup preview"),
            ("/api/env", "Runtime environment"),
            ("/api/routing", "Routing configuration"),
            ("/api/routing-probe", "Probe all user-facing URLs per service"),
            ("/api/profile", "Bootstrap profile"),
            ("/api/envvars", "Stack environment variables"),
            ("/api/manifests", "Deployment manifests"),
            ("/api/backup", "Config backup download"),
            ("/api/namespaces", "Containers / namespaces"),
            ("/api/image-updates", "Image versions + staleness"),
            ("/api/gpu", "GPU detection for transcoding"),
            ("/api/snapshots", "Config snapshots"),
            ("/api/snapshots/{file}", "Snapshot detail"),
            ("/api/snapshot-diff", "Compare two snapshots"),
            ("/api/mounts", "Filesystem mounts"),
            ("/api/logs/{service}", "Service container logs"),
            ("/metrics", "Prometheus metrics"),
            ("/api/envoy/stats", "Envoy proxy traffic stats"),
            ("/api/feed.xml", "RSS feed"),
            ("/api/grafana.json", "Grafana dashboard JSON"),
            ("/api/openapi.json", "This spec (abridged)"),
            ("/api/openapi.yaml", "Full OpenAPI 3.0.3 spec"),
        ]
        post_endpoints = [
            ("/actions/{name}", "Trigger action"),
            ("/cancel", "Cancel running action"),
            ("/run", "Trigger bootstrap (legacy)"),
            ("/api/services/{id}/api-key", "Set/discover API key"),
            ("/api/rotate-keys", "Rotate all API keys"),
            ("/api/reset-password", "Reset admin password"),
            ("/api/media-server/reset", "Hard-reset media server credentials"),
            ("/api/routing", "Update routing config"),
            ("/api/guardrails", "Update guardrail settings"),
            ("/api/profile", "Save bootstrap profile"),
            ("/api/envvars", "Set environment variable"),
            ("/api/restore", "Restore config from backup"),
            ("/api/batch-restart", "Restart multiple services"),
            ("/api/restart/{service}", "Restart a single service"),
            ("/api/gpu/enable", "Auto-configure GPU transcoding"),
            ("/api/snapshot", "Take a config snapshot"),
            ("/config", "Update runtime config"),
            ("/webhooks", "Register webhook URL"),
            ("/webhooks/test", "Test all webhooks"),
        ]
        paths: dict[str, Any] = {}
        for ep, desc in get_endpoints:
            paths[ep] = {"get": {"summary": desc, "responses": {"200": {"description": "OK"}}}}
        for ep, desc in post_endpoints:
            paths[ep] = {"post": {"summary": desc, "responses": {"200": {"description": "OK"}}}}
        return {
            "openapi": "3.0.3",
            "info": {
                "title": "Media Stack Controller API",
                "version": "1.0.0",
                "description": "Abridged spec. For the full OpenAPI 3.0.3 specification with schemas, examples, and descriptions, see GET /api/openapi.yaml or visit /api/docs.",
            },
            "paths": paths,
        }

    # =======================================================================
    # GET routing — delegates to handlers_get
    # =======================================================================

    def do_GET(self) -> None:  # noqa: N802
        _auth_policy.canonicalize_path(self)
        if not self._check_auth():
            return
        handlers_get.handle(self)

    # =======================================================================
    # POST routing — delegates to handlers_post
    # =======================================================================

    def do_POST(self) -> None:  # noqa: N802
        _auth_policy.canonicalize_path(self)
        if not self._check_auth():
            return
        if not _auth_policy.check_body_size(self):
            return
        if not _controller_rbac.allows(self):
            self._json_response(
                HTTPStatus.FORBIDDEN,
                {"error": "role does not permit controller mutations; "
                          "controller_admin=false"},
            )
            return
        sudo_path = self.path.split("?")[0]
        if not _sudo_gate.allows(self, sudo_path):
            security_counters.incr("sudo_fail")
            self._json_response(
                HTTPStatus.FORBIDDEN,
                {"error": "sensitive endpoint requires re-authentication; "
                          "present X-Sudo-Password header with your password"},
            )
            return
        handlers_post.handle(self)
        _audit_mutation(self)


# ---------------------------------------------------------------------------
# Server lifecycle
# ---------------------------------------------------------------------------

def start_api_server(
    state: ControllerState,
    port: int = 9100,
    action_trigger: ActionTriggerFn | None = None,
    reload_config: Callable[[], None] | None = None,
) -> ThreadingHTTPServer:
    """Start the API server in a background thread."""
    ControllerAPIHandler.state = state
    # Store callables in a dict to avoid Python's descriptor protocol
    # binding them to self when accessed as class attributes.
    ControllerAPIHandler._callbacks = {
        "action_trigger": action_trigger,
        "reload_config": reload_config,
    }

    server = ThreadingHTTPServer(("0.0.0.0", port), ControllerAPIHandler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True, name="api-server")
    thread.start()

    if _build_sched_reconciler is not None:
        try:
            _build_sched_reconciler().start()
        except Exception as exc:  # noqa: BLE001
            logging.getLogger("media_stack").debug(
                "[DEBUG] scheduled reconcile not started: %s", exc,
            )

    if _build_audit_verifier is not None:
        try:
            _build_audit_verifier().start()
        except Exception as exc:  # noqa: BLE001
            logging.getLogger("media_stack").debug(
                "[DEBUG] audit-chain verifier not started: %s", exc,
            )

    # Auto-heal loop: snapshot healthy configs, restore corrupt ones,
    # restart pods. Disabled with CONTROLLER_AUTO_HEAL_ENABLED=false.
    _start_auto_heal_loop()

    # Pre-warm the argon2 backend, audit-chain hash cache, user
    # service singleton — anything heavy enough to make the FIRST
    # password rotation feel slow. Runs in a daemon thread so a
    # cold disk doesn't gate /healthz returning ok.
    try:
        from .services import prewarm as _prewarm_svc
        _prewarm_svc.run_in_background()
    except Exception as exc:  # noqa: BLE001
        logging.getLogger("media_stack").debug(
            "[DEBUG] pre-warm not started: %s", exc,
        )

    # Graceful shutdown on SIGTERM
    def _shutdown(signum: int, frame: Any) -> None:
        server.shutdown()

    signal.signal(signal.SIGTERM, _shutdown)
    return server
