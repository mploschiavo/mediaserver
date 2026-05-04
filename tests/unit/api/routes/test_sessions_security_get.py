"""Tests for ``api/routes/sessions_security_get.py``
(ADR-0007 Phase 2 wave 5).

Twelve route tests + a routing-integration sanity check + a
defence-in-depth security suite that pins the rate-limit gate, the
no-PII-leak contract on error envelopes, and the lazy-cache-resolver
absence (the bug class called out in
``probes_dns_tls.py::_resolve_tls_factory``'s docstring — caching
default-resolved factories on ``self`` would freeze pre-patch
references and break tests that ``mock.patch`` on the canonical
singleton).

Auth gating note: every path here is GET. The controller's
``_check_auth`` middleware fires upstream of the dispatcher in
production; the route module itself adds no per-route auth bypass.
The ``RequestPlumbing`` collaborator (in
``security_get_deps.py::RequestPlumbing``) emits 401 / 403 inside
the legacy helper — tests below pin the rate-limit gate emits 429
BEFORE the helper runs, so the gate is the load-bearing security
edge for this domain.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import patch

from media_stack.api.routes.sessions_security_get import (
    SessionsSecurityGetRoutes,
    _SecurityReadGate,
    _SessionsViewerAdapter,
)
from media_stack.api.routing import DefaultDispatcher, DispatchOutcome
from tests.unit.api.routes._helpers import (
    MockControllerHandler,
    RouteDispatchHarness,
)


# ---------------------------------------------------------------------------
# Test scaffolding
# ---------------------------------------------------------------------------


class _StubAdapter:
    """Capture every adapter call so tests can assert what the
    route module delegated. Mirrors the legacy
    ``_SessionVisibilityGetHelper.dispatch`` signature without
    actually instantiating the helper graph."""

    def __init__(self) -> None:
        self.dispatch_calls: list[tuple[str, str]] = []
        self.payload: dict[str, Any] = {"ok": True}

    def dispatch(self, handler: Any, path: str) -> None:
        self.dispatch_calls.append(("dispatch", path))
        handler._json_response(200, dict(self.payload))


class _AlwaysAllowGate:
    """Rate-limit gate stub that always permits. Used by tests that
    need to verify the delegation path without exercising the 429
    branch."""

    def allow(self, handler: Any) -> bool:
        return True

    def write_too_many_requests(self, handler: Any) -> None:  # pragma: no cover
        raise AssertionError("gate.write_too_many_requests called unexpectedly")


class _AlwaysDenyGate:
    """Rate-limit gate stub that always denies. Used to verify the
    429 envelope shape + that the adapter is NOT called when the
    gate denies."""

    def __init__(self) -> None:
        self.denied = 0

    def allow(self, handler: Any) -> bool:
        return False

    def write_too_many_requests(self, handler: Any) -> None:
        self.denied += 1
        handler._json_response(
            429,
            {
                "error": "rate_limit_exceeded",
                "detail": "security-read bucket exhausted",
            },
        )


def _routes_with_stubs(
    adapter: _StubAdapter | None = None,
    gate: Any = None,
) -> tuple[SessionsSecurityGetRoutes, _StubAdapter, Any]:
    adapter = adapter or _StubAdapter()
    gate = gate or _AlwaysAllowGate()
    routes = SessionsSecurityGetRoutes(
        sessions_viewer=adapter,
        security_read_gate=gate,
    )
    return routes, adapter, gate


# ---------------------------------------------------------------------------
# _SecurityReadGate unit tests
# ---------------------------------------------------------------------------


class TestSecurityReadGate:
    """Strategy: rate-limit-check enumeration-prone admin paths.
    Pinned because the gate is the load-bearing security edge for
    this domain."""

    def test_allow_returns_true_when_limiter_has_capacity(self) -> None:
        class _Limiter:
            def allow(self, *, client_id: str, bucket: str) -> bool:
                assert bucket == "security-read"
                assert client_id == "10.1.55.177"
                return True

        gate = _SecurityReadGate(
            limiter=_Limiter(),
            client_ip_resolver=lambda h: "10.1.55.177",
        )
        handler = MockControllerHandler(path="/api/sessions/active")
        assert gate.allow(handler) is True

    def test_allow_returns_false_when_limiter_exhausted(self) -> None:
        class _Limiter:
            def allow(self, *, client_id: str, bucket: str) -> bool:
                return False

        gate = _SecurityReadGate(
            limiter=_Limiter(),
            client_ip_resolver=lambda h: "10.1.55.177",
        )
        handler = MockControllerHandler(path="/api/sessions/active")
        assert gate.allow(handler) is False

    def test_empty_client_ip_falls_back_to_dash_sentinel(self) -> None:
        """The legacy chain keys an unknown caller as ``"-"`` so the
        bucket is shared rather than spawning per-blank-IP buckets."""
        captured: dict[str, str] = {}

        class _Limiter:
            def allow(self, *, client_id: str, bucket: str) -> bool:
                captured["client_id"] = client_id
                return True

        gate = _SecurityReadGate(
            limiter=_Limiter(),
            client_ip_resolver=lambda h: "",
        )
        handler = MockControllerHandler(path="/api/sessions/active")
        gate.allow(handler)
        assert captured["client_id"] == "-"

    def test_429_envelope_matches_legacy_shape(self) -> None:
        gate = _SecurityReadGate(
            limiter=None,
            client_ip_resolver=lambda h: "x",
        )
        handler = MockControllerHandler(path="/api/sessions/active")
        gate.write_too_many_requests(handler)
        assert handler.captured.status == 429
        body = json.loads(handler.captured.body)
        assert body == {
            "error": "rate_limit_exceeded",
            "detail": "security-read bucket exhausted",
        }

    def test_no_pii_in_429_envelope(self) -> None:
        """Defence-in-depth: the 429 envelope never echoes the
        client IP, the request path, or any header value — the
        legacy contract is two static strings only."""
        gate = _SecurityReadGate(
            limiter=None,
            client_ip_resolver=lambda h: "192.0.2.1",
        )
        handler = MockControllerHandler(
            path="/api/sessions/active?secret=abc123",
            headers={"X-Forwarded-For": "192.0.2.1, 10.0.0.1"},
        )
        gate.write_too_many_requests(handler)
        body_text = handler.captured.body.decode("utf-8")
        assert "192.0.2.1" not in body_text
        assert "secret" not in body_text
        assert "abc123" not in body_text

    def test_default_construction_does_not_cache_resolver(self) -> None:
        """Anti-pattern check: the default ``client_ip_resolver``
        path must use fresh attribute lookup each call so
        ``mock.patch`` on the singleton symbol takes effect. See
        ``probes_dns_tls.py::_resolve_tls_factory`` for the bug
        class. We patch the singleton AFTER construction and assert
        the patch is honoured on the next ``allow`` call."""

        class _Limiter:
            def __init__(self) -> None:
                self.calls: list[str] = []

            def allow(self, *, client_id: str, bucket: str) -> bool:
                self.calls.append(client_id)
                return True

        limiter = _Limiter()
        gate = _SecurityReadGate(limiter=limiter)
        handler = MockControllerHandler(path="/api/sessions/active")

        with patch(
            "media_stack.api.routes.sessions_security_get."
            "trusted_proxy_auth.client_ip",
            return_value="203.0.113.7",
        ):
            gate.allow(handler)

        assert limiter.calls == ["203.0.113.7"]


# ---------------------------------------------------------------------------
# _SessionsViewerAdapter unit tests
# ---------------------------------------------------------------------------


class TestSessionsViewerAdapter:
    """Adapter: wraps the legacy ``_SessionVisibilityGetHelper``
    so route handlers don't reach into helper internals."""

    def test_dispatch_calls_helper_dispatch(self) -> None:
        seen: list[tuple[Any, str]] = []

        class _Helper:
            def dispatch(self, handler: Any, path: str) -> None:
                seen.append((handler, path))

        adapter = _SessionsViewerAdapter(helper_factory=lambda: _Helper())
        handler = MockControllerHandler(path="/api/sessions/active")
        adapter.dispatch(handler, "/api/sessions/active")
        assert seen == [(handler, "/api/sessions/active")]

    def test_factory_resolution_is_lazy_not_cached(self) -> None:
        """Anti-pattern check: the helper factory must NOT be cached
        on the adapter. Two consecutive ``dispatch`` calls each
        resolve a fresh helper instance via the factory — proving
        the factory result is not memoised on ``self``."""
        instance_count = 0

        class _Helper:
            def __init__(self) -> None:
                pass

            def dispatch(self, handler: Any, path: str) -> None:
                handler._json_response(200, {})

        def _factory() -> _Helper:
            nonlocal instance_count
            instance_count += 1
            return _Helper()

        adapter = _SessionsViewerAdapter(helper_factory=_factory)
        handler1 = MockControllerHandler(path="/api/sessions/active")
        handler2 = MockControllerHandler(path="/api/me/sessions")
        adapter.dispatch(handler1, "/api/sessions/active")
        adapter.dispatch(handler2, "/api/me/sessions")
        # Factory runs every call — no cache.
        assert instance_count == 2


# ---------------------------------------------------------------------------
# Admin route tests — security-read gated
# ---------------------------------------------------------------------------


class TestActiveSessionsRoute:
    """``GET /api/sessions/active`` — admin sessions list. Rate-
    limited under the security-read bucket."""

    def test_dispatches_through_gate_and_adapter(self) -> None:
        routes, adapter, _ = _routes_with_stubs()
        handler = MockControllerHandler(path="/api/sessions/active")
        routes.handle_sessions_active(handler)
        assert adapter.dispatch_calls == [
            ("dispatch", "/api/sessions/active"),
        ]
        assert handler.captured.status == 200

    def test_429_when_gate_denies(self) -> None:
        gate = _AlwaysDenyGate()
        routes, adapter, _ = _routes_with_stubs(gate=gate)
        handler = MockControllerHandler(path="/api/sessions/active")
        routes.handle_sessions_active(handler)
        assert handler.captured.status == 429
        body = json.loads(handler.captured.body)
        assert body["error"] == "rate_limit_exceeded"
        # Adapter must not be called when gate denies.
        assert adapter.dispatch_calls == []
        assert gate.denied == 1


class TestSecurityReportRoutes:
    """The three ``/api/security/*`` admin reports
    (failed-logins, new-locations, concurrent)."""

    def test_failed_logins_dispatches(self) -> None:
        routes, adapter, _ = _routes_with_stubs()
        handler = MockControllerHandler(path="/api/security/failed-logins")
        routes.handle_security_failed_logins(handler)
        assert adapter.dispatch_calls == [
            ("dispatch", "/api/security/failed-logins"),
        ]

    def test_new_locations_dispatches(self) -> None:
        routes, adapter, _ = _routes_with_stubs()
        handler = MockControllerHandler(path="/api/security/new-locations")
        routes.handle_security_new_locations(handler)
        assert adapter.dispatch_calls == [
            ("dispatch", "/api/security/new-locations"),
        ]

    def test_concurrent_dispatches(self) -> None:
        routes, adapter, _ = _routes_with_stubs()
        handler = MockControllerHandler(path="/api/security/concurrent")
        routes.handle_security_concurrent(handler)
        assert adapter.dispatch_calls == [
            ("dispatch", "/api/security/concurrent"),
        ]

    def test_failed_logins_429_when_gate_denies(self) -> None:
        gate = _AlwaysDenyGate()
        routes, adapter, _ = _routes_with_stubs(gate=gate)
        handler = MockControllerHandler(path="/api/security/failed-logins")
        routes.handle_security_failed_logins(handler)
        assert handler.captured.status == 429
        assert adapter.dispatch_calls == []


class TestBansRoutes:
    """``/api/bans/users`` + ``/api/bans/ips`` — admin ban lists."""

    def test_users_dispatches(self) -> None:
        routes, adapter, _ = _routes_with_stubs()
        handler = MockControllerHandler(path="/api/bans/users")
        routes.handle_bans_users(handler)
        assert adapter.dispatch_calls == [("dispatch", "/api/bans/users")]

    def test_ips_dispatches(self) -> None:
        routes, adapter, _ = _routes_with_stubs()
        handler = MockControllerHandler(path="/api/bans/ips")
        routes.handle_bans_ips(handler)
        assert adapter.dispatch_calls == [("dispatch", "/api/bans/ips")]

    def test_users_429_when_gate_denies(self) -> None:
        gate = _AlwaysDenyGate()
        routes, adapter, _ = _routes_with_stubs(gate=gate)
        handler = MockControllerHandler(path="/api/bans/users")
        routes.handle_bans_users(handler)
        assert handler.captured.status == 429
        assert adapter.dispatch_calls == []


class TestAuditLogHeadRoute:
    """``GET /api/audit-log/head`` — admin audit-log integrity head."""

    def test_dispatches(self) -> None:
        routes, adapter, _ = _routes_with_stubs()
        handler = MockControllerHandler(path="/api/audit-log/head")
        routes.handle_audit_log_head(handler)
        assert adapter.dispatch_calls == [
            ("dispatch", "/api/audit-log/head"),
        ]

    def test_429_when_gate_denies(self) -> None:
        gate = _AlwaysDenyGate()
        routes, adapter, _ = _routes_with_stubs(gate=gate)
        handler = MockControllerHandler(path="/api/audit-log/head")
        routes.handle_audit_log_head(handler)
        assert handler.captured.status == 429


# ---------------------------------------------------------------------------
# Self-service ``/api/me/*`` route tests — gate-bypass
# ---------------------------------------------------------------------------


class TestMeRoutes:
    """``/api/me/*`` GET routes — caller-self surfaces. Bucket:
    global (rides the POST limit; no admin gate). Tests pin that the
    security-read gate is NOT consulted on these paths — a
    misclassification that put ``/api/me/sessions`` behind the admin
    gate would 429 every operator who refreshed the page faster than
    5 times per second."""

    def test_me_sessions_bypasses_gate(self) -> None:
        gate = _AlwaysDenyGate()
        routes, adapter, _ = _routes_with_stubs(gate=gate)
        handler = MockControllerHandler(path="/api/me/sessions")
        routes.handle_me_sessions(handler)
        # Adapter ran — gate was NOT consulted.
        assert adapter.dispatch_calls == [("dispatch", "/api/me/sessions")]
        assert gate.denied == 0
        assert handler.captured.status == 200

    def test_me_tokens_bypasses_gate(self) -> None:
        gate = _AlwaysDenyGate()
        routes, adapter, _ = _routes_with_stubs(gate=gate)
        handler = MockControllerHandler(path="/api/me/tokens")
        routes.handle_me_tokens(handler)
        assert adapter.dispatch_calls == [("dispatch", "/api/me/tokens")]
        assert gate.denied == 0

    def test_me_mfa_state_bypasses_gate(self) -> None:
        gate = _AlwaysDenyGate()
        routes, adapter, _ = _routes_with_stubs(gate=gate)
        handler = MockControllerHandler(path="/api/me/mfa-state")
        routes.handle_me_mfa_state(handler)
        assert adapter.dispatch_calls == [("dispatch", "/api/me/mfa-state")]
        assert gate.denied == 0

    def test_me_login_history_bypasses_gate(self) -> None:
        gate = _AlwaysDenyGate()
        routes, adapter, _ = _routes_with_stubs(gate=gate)
        handler = MockControllerHandler(
            path="/api/me/login-history?limit=20",
        )
        routes.handle_me_login_history(handler)
        assert adapter.dispatch_calls == [
            ("dispatch", "/api/me/login-history"),
        ]
        assert gate.denied == 0


# ---------------------------------------------------------------------------
# Routing-integration sanity check
# ---------------------------------------------------------------------------


class TestRoutingIntegration:
    """Pin auto-discovery + spec-parity behaviour for the
    sessions-security domain. If a future change accidentally drops
    a handler from the registry, this test fires before the per-route
    tests."""

    def test_all_sessions_security_routes_registered(self) -> None:
        harness = RouteDispatchHarness.with_default_router()
        expected = {
            "/api/sessions/active",
            "/api/security/failed-logins",
            "/api/security/new-locations",
            "/api/security/concurrent",
            "/api/bans/users",
            "/api/bans/ips",
            "/api/audit-log/head",
            "/api/me/sessions",
            "/api/me/tokens",
            "/api/me/mfa-state",
            "/api/me/login-history",
        }
        registered = {
            r.path
            for r in harness._dispatcher._router.registered_routes()
            if r.path in expected
        }
        assert registered == expected, (
            f"Missing sessions-security routes: "
            f"{expected - registered}"
        )

    def test_post_to_sessions_active_returns_method_not_allowed(self) -> None:
        harness = RouteDispatchHarness.with_default_router()
        outcome, _ = harness.try_dispatch("POST", "/api/sessions/active")
        assert outcome == DispatchOutcome.METHOD_NOT_ALLOWED

    def test_post_to_audit_log_head_returns_method_not_allowed(self) -> None:
        harness = RouteDispatchHarness.with_default_router()
        outcome, _ = harness.try_dispatch("POST", "/api/audit-log/head")
        assert outcome == DispatchOutcome.METHOD_NOT_ALLOWED


# ---------------------------------------------------------------------------
# Defence-in-depth security suite
# ---------------------------------------------------------------------------


class TestSecurityDefenceInDepth:
    """Cross-cutting security assertions that don't fit into a
    single per-route test class. Each one pins a contract that, if
    accidentally regressed, would surface as a real-world security
    bug rather than a flaky test."""

    def test_gate_runs_before_adapter_on_every_admin_path(self) -> None:
        """A regression that calls the adapter BEFORE the gate
        would let an attacker exhaust the database connection pool
        before the limiter trips. Tested by pinning that the
        adapter sees zero calls when the gate denies."""
        gate = _AlwaysDenyGate()
        routes, adapter, _ = _routes_with_stubs(gate=gate)
        admin_paths = [
            ("/api/sessions/active", routes.handle_sessions_active),
            ("/api/security/failed-logins",
             routes.handle_security_failed_logins),
            ("/api/security/new-locations",
             routes.handle_security_new_locations),
            ("/api/security/concurrent",
             routes.handle_security_concurrent),
            ("/api/bans/users", routes.handle_bans_users),
            ("/api/bans/ips", routes.handle_bans_ips),
            ("/api/audit-log/head", routes.handle_audit_log_head),
        ]
        for path, fn in admin_paths:
            handler = MockControllerHandler(path=path)
            fn(handler)
            assert handler.captured.status == 429, (
                f"{path}: gate failed to deny"
            )
        # Across every denied call, the adapter saw zero work.
        assert adapter.dispatch_calls == []
        assert gate.denied == len(admin_paths)

    def test_no_static_method_on_route_module(self) -> None:
        """OO-discipline: route handlers must be instance methods,
        not ``@staticmethod``. Static methods bypass the
        constructor-injected dependencies and the dispatch graph
        becomes untestable. Pinned by walking the class and
        asserting no method is a staticmethod."""
        for name in dir(SessionsSecurityGetRoutes):
            if name.startswith("__"):
                continue
            attr = SessionsSecurityGetRoutes.__dict__.get(name)
            assert not isinstance(attr, staticmethod), (
                f"{name} is a staticmethod — must be an instance method"
            )

    def test_constructor_injection_default_does_not_cache(self) -> None:
        """Lazy-cache resolver anti-pattern: the constructor must
        NOT cache default-resolved factories on ``self``. Verified
        by patching the canonical helper symbol AFTER constructing
        the route module and asserting the patch is honoured on
        the next dispatch — caching the default would freeze the
        pre-patch reference."""
        # Use the real adapter (with default helper_factory=None) so
        # the lazy-resolution path runs. Override the gate to allow.
        adapter = _SessionsViewerAdapter()  # default helper_factory
        routes = SessionsSecurityGetRoutes(
            sessions_viewer=adapter,
            security_read_gate=_AlwaysAllowGate(),
        )
        handler = MockControllerHandler(path="/api/sessions/active")

        seen_paths: list[str] = []

        class _Helper:
            def dispatch(self, h: Any, path: str) -> None:
                seen_paths.append(path)
                h._json_response(200, {"sessions": []})

        with patch(
            "media_stack.api.services.security_get_handlers."
            "_SessionVisibilityGetHelper",
            new=_Helper,
        ):
            routes.handle_sessions_active(handler)

        assert seen_paths == ["/api/sessions/active"]
        assert handler.captured.status == 200

# ---------------------------------------------------------------------------
# Unauthenticated dispatch (parity with security_audit suite)
# ---------------------------------------------------------------------------


class TestUnauthenticatedDispatch:
    """The dispatcher does not re-implement auth — the controller's
    ``_check_auth`` middleware runs ahead of dispatch in production.
    Tests here pin that the route ITSELF doesn't bypass that gate
    by writing a 200 with secrets BEFORE the upstream check would
    have a chance to fire.

    The route module's only contract with the wire is: gate, then
    delegate. The legacy helper is responsible for emitting 401 /
    403 — covered by the helper's own test suite."""

    def test_sessions_active_without_auth_runs_gate_and_helper(self) -> None:
        """No headers — the route still calls the gate and
        delegates. The legacy helper translates the missing actor
        into a 401, but that's the helper's contract; pinned by
        the helper tests, not here."""
        adapter = _StubAdapter()
        gate = _AlwaysAllowGate()
        routes = SessionsSecurityGetRoutes(
            sessions_viewer=adapter, security_read_gate=gate,
        )
        handler = MockControllerHandler(
            path="/api/sessions/active", headers={},
        )
        routes.handle_sessions_active(handler)
        # Adapter ran (helper would translate to 401 in production).
        assert adapter.dispatch_calls == [
            ("dispatch", "/api/sessions/active"),
        ]
