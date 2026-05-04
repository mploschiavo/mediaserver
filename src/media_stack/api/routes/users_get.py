"""Users-domain GET routes (ADR-0007 Phase 2 wave 5).

Migrates the eleven user-management GET surfaces off the legacy
``handlers_get.GetRequestHandler.handle()`` chain that previously
dispatched through ``_handle_user_mgmt`` -> ``_UserMgmtGetHelper``:

  * ``GET /api/users``                       — list users
  * ``GET /api/users/{user_id}``             — single-user detail
  * ``GET /api/me``                          — caller's own record
  * ``GET /api/users-reconcile``             — provider reconcile diffs
  * ``GET /api/invites``                     — pending invites
  * ``GET /api/tokens``                      — API tokens
  * ``GET /api/roles``                       — role catalog
  * ``GET /api/user-providers``              — provider health
  * ``GET /api/audit-log``                   — recent audit entries
  * ``GET /api/audit-log/stats``             — retention stats
  * ``GET /api/users/{user_id}/login-history`` — per-user login history
                                                (rate-limited via the
                                                security-read bucket)

Implementation patterns (named per the project's "use named design
patterns where they fit" rule):

* **Repository** — ``UserRepository`` wraps the ``UserService``,
  ``InviteService``, and ``ApiTokenStore`` triplet behind a single
  collaborator. Routes never reach across the wire to a private
  attribute (e.g. the legacy ``svc._audit.stats()`` reach is now
  ``UserRepository.audit_stats()``).
* **Strategy + Chain of Responsibility** — ``MeIdentityResolver``
  walks ``session-cookie → trusted-proxy → basic-auth`` in the same
  order the legacy chain grew over time. Each strategy is a tiny
  method on the resolver; constructor-injected so tests swap them
  without monkeypatching module imports.
* **Adapter** — ``LoginHistoryRateLimitAdapter`` adapts the shared
  ``RateLimiter`` to the route's "permit / 429" predicate, so the
  route body reads as one branch instead of three. Default
  construction keeps the legacy bucket-name + per-IP key intact;
  tests override the limiter with an "always-deny" stub to exercise
  the 429 path without timing.

Design: the route module never caches a resolved factory reference
at module level. Constructor-injected dependencies are stored as
private attributes; defaulted dependencies are resolved by FRESH
attribute lookup against the imported module each call so
``unittest.mock.patch`` flips them per-test (the lazy-cache anti-
pattern that bit ADR-0007 wave 3+4).

CSRF: every route here is GET, so the controller's CSRF double-
submit middleware is bypassed by design — the X-CSRF-Token
requirement only fires on mutations. Auth gating is performed by
the controller's ``_check_auth`` middleware ahead of dispatch; this
module does not re-implement auth, only the security-read rate-
limit on the login-history surface (per the legacy contract).
"""

from __future__ import annotations

import base64
import binascii
import logging
import os
from http import HTTPStatus
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

from media_stack.api.routing import RouteModule, get
from media_stack.api.services import (
    security_get_handlers as _security_get_handlers_module,
)
from media_stack.api.session_singletons import (
    session_cookie_reader,
    trusted_proxy_auth,
)
from media_stack.core.auth import rate_limiter as _rate_limiter_module
from media_stack.core.auth.users import (
    user_service_factory as _user_service_factory_module,
)
from media_stack.core.logging_utils import log_swallowed


_log = logging.getLogger("media_stack")


# Cap on the ``error`` detail string when the audit-log stats read
# raises. Matches the ``_ERR_LEN`` convention the rest of the
# migrated route modules use.
_ERR_STATS_LEN = 200

# Default ``limit`` for ``/api/audit-log`` — the legacy chain
# parsed ``?limit=`` and fell back to 100 entries. Pulled out as a
# constant so the magic number doesn't show up in the body and the
# ratchet's "magic int > 100" rule has nothing to flag.
_AUDIT_LOG_DEFAULT_LIMIT = 100

# Security-read bucket parameters. These mirror the per-process
# bucket the legacy ``handlers_get`` module configures — admin-read
# endpoints share one credit line so an attacker enumerating per-
# user login-history can't slip past via burst.
_SECURITY_READ_BUCKET_CAPACITY = 60
_SECURITY_READ_REFILL_PER_SECOND = 5.0
_SECURITY_READ_BUCKET_NAME = "security-read"

# Suppress the bootstrap-credential rotation gate when the operator
# has explicitly opted out (e.g. fresh-install testing rigs).
_SKIP_FORCED_ROTATION_ENV = "STACK_ADMIN_SKIP_FORCED_ROTATION"
_TRUTHY = ("1", "true", "yes", "on")
_BOOTSTRAP_SOURCES = ("env-seed", "env-legacy")


class UserRepository:
    """Repository: collapses the three user-management collaborators
    (``UserService``, ``InviteService``, ``ApiTokenStore``) and the
    audit-log infrastructure behind one dependency-inverted surface.

    Tests construct the routes with a stub repository so each method
    becomes a one-line return — no monkeypatching of the user-service
    factory module needed. Production wiring uses the default
    factories.

    The factory references are resolved by FRESH attribute lookup
    against the imported module on every call, NOT cached on the
    instance. This is intentional: caching the pre-patch reference
    breaks ``mock.patch("media_stack.core.auth.users.user_service_"
    "factory.build_default_service", …)`` in unit tests, which was
    the wave-3+4 lazy-cache anti-pattern.
    """

    def list_users(self) -> list[dict[str, Any]]:
        return self._service().list_users()

    def list_users_with_deleted(self) -> list[dict[str, Any]]:
        return self._service().list_users(include_deleted=True)

    def list_roles(self) -> list[dict[str, Any]]:
        return self._service().list_roles()

    def provider_health(self) -> list[dict[str, Any]]:
        return self._service().provider_health()

    def reconcile_report(self) -> list[dict[str, Any]]:
        return self._service().reconcile_report()

    def list_invites(self) -> list[dict[str, Any]]:
        return self._invites().list_pending()

    def list_tokens(self) -> list[dict[str, Any]]:
        return [t.to_dict() for t in self._tokens().list_all()]

    def audit_recent(
        self, *, limit: int, action_filter: str,
    ) -> list[dict[str, Any]]:
        return self._service().audit_recent(
            limit=limit, action_filter=action_filter,
        )

    def audit_stats(self) -> dict[str, Any]:
        return self._service()._audit.stats()

    def user_detail(self, user_id: str) -> dict[str, Any] | None:
        return self._service().user_detail(user_id)

    # --- internals: fresh attribute lookups --------------------------

    def _service(self) -> Any:
        return _user_service_factory_module.build_default_service()

    def _invites(self) -> Any:
        return _user_service_factory_module.build_default_invite_service()

    def _tokens(self) -> Any:
        return _user_service_factory_module.build_default_api_token_store()


class _SessionCookieIdentity:
    """First identity strategy: read the controller's session cookie.

    Mirrors the legacy ``_build_me_response`` order — the session
    cookie is checked before any forwarded SSO header so an operator
    who logged in via the controller's in-page form keeps a stable
    identity even when an SSO proxy is also setting headers.
    """

    def __init__(self, reader: Any = session_cookie_reader) -> None:
        self._reader = reader

    def resolve(self, handler: Any) -> str:
        return self._reader.username_for_handler(handler) or ""


class _TrustedProxyIdentity:
    """Second identity strategy: trusted-proxy ``Remote-User``.

    The trusted-proxy reader gates on
    ``CONTROLLER_TRUSTED_PROXY_CIDRS`` so a request not arriving
    from Envoy can't forge ``Remote-User``. Returns empty string
    when the proxy didn't authenticate the caller upstream.
    """

    def __init__(self, reader: Any = trusted_proxy_auth) -> None:
        self._reader = reader

    def resolve(self, handler: Any) -> str:
        return self._reader.identity(handler) or ""


class _BasicAuthIdentity:
    """Third identity strategy: HTTP Basic credentials.

    Returns just the username; the password validation is the
    controller-level auth middleware's job. Decoding failures
    (``binascii.Error`` / ``ValueError``) are routed through
    ``log_swallowed`` so a malformed header never silently drops a
    real authenticated identity.
    """

    def resolve(self, headers: Any) -> str:
        auth = headers.get("Authorization", "") or ""
        if not auth.startswith("Basic "):
            return ""
        try:
            decoded = base64.b64decode(auth[6:]).decode("utf-8", "replace")
        except (binascii.Error, ValueError) as exc:
            log_swallowed(exc, context="users_get/basic-auth-decode")
            return ""
        return decoded.partition(":")[0] or ""


class MeIdentityResolver:
    """Strategy + Chain-of-Responsibility coordinator for ``/api/me``.

    Walks session-cookie -> trusted-proxy -> basic-auth in order,
    stopping at the first strategy that yields a username. When no
    strategy fires, the response shape is ``{authenticated: False}``
    (matches the legacy contract the dashboard binds against).
    """

    def __init__(
        self,
        *,
        cookie: _SessionCookieIdentity | None = None,
        proxy: _TrustedProxyIdentity | None = None,
        basic: _BasicAuthIdentity | None = None,
    ) -> None:
        self._cookie = cookie or _SessionCookieIdentity()
        self._proxy = proxy or _TrustedProxyIdentity()
        self._basic = basic or _BasicAuthIdentity()

    def resolve(self, handler: Any) -> str:
        username = self._cookie.resolve(handler)
        if username:
            return username
        username = self._proxy.resolve(handler)
        if username:
            return username
        return self._basic.resolve(handler.headers)


class ForcedRotationGate:
    """Resolves whether a bootstrap-credential admin still has to
    rotate their password.

    Pulled out as a standalone class so the env-var read lives in
    one constructor-injected service instead of inline on the
    builder. Tests pass a stub gate; production passes nothing
    and the default reader walks the process environment.

    The env-read happens in the constructor (read-once at
    instantiation), not on every call: the builder is rebuilt per
    route module instance, and the env doesn't change at runtime.
    """

    def __init__(self, *, env_skip_value: str | None = None) -> None:
        if env_skip_value is None:
            # Single-hop accessor (``os.getenv``) — the codebase
            # ratchets prefer routing through the central config
            # helper, but this gate is read once at construction
            # and only flips a UI-only bootstrap-credential
            # rotation modal; we keep it inline here so the route
            # has zero indirection. Tests inject ``env_skip_value``
            # to drive both branches deterministically.
            env_skip_value = os.getenv(_SKIP_FORCED_ROTATION_ENV, "")
        self._skip = env_skip_value.strip().lower()

    def needs_rotation_for(self, source: str) -> bool:
        if source.lower() not in _BOOTSTRAP_SOURCES:
            return False
        return self._skip not in _TRUTHY


class _MeRecordBuilder:
    """Builds the ``/api/me`` response envelope from a resolved
    username + the ``UserRepository.list_users()`` snapshot.

    Kept separate from ``MeIdentityResolver`` so identity resolution
    and record hydration each have one reason to change. The
    ``needs_rotation`` flag is computed via the constructor-
    injected ``ForcedRotationGate`` because it's a UI-only gate —
    flipping the env knob suppresses it without rebuilding the
    user record on disk.
    """

    def __init__(
        self, *, rotation_gate: ForcedRotationGate | None = None,
    ) -> None:
        self._rotation_gate = rotation_gate or ForcedRotationGate()

    def build(
        self,
        username: str,
        users: list[dict[str, Any]],
    ) -> dict[str, Any]:
        if not username:
            return {"authenticated": False}
        detail: dict[str, Any] = {
            "authenticated": True, "username": username,
        }
        for u in users:
            if u.get("username", "").lower() != username.lower():
                continue
            source = str(u.get("source", "") or "")
            detail.update({
                "id": u["id"],
                "email": u["email"],
                "display_name": u["display_name"],
                "role_slug": u["role_slug"],
                "last_login_at": u.get("last_login_at", ""),
                "source": source,
                "needs_rotation":
                    self._rotation_gate.needs_rotation_for(source),
            })
            break
        return detail


class LoginHistoryRateLimitAdapter:
    """Adapter: turns the per-process ``RateLimiter`` into the
    route's "permit / 429" predicate.

    Keeps the legacy bucket-name and per-IP keying so production
    behaviour is preserved 1:1. Constructor-injected limiter +
    client-id resolver so tests deny without timing or monkey-
    patching the global limiter (the wave-3 anti-pattern).

    Construction-time semantics: when the caller passes
    ``limiter=None``, the adapter builds ONE ``RateLimiter`` and
    keeps it for the lifetime of the adapter — token-bucket state
    has to persist across requests for the cap to be meaningful.
    Tests inject an "always-deny" stub via the ``limiter`` kwarg
    so the 429 path is exercised deterministically; we never cache
    a build-time-resolved factory reference (the wave-3 anti-
    pattern that broke ``mock.patch`` against the limiter class).
    """

    def __init__(
        self,
        *,
        limiter: Any = None,
        client_id_resolver: Callable[[Any], str] | None = None,
    ) -> None:
        if limiter is None:
            # Fresh attribute read on construction — keeps the
            # module-level patch surface live for tests that swap
            # the ``RateLimiter`` class before instantiating the
            # routes. After construction we hold the bucket-state
            # holder, NOT a class reference.
            limiter = _rate_limiter_module.RateLimiter(
                capacity=_SECURITY_READ_BUCKET_CAPACITY,
                refill_per_second=_SECURITY_READ_REFILL_PER_SECOND,
            )
        self._limiter = limiter
        self._client_id_resolver = (
            client_id_resolver
            or (lambda h: trusted_proxy_auth.client_ip(h) or "-")
        )

    def allow(self, handler: Any) -> bool:
        client_id = self._client_id_resolver(handler) or "-"
        return self._limiter.allow(
            client_id=client_id, bucket=_SECURITY_READ_BUCKET_NAME,
        )


class UsersGetRoutes(RouteModule):
    """Eleven user-management GET routes — list users, single user,
    /api/me, reconcile diffs, invites, tokens, roles, providers,
    audit log + stats, and per-user login history.

    Constructor-inject ``UserRepository``, ``MeIdentityResolver``,
    ``_MeRecordBuilder``, and ``LoginHistoryRateLimitAdapter`` so
    tests swap each one independently. Production passes nothing —
    defaults materialize the production wiring.
    """

    def __init__(
        self,
        *,
        repository: UserRepository | None = None,
        identity_resolver: MeIdentityResolver | None = None,
        me_record_builder: _MeRecordBuilder | None = None,
        login_history_limiter: LoginHistoryRateLimitAdapter | None = None,
        login_history_helper: Any = None,
    ) -> None:
        self._repo = repository or UserRepository()
        self._identity_resolver = (
            identity_resolver or MeIdentityResolver()
        )
        self._me_builder = me_record_builder or _MeRecordBuilder()
        self._login_history_limiter = (
            login_history_limiter or LoginHistoryRateLimitAdapter()
        )
        # Constructor-injected for tests; production resolves a fresh
        # helper per request via ``_resolve_login_history_helper`` so
        # ``mock.patch`` against the security_get_handlers module wins.
        self._login_history_helper = login_history_helper

    # --- collection routes ------------------------------------------------

    @get("/api/users")
    def handle_list_users(self, handler: Any) -> None:
        """List the controller's user records (excludes soft-
        deleted entries; ``include_deleted=True`` is the metrics-
        only path)."""
        handler._json_response(
            HTTPStatus.OK, {"users": self._repo.list_users()},
        )

    @get("/api/roles")
    def handle_list_roles(self, handler: Any) -> None:
        """Role catalog — drives the role-picker on the user
        edit/create forms."""
        handler._json_response(
            HTTPStatus.OK, {"roles": self._repo.list_roles()},
        )

    @get("/api/user-providers")
    def handle_list_user_providers(self, handler: Any) -> None:
        """Health snapshot of every configured user provider
        (Authelia, Jellyfin, Jellyseerr, …) — drives the providers
        tile on the user-management page."""
        handler._json_response(
            HTTPStatus.OK, {"providers": self._repo.provider_health()},
        )

    @get("/api/users-reconcile")
    def handle_users_reconcile(self, handler: Any) -> None:
        """Diff between the controller user-store and each external
        provider — orphans (in provider, not in store) + ghosts
        (linked in store, not in provider)."""
        handler._json_response(
            HTTPStatus.OK, {"diffs": self._repo.reconcile_report()},
        )

    @get("/api/invites")
    def handle_list_invites(self, handler: Any) -> None:
        """Pending invitation tokens (un-redeemed, non-expired)."""
        handler._json_response(
            HTTPStatus.OK, {"invites": self._repo.list_invites()},
        )

    @get("/api/tokens")
    def handle_list_tokens(self, handler: Any) -> None:
        """All API tokens (admin surface) — short-lived access +
        refresh tokens, plus long-lived service tokens. Raw token
        values never leak through this surface; ``to_dict``
        scrubs them on the model side."""
        handler._json_response(
            HTTPStatus.OK, {"tokens": self._repo.list_tokens()},
        )

    @get("/api/me")
    def handle_me(self, handler: Any) -> None:
        """Authenticated caller's own user record.

        Identity is resolved through ``MeIdentityResolver`` (session
        cookie -> trusted-proxy -> Basic auth). Anonymous callers
        get ``{authenticated: False}``; authenticated callers get
        the full record + a ``needs_rotation`` flag that gates the
        bootstrap-credential rotation modal.
        """
        username = self._identity_resolver.resolve(handler)
        users = self._repo.list_users() if username else []
        handler._json_response(
            HTTPStatus.OK, self._me_builder.build(username, users),
        )

    @get("/api/audit-log")
    def handle_audit_log(self, handler: Any) -> None:
        """Recent audit-log entries — limit + action filter via
        query string. Hash-chain integrity is not re-verified here;
        the dedicated ``/api/audit-log/verify`` route runs that
        check on demand."""
        qs = parse_qs(urlparse(handler.path).query)
        limit_raw = qs.get("limit", [str(_AUDIT_LOG_DEFAULT_LIMIT)])[0]
        try:
            limit = int(limit_raw)
        except ValueError as exc:
            log_swallowed(exc, context="users_get/audit-log/limit")
            limit = _AUDIT_LOG_DEFAULT_LIMIT
        action_filter = qs.get("action", [""])[0]
        handler._json_response(HTTPStatus.OK, {
            "entries": self._repo.audit_recent(
                limit=limit, action_filter=action_filter,
            ),
        })

    @get("/api/audit-log/stats")
    def handle_audit_log_stats(self, handler: Any) -> None:
        """Audit-log retention stats — entry count, disk bytes,
        oldest/newest timestamps, archive count, rotation policy.

        OSError (filesystem read failure) is surfaced as a 500
        envelope with a short error string + zeroed counts for the
        UI's defensive bind. Anything other than ``OSError`` /
        ``ValueError`` propagates so the dispatcher's 500 handler
        records it — silent ``Exception`` swallow on a
        ``RuntimeError`` was the legacy footgun.
        """
        try:
            stats = self._repo.audit_stats()
        except (OSError, ValueError) as exc:
            log_swallowed(exc, context="users_get/audit-log/stats")
            handler._json_response(HTTPStatus.INTERNAL_SERVER_ERROR, {
                "error": str(exc)[:_ERR_STATS_LEN],
                "entry_count": 0,
                "disk_bytes": 0,
            })
            return
        handler._json_response(HTTPStatus.OK, stats)

    # --- parameterized routes --------------------------------------------

    @get("/api/users/{user_id}")
    def handle_user_detail(
        self, handler: Any, *, user_id: str,
    ) -> None:
        """Single-user record, by id."""
        user = self._repo.user_detail(user_id)
        if user is None:
            handler._json_response(
                HTTPStatus.NOT_FOUND,
                {"error": f"user {user_id} not found"},
            )
            return
        handler._json_response(HTTPStatus.OK, user)

    @get("/api/users/{user_id}/login-history")
    def handle_user_login_history(
        self, handler: Any, *, user_id: str,
    ) -> None:
        """Per-user login history (first-seen IPs, impossible-
        travel signal). Rate-limited via the ``security-read``
        bucket — admin-read paths share one credit line so an
        attacker enumerating per-user history can't slip past
        via burst.

        After the rate-limit gate, dispatch is delegated to the
        existing ``_SessionVisibilityGetHelper`` whose
        ``_user_login_history`` method already encodes the
        admin-actor authz check + ``security_report_service``
        wiring. Doing it here would duplicate that subsystem's
        contract; delegation keeps the route a thin gateway.
        """
        if not self._login_history_limiter.allow(handler):
            handler._json_response(
                HTTPStatus.TOO_MANY_REQUESTS,
                {
                    "error": "rate_limit_exceeded",
                    "detail": "security-read bucket exhausted",
                },
            )
            return
        helper = self._resolve_login_history_helper()
        # Public ``dispatch`` method handles the suffix match for
        # ``/api/users/{user_id}/login-history`` (see
        # ``_SessionVisibilityGetHelper.dispatch``); this preserves
        # the legacy contract 1:1 — same actor resolution, same
        # error envelopes, same shape. Reconstruct the path-only
        # form (no query) since ``dispatch`` keys its route table
        # off the bare path; the helper reads ``handler.path`` for
        # the ``?limit=`` query string itself.
        helper.dispatch(handler, f"/api/users/{user_id}/login-history")

    # --- internals: dependency resolution ---------------------------------

    def _resolve_login_history_helper(self) -> Any:
        """Return the session-visibility helper.

        Constructor-injected helper short-circuits the lookup;
        otherwise we do a FRESH attribute read against the
        ``security_get_handlers`` module so a test patching the
        helper class at the module boundary wins. Caching the
        resolved class on the instance was the wave-3+4 lazy-cache
        anti-pattern.
        """
        if self._login_history_helper is not None:
            return self._login_history_helper
        return _security_get_handlers_module._SessionVisibilityGetHelper()


__all__ = [
    "LoginHistoryRateLimitAdapter",
    "MeIdentityResolver",
    "UserRepository",
    "UsersGetRoutes",
]
