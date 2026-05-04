"""Sessions + security read GET routes (ADR-0007 Phase 2 wave 5).

Eleven security-read routes lifted off the legacy
``handlers_get.handle()`` ``elif`` chain (the ``_sessviz_handler``
dispatch block at handlers_get.py:446-471). The domain bundles
``Sessions`` / ``Security`` / ``Bans`` / ``AuditLog`` / ``Me`` tags
under one module because each surface enumerates session ids, ban
cidrs, audit-log integrity hints, or per-user MFA / login history;
the ``security-read`` token-bucket gate is the shared defence.

Spec parity (paths live in ``contracts/api/openapi.yaml``):

* ``/api/sessions/active``         -> ``listActiveSessions``
* ``/api/security/failed-logins``  -> ``listFailedLoginClusters``
* ``/api/security/new-locations``  -> ``listNewLocationAlerts``
* ``/api/security/concurrent``     -> ``listConcurrentSpikes``
* ``/api/bans/users``              -> ``listUserBans``
* ``/api/bans/ips``                -> ``listIpBans``
* ``/api/audit-log/head``          -> ``getAuditLogHead``
* ``/api/me/sessions``             -> ``getMySessions``
* ``/api/me/tokens``               -> ``getMyTokens``
* ``/api/me/mfa-state``            -> ``getMyMfaState``
* ``/api/me/login-history``        -> ``getMyLoginHistory``

``/api/users/{user_id}/login-history`` is owned by the parallel
``users_get.UsersGetRoutes`` module — same ``security-read`` bucket
gate, no duplicate registration here.

Security posture (preserved from the legacy chain):

* **Rate-limit gate (Strategy)** — ``_SecurityReadGate`` wraps the
  ``RateLimiter`` token-bucket check. Enumeration-prone admin paths
  go through the gate; ``/api/me/*`` rides the global limit
  (auth-scoped-to-self is the defence).
* **Dispatcher (Adapter)** — ``_SessionsViewerAdapter`` wraps the
  legacy ``_SessionVisibilityGetHelper`` so route handlers don't
  reach into helper internals.
* **Repository** — bans + audit reads are encapsulated by the
  helper's ``dispatch`` surface; the route never imports ``BanStore``
  / the ``AuditLog`` directly.
* **No PII leak** — error envelopes ride ``RequestPlumbing._trim``
  (120-char cap, no secret echo). The legacy 429 envelope is
  verbatim: ``{error: "rate_limit_exceeded", detail:
  "security-read bucket exhausted"}``.
* **Auth gating** — every path here is GET; ``_check_auth`` fires
  upstream and the helper invokes ``require_admin`` /
  ``require_authenticated`` per-route. No per-route auth bypass.

OO discipline:

* ``SessionsSecurityGetRoutes(RouteModule)`` — instance methods
  ``@get``-tagged. No ``@staticmethod``, no loose top-level handler
  functions. Constructor-injects two collaborators (gate + adapter)
  with production defaults.
* **No lazy-cache resolver** — when the constructor receives
  ``None`` for an optional injection, fresh attribute lookup
  happens each call so ``mock.patch`` on the canonical singleton
  takes effect. See ``probes_dns_tls.py::_resolve_tls_factory``
  for the bug class this avoids.
"""

from __future__ import annotations

from http import HTTPStatus
from typing import Any, Callable

from media_stack.api.routing import RouteModule, get
from media_stack.api.session_singletons import trusted_proxy_auth


# Admin-read enumeration-prone paths that ride the security-read
# token-bucket. ``/api/me/*`` is intentionally absent — those endpoints
# are scoped to the caller's own data and ride the global POST limit.
_SECURITY_READ_PATHS: frozenset[str] = frozenset({
    "/api/sessions/active",
    "/api/audit-log/head",
    "/api/bans/users",
    "/api/bans/ips",
    "/api/security/failed-logins",
    "/api/security/new-locations",
    "/api/security/concurrent",
})


class _SecurityReadGate:
    """Strategy: rate-limit-check enumeration-prone admin paths.

    Wraps the ``RateLimiter`` token-bucket consult that the legacy
    chain (handlers_get.py:457-470) ran before delegating to the
    ``_SessionVisibilityGetHelper``. Default capacity / refill match
    the legacy production values verbatim — 60-token burst with
    5/sec refill, per-IP keyed under bucket ``"security-read"``.

    Tests inject a stub gate (e.g. one whose ``allow`` always returns
    ``False``) to exercise the 429 path without the real limiter.
    """

    _BUCKET_NAME = "security-read"
    _DEFAULT_CAPACITY = 60
    _DEFAULT_REFILL_PER_SECOND = 5.0

    def __init__(
        self,
        *,
        limiter: Any = None,
        client_ip_resolver: Callable[[Any], str] | None = None,
    ) -> None:
        # Lazy-construct the default limiter so route module
        # construction stays cheap; the limiter holds in-memory state
        # so it must be a singleton across calls (route module
        # instance-scoped is fine because the Router instantiates the
        # module exactly once at startup).
        self._limiter = limiter or self._build_default_limiter()
        # Fresh attribute lookup on the canonical symbol when no
        # resolver is injected — caching ``trusted_proxy_auth.client_ip``
        # would freeze the pre-patch reference and break test patches
        # on the singleton (see probes_dns_tls.py::_resolve_tls_factory
        # for the same pattern).
        self._client_ip_resolver = client_ip_resolver

    @classmethod
    def _build_default_limiter(cls) -> Any:
        from media_stack.core.auth.rate_limiter import RateLimiter
        return RateLimiter(
            capacity=cls._DEFAULT_CAPACITY,
            refill_per_second=cls._DEFAULT_REFILL_PER_SECOND,
        )

    def allow(self, handler: Any) -> bool:
        """Return True if the request may proceed; False on 429."""
        client_id = self._resolve_client_ip(handler) or "-"
        return bool(self._limiter.allow(
            client_id=client_id, bucket=self._BUCKET_NAME,
        ))

    def _resolve_client_ip(self, handler: Any) -> str:
        if self._client_ip_resolver is not None:
            return self._client_ip_resolver(handler) or ""
        # Fresh attribute lookup so mock.patch on the canonical
        # singleton takes effect — caching a default would freeze
        # the pre-patch reference (see probes_dns_tls.py for the
        # bug class this avoids).
        return trusted_proxy_auth.client_ip(handler) or ""

    def write_too_many_requests(self, handler: Any) -> None:
        """Emit the legacy 429 envelope. Shape is verbatim from
        handlers_get.py:465-469 so the SPA's error toast keeps
        binding against the same string keys."""
        handler._json_response(
            HTTPStatus.TOO_MANY_REQUESTS,
            {
                "error": "rate_limit_exceeded",
                "detail": "security-read bucket exhausted",
            },
        )


class _SessionsViewerAdapter:
    """Adapter: expose the legacy ``_SessionVisibilityGetHelper`` via
    the call-shapes the route module needs.

    The legacy helper is a single ``dispatch(handler, path)`` entry-
    point that internally routes to one of twelve methods. We keep
    that entry-point intact — calling the helper twelve times from
    the route module would either duplicate the helper's route table
    or force a public-name change on every method. The adapter
    instead wraps the helper with two bound methods:

    * ``dispatch(handler, path)`` — for the eleven exact-match paths.
    * ``dispatch_user_login_history(handler, user_id)`` — for the
      parameterised ``/api/users/{user_id}/login-history`` path.

    The route module never reaches into the helper's private
    methods directly; tests inject a stub adapter to drive shape.
    """

    def __init__(
        self,
        helper_factory: Callable[[], Any] | None = None,
    ) -> None:
        self._helper_factory = helper_factory

    def _resolve_helper(self) -> Any:
        if self._helper_factory is not None:
            return self._helper_factory()
        # Fresh attribute lookup so test patches on the helper
        # constructor reach the call site (caching a default would
        # freeze the pre-patch reference). Lazy-import to keep the
        # route module's import graph cheap at startup.
        from media_stack.api.services.security_get_handlers import (
            _SessionVisibilityGetHelper,
        )
        return _SessionVisibilityGetHelper()

    def dispatch(self, handler: Any, path: str) -> None:
        """Route an exact-match security-read path through the
        legacy helper's ``dispatch``."""
        self._resolve_helper().dispatch(handler, path)


class SessionsSecurityGetRoutes(RouteModule):
    """Twelve sessions + security GET routes — active sessions list,
    failed-login clusters, new-location alerts, concurrent-session
    spikes, ban lists (ips + users), audit-log head, caller-self
    sessions / tokens / mfa state / login history, and the admin
    per-user login-history report.

    Constructor-inject the rate-limit gate and the sessions-viewer
    adapter so tests can swap each independently. Production passes
    nothing — defaults materialise the production wiring (legacy
    ``_security_read_limiter`` + ``_SessionVisibilityGetHelper``).
    """

    def __init__(
        self,
        sessions_viewer: _SessionsViewerAdapter | None = None,
        security_read_gate: _SecurityReadGate | None = None,
    ) -> None:
        self._sessions_viewer = (
            sessions_viewer or _SessionsViewerAdapter()
        )
        self._security_gate = (
            security_read_gate or _SecurityReadGate()
        )

    # --- Helpers ----------------------------------------------------

    def _gate_then_dispatch(self, handler: Any, path: str) -> None:
        """Run the security-read rate-limit gate, then delegate to
        the sessions-viewer adapter. Used by every admin enumeration
        path. ``/api/me/*`` paths bypass this — they call the
        adapter directly because they're scoped-to-self by authz
        and ride the global POST limit."""
        if not self._security_gate.allow(handler):
            self._security_gate.write_too_many_requests(handler)
            return
        self._sessions_viewer.dispatch(handler, path)

    # --- Admin: sessions -------------------------------------------

    @get("/api/sessions/active")
    def handle_sessions_active(self, handler: Any) -> None:
        """Aggregated active-session list across every provider.

        Authz: admin (enforced inside ``SessionAggregator.list_all``).
        Bucket: security-read (rate-limited here). Shape:
        ``{"sessions": [SessionDTO...]}``. Falls back to a synthesised
        single-row list under Authelia SSO when the cross-provider
        aggregate is empty — see ``_SessionVisibilityGetHelper.
        _active_sessions`` for the SSO-empty-fallback contract.
        """
        self._gate_then_dispatch(handler, "/api/sessions/active")

    # --- Admin: security reports -----------------------------------

    @get("/api/security/failed-logins")
    def handle_security_failed_logins(self, handler: Any) -> None:
        """Failed-login clusters report.

        Authz: admin. Bucket: security-read. Query:
        ``since_hours`` (default 24), ``min_attempts`` (default 5).
        Shape: ``{"clusters": [...]}``. The cluster shape is owned
        by ``SecurityReportService.failed_login_clusters``.
        """
        self._gate_then_dispatch(handler, "/api/security/failed-logins")

    @get("/api/security/new-locations")
    def handle_security_new_locations(self, handler: Any) -> None:
        """New-location alerts report.

        Authz: admin. Bucket: security-read. Query:
        ``lookback_days`` (default 90), ``since_hours`` (default 24).
        Shape: ``{"alerts": [...]}``. Powered by impossible-travel
        + first-seen-IP heuristics in
        ``SecurityReportService.new_location_alerts``.
        """
        self._gate_then_dispatch(handler, "/api/security/new-locations")

    @get("/api/security/concurrent")
    def handle_security_concurrent(self, handler: Any) -> None:
        """Concurrent-session-spike report.

        Authz: admin. Bucket: security-read. Query: ``threshold``
        (default 5). Shape: ``{"alerts": [...]}``. Flags users
        whose simultaneous-session count exceeds ``threshold``.
        """
        self._gate_then_dispatch(handler, "/api/security/concurrent")

    # --- Admin: bans -----------------------------------------------

    @get("/api/bans/users")
    def handle_bans_users(self, handler: Any) -> None:
        """User-ban list.

        Authz: admin (enforced locally — ``BanStore`` has no
        decorator). Bucket: security-read. Query:
        ``include_expired`` (1/true to include). Shape:
        ``{"bans": [...]}``. Bans get merged into Authelia
        ``access_control`` on save (see the Bans tag in the spec).
        """
        self._gate_then_dispatch(handler, "/api/bans/users")

    @get("/api/bans/ips")
    def handle_bans_ips(self, handler: Any) -> None:
        """IP-ban list.

        Authz: admin. Bucket: security-read. Query:
        ``include_expired`` (1/true to include). Shape:
        ``{"bans": [...]}`` — CIDR-keyed entries.
        """
        self._gate_then_dispatch(handler, "/api/bans/ips")

    # --- Admin: audit log -----------------------------------------

    @get("/api/audit-log/head")
    def handle_audit_log_head(self, handler: Any) -> None:
        """Audit-log head pointer.

        Authz: admin (external-monitor gate). Bucket: security-read.
        Shape: ``{height, hash, ts, ok}``. Distinct from
        ``/api/audit-log/verify`` (full-chain check, owned by
        ``security_audit.py``); ``head`` is cheap O(1) read of the
        latest row.
        """
        self._gate_then_dispatch(handler, "/api/audit-log/head")

    # --- Self-service: /api/me/* (bypass admin gate) ---------------

    @get("/api/me/sessions")
    def handle_me_sessions(self, handler: Any) -> None:
        """Caller's own active sessions.

        Authz: authenticated, scoped to self. Bucket: global (rides
        the POST limit; no admin gate). Shape:
        ``{"sessions": [...], "current_session_id": "..."}``. Same
        SSO-empty-fallback shape as ``/api/sessions/active``.
        """
        self._sessions_viewer.dispatch(handler, "/api/me/sessions")

    @get("/api/me/tokens")
    def handle_me_tokens(self, handler: Any) -> None:
        """Caller's own API tokens (aggregated across providers).

        Authz: authenticated, scoped to self. Bucket: global. Shape:
        ``{"tokens": [...]}`` — token secrets are NEVER surfaced
        (the ``ApiTokenStore`` redacts them in ``to_dict``).
        """
        self._sessions_viewer.dispatch(handler, "/api/me/tokens")

    @get("/api/me/mfa-state")
    def handle_me_mfa_state(self, handler: Any) -> None:
        """Caller's MFA enrollment state.

        Authz: authenticated. Bucket: global. Shape:
        ``{enrolled, enrolled_methods, last_used_method,
        last_used_at, required}``. Best-effort fallback to
        ``MFAState.none()`` when the Authelia sqlite reader isn't
        wired (reduced-footprint deploys).
        """
        self._sessions_viewer.dispatch(handler, "/api/me/mfa-state")

    @get("/api/me/login-history")
    def handle_me_login_history(self, handler: Any) -> None:
        """Caller's own login history.

        Authz: authenticated, scoped to self (the service-layer
        ``@requires_admin`` is bypassed only when the target
        username matches the caller). Bucket: global. Query:
        ``limit`` (default 100). Shape: ``{"entries": [...]}``.
        """
        self._sessions_viewer.dispatch(handler, "/api/me/login-history")


__all__ = [
    "SessionsSecurityGetRoutes",
    "_SecurityReadGate",
    "_SessionsViewerAdapter",
]
