"""Session-visibility GET handlers.

Dispatcher helper for the read-side of the session-visibility feature.
Standalone module so ``handlers_get.py`` stays under the 400-line file
ratchet while the new endpoints ship together.

Each method is a thin adapter: resolve an ``Actor``, call the already-
hardened service, translate ``AuthorizationError`` to 403 (or 401 when
the actor is anonymous) and ``ValueError`` to 400. No mutation — GET
only. See ``§ 11. Endpoint authz matrix`` in
``docs/security-a11y-contract.md`` for the one-row-per-endpoint table.

The admin ``security-read`` rate-limit bucket is owned by
``handlers_get.py`` (``_security_read_limiter``). It's consulted by
``GetRequestHandler.handle`` BEFORE dispatching here.
"""

from __future__ import annotations

from http import HTTPStatus
from typing import Any, Callable

from media_stack.api.services.security_get_deps import (
    HandlerActorResolverFactory,
    RequestPlumbing,
    SecurityGetDeps,
)
from media_stack.api.session_aggregator_singletons import (
    get_security_report_service,
    get_session_aggregator,
)
from media_stack.core.auth.authz import Actor
from media_stack.core.auth.users.user_service_factory import (
    build_default_api_token_store,
)

_DEFAULT_LIMIT = 100
_DEFAULT_SINCE_HOURS = 24
_DEFAULT_LOOKBACK_DAYS = 90
_DEFAULT_MIN_ATTEMPTS = 5
_DEFAULT_THRESHOLD = 5


class _SessionVisibilityGetHelper:
    """Dispatch session-visibility GETs to bound handler methods.

    Dependencies are constructor-injected so tests can point at stubs.
    Callers (the dispatcher) must have already enforced the
    ``security-read`` rate-limit bucket on admin paths — this helper
    is pure read logic.
    """

    def __init__(
        self,
        *,
        actor_resolver: HandlerActorResolverFactory | None = None,
        aggregator_getter: Callable[[], Any] | None = None,
        report_getter: Callable[[], Any] | None = None,
        token_store_getter: Callable[[], Any] | None = None,
        ban_store_getter: Callable[[], Any] | None = None,
        audit_getter: Callable[[], Any] | None = None,
        mfa_getter: Callable[[str], Any] | None = None,
        plumbing: RequestPlumbing | None = None,
    ) -> None:
        deps = SecurityGetDeps()
        self._actor_resolver = (
            actor_resolver or deps.build_actor_resolver_factory()
        )
        self._aggregator_getter = aggregator_getter or get_session_aggregator
        self._report_getter = report_getter or get_security_report_service
        self._token_store_getter = (
            token_store_getter or build_default_api_token_store
        )
        self._ban_store_getter = ban_store_getter or deps.default_ban_store
        self._audit_getter = audit_getter or deps.default_audit_log
        self._mfa_getter = mfa_getter or deps.default_mfa_state
        self._plumb = plumbing or RequestPlumbing()

    # -- Dispatch --------------------------------------------------------

    def dispatch(self, handler: Any, path: str) -> None:
        """Route ``path`` to one of the handler methods, or 404."""
        fn = self._route_table().get(path)
        if fn is not None:
            fn(handler)
            return
        if path.startswith("/api/users/") and path.endswith("/login-history"):
            middle = path[len("/api/users/"):-len("/login-history")]
            if middle and "/" not in middle:
                self._user_login_history(handler, middle)
                return
        handler._json_response(
            HTTPStatus.NOT_FOUND, {"error": "not found"},
        )

    def _route_table(self) -> dict[str, Callable[[Any], None]]:
        return {
            "/api/sessions/active": self._active_sessions,
            "/api/security/failed-logins": self._failed_logins,
            "/api/security/new-locations": self._new_locations,
            "/api/security/concurrent": self._concurrent_spikes,
            "/api/bans/users": lambda h: self._bans(h, kind="user"),
            "/api/bans/ips": lambda h: self._bans(h, kind="ip"),
            "/api/audit-log/head": self._audit_head,
            "/api/me/sessions": self._my_sessions,
            "/api/me/tokens": self._my_tokens,
            "/api/me/mfa-state": self._my_mfa_state,
            "/api/me/login-history": self._my_login_history,
        }

    def _serve(self, handler: Any, runner: Callable[[Actor], dict]) -> None:
        self._plumb.serve(handler, self._actor_resolver, runner)

    # -- Admin endpoints -------------------------------------------------

    def _active_sessions(self, handler: Any) -> None:
        """GET /api/sessions/active. Authz: admin (SessionAggregator.list_all).
        Bucket: security-read. Shape: ``{"sessions": [SessionDTO...]}``."""
        def _run(actor: Actor) -> dict:
            dtos = self._aggregator_getter().list_all(actor=actor)
            return {"sessions": [d.to_dict() for d in dtos]}
        self._serve(handler, _run)

    def _user_login_history(self, handler: Any, user_id: str) -> None:
        """GET /api/users/{user_id}/login-history?limit=N. Authz: admin.
        Bucket: security-read. Shape: ``{"entries": [...]}``."""
        try:
            limit = self._plumb.int_query(handler, "limit", _DEFAULT_LIMIT)
        except ValueError as exc:
            self._plumb.bad_request(handler, exc)
            return

        def _run(actor: Actor) -> dict:
            entries = self._report_getter().login_history_for_user(
                username=user_id, actor=actor, limit=limit,
            )
            return {"entries": list(entries)}
        self._serve(handler, _run)

    def _failed_logins(self, handler: Any) -> None:
        """GET /api/security/failed-logins?since_hours=&min_attempts=.
        Authz: admin. Bucket: security-read. Shape: ``{"clusters": [...]}``."""
        try:
            since_hours = self._plumb.int_query(
                handler, "since_hours", _DEFAULT_SINCE_HOURS,
            )
            min_attempts = self._plumb.int_query(
                handler, "min_attempts", _DEFAULT_MIN_ATTEMPTS,
            )
        except ValueError as exc:
            self._plumb.bad_request(handler, exc)
            return

        def _run(actor: Actor) -> dict:
            rows = self._report_getter().failed_login_clusters(
                actor=actor, since_hours=since_hours,
                min_attempts=min_attempts,
            )
            return {"clusters": [r.to_dict() for r in rows]}
        self._serve(handler, _run)

    def _new_locations(self, handler: Any) -> None:
        """GET /api/security/new-locations?lookback_days=&since_hours=.
        Authz: admin. Bucket: security-read. Shape: ``{"alerts": [...]}``."""
        try:
            lookback_days = self._plumb.int_query(
                handler, "lookback_days", _DEFAULT_LOOKBACK_DAYS,
            )
            since_hours = self._plumb.int_query(
                handler, "since_hours", _DEFAULT_SINCE_HOURS,
            )
        except ValueError as exc:
            self._plumb.bad_request(handler, exc)
            return

        def _run(actor: Actor) -> dict:
            rows = self._report_getter().new_location_alerts(
                actor=actor, lookback_days=lookback_days,
                since_hours=since_hours,
            )
            return {"alerts": [r.to_dict() for r in rows]}
        self._serve(handler, _run)

    def _concurrent_spikes(self, handler: Any) -> None:
        """GET /api/security/concurrent?threshold=N. Authz: admin.
        Bucket: security-read. Shape: ``{"alerts": [...]}``."""
        try:
            threshold = self._plumb.int_query(
                handler, "threshold", _DEFAULT_THRESHOLD,
            )
        except ValueError as exc:
            self._plumb.bad_request(handler, exc)
            return

        def _run(actor: Actor) -> dict:
            rows = self._report_getter().concurrent_session_spikes(
                actor=actor, threshold=threshold,
            )
            return {"alerts": [r.to_dict() for r in rows]}
        self._serve(handler, _run)

    def _bans(self, handler: Any, *, kind: str) -> None:
        """GET /api/bans/{users|ips}?include_expired=1.
        Authz: admin (enforced locally — BanStore has no decorator).
        Bucket: security-read. Shape: ``{"bans": [...]}``."""
        include_expired = self._plumb.bool_query(handler, "include_expired")

        def _run(actor: Actor) -> dict:
            self._plumb.require_admin(actor)
            store = self._ban_store_getter()
            lister = (
                store.list_user_bans if kind == "user"
                else store.list_ip_bans
            )
            rows = lister(include_expired=include_expired)
            return {"bans": [r.to_dict() for r in rows]}
        self._serve(handler, _run)

    def _audit_head(self, handler: Any) -> None:
        """GET /api/audit-log/head. Authz: admin (external-monitor gate).
        Bucket: security-read. Shape: ``{height, hash, ts, ok}``."""
        def _run(actor: Actor) -> dict:
            self._plumb.require_admin(actor)
            return dict(self._audit_getter().head())
        self._serve(handler, _run)

    # -- Self-service endpoints -----------------------------------------

    def _my_sessions(self, handler: Any) -> None:
        """GET /api/me/sessions. Authz: authenticated, scoped to self.
        Bucket: global. Shape:
        ``{"sessions": [...], "current_session_id": "..."}``."""
        def _run(actor: Actor) -> dict:
            self._plumb.require_authenticated(actor)
            dtos = self._aggregator_getter().list_for_user(
                username=actor.username, actor=actor,
            )
            return {
                "sessions": [d.to_dict() for d in dtos],
                "current_session_id": self._plumb.current_session_id(handler),
            }
        self._serve(handler, _run)

    def _my_tokens(self, handler: Any) -> None:
        """GET /api/me/tokens. Authz: authenticated, scoped to self.
        Bucket: global. Shape: ``{"tokens": [...]}``."""
        def _run(actor: Actor) -> dict:
            self._plumb.require_authenticated(actor)
            rows = self._token_store_getter().list_all(
                owner_username=actor.username,
            )
            return {"tokens": [r.to_dict() for r in rows]}
        self._serve(handler, _run)

    def _my_mfa_state(self, handler: Any) -> None:
        """GET /api/me/mfa-state. Authz: authenticated. Bucket: global.
        Shape: ``{enrolled, enrolled_methods, last_used_method,
        last_used_at, required}``."""
        def _run(actor: Actor) -> dict:
            self._plumb.require_authenticated(actor)
            return dict(self._mfa_getter(actor.username).to_dict())
        self._serve(handler, _run)

    def _my_login_history(self, handler: Any) -> None:
        """GET /api/me/login-history?limit=N. Authz: authenticated, scoped
        to self (service-layer @requires_admin is bypassed only for
        the caller's own username). Bucket: global. Shape:
        ``{"entries": [...]}``."""
        try:
            limit = self._plumb.int_query(handler, "limit", _DEFAULT_LIMIT)
        except ValueError as exc:
            self._plumb.bad_request(handler, exc)
            return

        def _run(actor: Actor) -> dict:
            self._plumb.require_authenticated(actor)
            # Elevate for the @requires_admin-gated service call; safe
            # because the target username is pinned to actor.username.
            elevated = Actor(
                username=actor.username, roles=actor.roles, is_admin=True,
                session_id=actor.session_id,
                source_provider=actor.source_provider,
                client_ip=actor.client_ip, user_agent=actor.user_agent,
            )
            entries = self._report_getter().login_history_for_user(
                username=actor.username, actor=elevated, limit=limit,
            )
            return {"entries": list(entries)}
        self._serve(handler, _run)


__all__ = ["_SessionVisibilityGetHelper"]
