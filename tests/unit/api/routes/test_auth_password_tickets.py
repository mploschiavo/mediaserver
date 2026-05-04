"""Tests for ``api/routes/auth_password_tickets.py``
(ADR-0007 Phase 2 wave 7).

Route: ``GET /api/password-tickets/{ticket_id}``.

Security contracts pinned:
* Admin gate fires BEFORE the rate-limit check.
* Audit-log is attempted on EVERY request that passes admin + rate-limit
  gates — including expired/unknown tickets.
* Audit failure must NOT suppress the plaintext response.
* Response shape is ``{"password": "...", "user_id": "..."}``.
"""

from __future__ import annotations

import json
from typing import Any

from media_stack.api.routes.auth_password_tickets import (
    AuthPasswordTicketsGetRoutes,
    PasswordTicketConsumerService,
)
from media_stack.api.routing import (
    DefaultDispatcher,
    DispatchOutcome,
    Router,
    RouterDispatcher,
)
from tests.unit.api.routes._helpers import RouteDispatchHarness


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------


class _RouteHarness:
    @classmethod
    def with_routes(
        cls, routes: AuthPasswordTicketsGetRoutes,
    ) -> RouteDispatchHarness:
        DefaultDispatcher.reset_for_tests()
        router = Router()
        cls._rebind(router, routes)
        return RouteDispatchHarness(RouterDispatcher(router))

    @classmethod
    def _rebind(
        cls, router: Router, routes: AuthPasswordTicketsGetRoutes,
    ) -> None:
        for key, route in list(router._exact.items()):
            m = cls._maybe_replacement(route, routes)
            if m is not None:
                router._exact[key] = type(route)(
                    verb=route.verb, path=route.path, handler=m,
                    pattern=route.pattern, param_names=route.param_names,
                    display=route.display,
                )
        for idx, route in enumerate(list(router._parameterized)):
            m = cls._maybe_replacement(route, routes)
            if m is not None:
                router._parameterized[idx] = type(route)(
                    verb=route.verb, path=route.path, handler=m,
                    pattern=route.pattern, param_names=route.param_names,
                    display=route.display,
                )

    @staticmethod
    def _maybe_replacement(
        route: Any, routes: AuthPasswordTicketsGetRoutes,
    ) -> Any:
        if "AuthPasswordTicketsGetRoutes" not in route.display:
            return None
        method_name = route.display.rsplit(".", 1)[-1]
        return getattr(routes, method_name, None)


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _AlwaysAllowLimiter:
    def allow(self, *, client_id: str, bucket: str = "default") -> bool:
        return True


class _AlwaysDenyLimiter:
    def allow(self, *, client_id: str, bucket: str = "default") -> bool:
        return False


class _FakeTicketStore:
    def __init__(
        self,
        *,
        bound_user: str = "alice",
        plaintext: str | None = "s3cr3t",
    ) -> None:
        self._bound_user = bound_user
        self._plaintext = plaintext

    def peek_user_id(self, ticket_id: str) -> str | None:
        return self._bound_user or None

    def consume(self, ticket_id: str) -> str | None:
        return self._plaintext


class _FakeAudit:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def append(self, **kwargs: Any) -> None:
        self.calls.append(kwargs)


class _NullStore:
    def get_by_username(self, username: str) -> None:
        return None


class _FakeUserService:
    def __init__(self, audit: _FakeAudit) -> None:
        self._audit = audit
        self._store = _NullStore()
        self._roles: dict[str, Any] = {}


class _AdminService(PasswordTicketConsumerService):
    """Service subclass that always passes the admin check."""

    def __init__(
        self,
        *,
        ticket_store_fn: Any = None,
        user_service_fn: Any = None,
        limiter_fn: Any = None,
    ) -> None:
        super().__init__(
            ticket_store_fn=ticket_store_fn,
            user_service_fn=user_service_fn,
            limiter_fn=limiter_fn,
            actor_resolver=lambda h: "admin",
            admin_username_fn=lambda: "admin",
        )

    def _requester_is_admin(self, handler: Any, username: str) -> bool:
        return True


def _admin_service(
    *,
    plaintext: str | None = "s3cr3t",
    bound_user: str = "alice",
    audit: _FakeAudit | None = None,
) -> tuple[_AdminService, _FakeAudit]:
    if audit is None:
        audit = _FakeAudit()
    store = _FakeTicketStore(bound_user=bound_user, plaintext=plaintext)
    svc = _FakeUserService(audit)
    service = _AdminService(
        ticket_store_fn=lambda: store,
        user_service_fn=lambda: svc,
        limiter_fn=lambda: _AlwaysAllowLimiter(),
    )
    return service, audit


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


class TestPasswordTicketConsumeRoute:
    def test_happy_path_returns_password_and_user_id(self) -> None:
        """200 with ``{"password": "...", "user_id": "..."}`` shape."""
        service, _ = _admin_service(plaintext="s3cr3t", bound_user="alice")
        routes = AuthPasswordTicketsGetRoutes(consumer_service=service)
        harness = _RouteHarness.with_routes(routes)

        response = harness.dispatch(
            "GET", "/api/password-tickets/tkt-abc123",
        )

        assert response.status == 200
        assert json.loads(response.body) == {
            "password": "s3cr3t",
            "user_id": "alice",
        }

    def test_path_param_ticket_id_forwarded_to_store(self) -> None:
        """``ticket_id`` captured from URL path and passed to
        ``store.consume``."""
        consumed: list[str] = []

        class _SpyStore(_FakeTicketStore):
            def consume(self, ticket_id: str) -> str | None:
                consumed.append(ticket_id)
                return self._plaintext

            def peek_user_id(self, ticket_id: str) -> str | None:
                return "alice"

        audit = _FakeAudit()
        svc = _FakeUserService(audit)
        service = _AdminService(
            ticket_store_fn=lambda: _SpyStore(),
            user_service_fn=lambda: svc,
            limiter_fn=lambda: _AlwaysAllowLimiter(),
        )
        routes = AuthPasswordTicketsGetRoutes(consumer_service=service)
        harness = _RouteHarness.with_routes(routes)

        harness.dispatch("GET", "/api/password-tickets/tkt-deadbeef")

        assert consumed == ["tkt-deadbeef"]

    def test_expired_ticket_returns_404(self) -> None:
        """``store.consume`` returns None → 404 with legacy error string."""
        service, _ = _admin_service(plaintext=None)
        routes = AuthPasswordTicketsGetRoutes(consumer_service=service)
        harness = _RouteHarness.with_routes(routes)

        response = harness.dispatch(
            "GET", "/api/password-tickets/tkt-expired",
        )

        assert response.status == 404
        assert json.loads(response.body) == {
            "error": "ticket expired, unknown, or already consumed",
        }


# ---------------------------------------------------------------------------
# Admin gate — 403 for non-admin
# ---------------------------------------------------------------------------


class TestPasswordTicketAdminGate:
    def test_non_admin_returns_403_without_touching_store(self) -> None:
        """Admin gate fires BEFORE rate-limit and store calls."""
        store_touched: list[bool] = []

        class _SpyStore(_FakeTicketStore):
            def consume(self, ticket_id: str) -> str | None:
                store_touched.append(True)
                return super().consume(ticket_id)

        class _NonAdminService(PasswordTicketConsumerService):
            def __init__(self) -> None:
                super().__init__(
                    ticket_store_fn=lambda: _SpyStore(),
                    limiter_fn=lambda: _AlwaysAllowLimiter(),
                    actor_resolver=lambda h: "regular-user",
                    admin_username_fn=lambda: "admin",
                )

            def _requester_is_admin(self, handler: Any, username: str) -> bool:
                return False

        routes = AuthPasswordTicketsGetRoutes(
            consumer_service=_NonAdminService(),
        )
        harness = _RouteHarness.with_routes(routes)

        response = harness.dispatch(
            "GET", "/api/password-tickets/tkt-abc",
        )

        assert response.status == 403
        assert json.loads(response.body) == {"error": "admin required"}
        assert store_touched == []  # store must NOT have been touched


# ---------------------------------------------------------------------------
# Rate limit — 429 when bucket exhausted
# ---------------------------------------------------------------------------


class TestPasswordTicketRateLimit:
    def test_rate_limited_returns_429_without_touching_store(self) -> None:
        """Rate-limit gate fires after admin check, before store."""
        store_touched: list[bool] = []

        class _SpyStore(_FakeTicketStore):
            def consume(self, ticket_id: str) -> str | None:
                store_touched.append(True)
                return super().consume(ticket_id)

        audit = _FakeAudit()
        svc = _FakeUserService(audit)
        service = _AdminService(
            ticket_store_fn=lambda: _SpyStore(),
            user_service_fn=lambda: svc,
            limiter_fn=lambda: _AlwaysDenyLimiter(),
        )
        routes = AuthPasswordTicketsGetRoutes(consumer_service=service)
        harness = _RouteHarness.with_routes(routes)

        response = harness.dispatch(
            "GET", "/api/password-tickets/tkt-limited",
        )

        assert response.status == 429
        assert json.loads(response.body) == {"error": "rate limit exceeded"}
        assert store_touched == []


# ---------------------------------------------------------------------------
# Audit logging
# ---------------------------------------------------------------------------


class TestPasswordTicketAuditLog:
    def test_audit_appended_with_correct_action_on_success(self) -> None:
        """``audit.append`` called with action=PASSWORD_TICKET_CONSUMED
        and result='ok' on a successful consume."""
        service, audit = _admin_service(plaintext="pw", bound_user="alice")
        routes = AuthPasswordTicketsGetRoutes(consumer_service=service)
        harness = _RouteHarness.with_routes(routes)

        harness.dispatch("GET", "/api/password-tickets/tkt-abc")

        assert len(audit.calls) == 1
        call = audit.calls[0]
        assert call["action"] == "password_ticket_consumed"
        assert call["result"] == "ok"
        assert call["target"] == "alice"

    def test_audit_appended_even_on_expired_ticket(self) -> None:
        """``audit.append`` called even when the ticket is expired.
        Operators need the full audit trail of every attempt."""
        service, audit = _admin_service(plaintext=None, bound_user="")
        routes = AuthPasswordTicketsGetRoutes(consumer_service=service)
        harness = _RouteHarness.with_routes(routes)

        harness.dispatch("GET", "/api/password-tickets/tkt-expired")

        assert len(audit.calls) == 1
        call = audit.calls[0]
        assert call["action"] == "password_ticket_consumed"
        assert call["result"] == "expired"

    def test_audit_failure_does_not_suppress_plaintext(self) -> None:
        """If ``audit.append`` raises, plaintext must still be returned
        to the legitimate admin. Swallow, log, carry on."""

        class _FailingAudit(_FakeAudit):
            def append(self, **kwargs: Any) -> None:
                raise RuntimeError("audit backend down")

        store = _FakeTicketStore(plaintext="pw", bound_user="alice")
        svc = _FakeUserService(_FailingAudit())
        service = _AdminService(
            ticket_store_fn=lambda: store,
            user_service_fn=lambda: svc,
            limiter_fn=lambda: _AlwaysAllowLimiter(),
        )
        routes = AuthPasswordTicketsGetRoutes(consumer_service=service)
        harness = _RouteHarness.with_routes(routes)

        response = harness.dispatch(
            "GET", "/api/password-tickets/tkt-audit-fail",
        )

        assert response.status == 200
        assert json.loads(response.body)["password"] == "pw"


# ---------------------------------------------------------------------------
# Auto-discovery + spec-parity integration
# ---------------------------------------------------------------------------


class TestRoutingIntegration:
    def test_password_tickets_route_registered(self) -> None:
        harness = RouteDispatchHarness.with_default_router()
        found = any(
            r.path == "/api/password-tickets/{ticket_id}"
            for r in harness._dispatcher._router.registered_routes()
        )
        assert found, "/api/password-tickets/{ticket_id} not registered"

    def test_post_to_password_tickets_does_not_match_get_route(
        self,
    ) -> None:
        """``POST`` against the parameterized password-ticket path
        falls through with ``NO_MATCH``. The Router's literal-string
        spec-path lookup can't see the parameterized template, so it
        returns ``NO_MATCH`` rather than ``METHOD_NOT_ALLOWED`` for
        path-template routes — pin that contract.
        """
        harness = RouteDispatchHarness.with_default_router()
        outcome, _ = harness.try_dispatch(
            "POST", "/api/password-tickets/tkt-abc",
        )
        assert outcome == DispatchOutcome.NO_MATCH
