"""Tests for ``api/routes/media_integrity_get.py``
(ADR-0007 Phase 2 wave 5).

Two routes lifted off the legacy
``_media_integrity_handlers.dispatch_get`` branch in
``handlers_get.py`` — ``/api/media-integrity/status`` and
``/api/media-integrity/progress``.

Each route gets:

* a happy-path test asserting the canonical body shape (delegated
  verbatim to the constructor-injected ``MediaIntegrityService``
  stub);
* a service-not-configured test pinning the legacy 503 envelope;
* an unauthenticated-actor test pinning the legacy 401 envelope.

Plus a routing-integration sanity check: both paths must be
discovered + registered by the production ``Router`` after this
module ships, and the registered methods must map to the route
class. Method-Not-Allowed is asserted against ``POST`` to one of
the GET-only paths to pin the dispatcher's behaviour around the
spec parity check.

Patch points:

* The ``MediaIntegrityService`` is reached through a
  constructor-injected ``_LegacyHandlerServiceProvider``. Tests use
  a ``_StubServiceProvider`` to inject the desired service / None
  without touching the production module-level singleton.
* The ``HandlerActorResolverFactory`` is constructor-injected too;
  tests pass a ``_StubActorResolver`` that returns whichever
  ``Actor`` the test scenario needs (authenticated / anonymous).

Tests for the production wiring (``with_default_router``) patch
``session_cookie_reader.username_for_handler`` and
``trusted_proxy_auth.identity`` to drive the default
``HandlerActorResolverFactory`` along the authenticated path; this
keeps the production path covered without forking the route module.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

from media_stack.api.routes.media_integrity_get import (
    MediaIntegrityGetRoutes,
    _LegacyHandlerServiceProvider,
)
from media_stack.api.routing import (
    DispatchOutcome,
    Router,
    RouterDispatcher,
)
from media_stack.core.auth.authz import Actor, AuthorizationError
from tests.unit.api.routes._helpers import (
    MockControllerHandler,
    RouteDispatchHarness,
)


# ---------------------------------------------------------------------
# Stubs
# ---------------------------------------------------------------------


class _StubMediaIntegrityService:
    """Test double for ``MediaIntegrityService`` — captures the calls
    the route makes and returns canned payloads.

    Only the two read-only entry points the route exercises are
    modelled (``status`` / ``get_progress``); the POST surface lives
    on the legacy handler and is out of scope for this module.
    """

    def __init__(
        self,
        *,
        status_payload: dict | None = None,
        progress_payload: dict | None = None,
    ) -> None:
        self._status_payload = status_payload or {}
        self._progress_payload = progress_payload or {"in_progress": False}
        self.status_calls = 0
        self.progress_calls = 0

    def status(self) -> dict:
        self.status_calls += 1
        return dict(self._status_payload)

    def get_progress(self) -> dict:
        self.progress_calls += 1
        return dict(self._progress_payload)


class _StubServiceProvider:
    """``_LegacyHandlerServiceProvider``-shaped test double.

    Returns whichever object (or ``None``) the test scenario pins.
    The route's ``service_provider.get()`` call is the only contact
    point we need to satisfy.
    """

    def __init__(self, service: Any) -> None:
        self._service = service

    def get(self) -> Any:
        return self._service


class _StubActorResolver:
    """``HandlerActorResolverFactory``-shaped test double.

    Either returns the canned ``Actor`` or raises
    ``AuthorizationError`` to drive the unauthenticated branch.
    """

    def __init__(
        self,
        *,
        actor: Actor | None = None,
        raise_unauthorized: bool = False,
    ) -> None:
        self._actor = actor
        self._raise_unauthorized = raise_unauthorized
        self.resolve_calls = 0

    def resolve(self, handler: Any, body: dict | None = None) -> Actor:
        self.resolve_calls += 1
        if self._raise_unauthorized:
            raise AuthorizationError("authentication_required")
        # ``HandlerActorResolverFactory.resolve`` always returns an
        # ``Actor`` in the production path; the route inspects
        # ``actor.is_authenticated`` to decide whether to proceed.
        if self._actor is None:
            return Actor(username="")
        return self._actor


def _authenticated_actor() -> Actor:
    return Actor(username="alice", is_admin=False)


def _anonymous_actor() -> Actor:
    return Actor(username="")


# ---------------------------------------------------------------------
# Direct-route tests (instance under test, no Router indirection)
# ---------------------------------------------------------------------


class TestStatusRoute:
    """``GET /api/media-integrity/status`` — last-pass snapshot."""

    def test_returns_status_payload_for_authenticated_actor(self) -> None:
        payload = {
            "last_enforce": {"ts": "2026-04-25T14:30:22Z", "detail": {}},
            "last_reconcile": {"ts": "", "detail": {}},
            "policy_version": 3,
            "servarr_adapters": ["radarr", "sonarr"],
            "bazarr_present": True,
            "missing_api_keys": [],
        }
        service = _StubMediaIntegrityService(status_payload=payload)
        routes = MediaIntegrityGetRoutes(
            service_provider=_StubServiceProvider(service),
            actor_resolver=_StubActorResolver(actor=_authenticated_actor()),
        )
        handler = MockControllerHandler()

        routes.handle_status(handler)

        assert handler.captured.status == 200
        body = json.loads(handler.captured.body)
        assert body == payload
        assert service.status_calls == 1

    def test_returns_503_when_service_not_configured(self) -> None:
        routes = MediaIntegrityGetRoutes(
            service_provider=_StubServiceProvider(None),
            actor_resolver=_StubActorResolver(actor=_authenticated_actor()),
        )
        handler = MockControllerHandler()

        routes.handle_status(handler)

        assert handler.captured.status == 503
        body = json.loads(handler.captured.body)
        assert body == {"error": "media-integrity service not configured"}

    def test_returns_401_for_anonymous_actor(self) -> None:
        service = _StubMediaIntegrityService(status_payload={"x": 1})
        resolver = _StubActorResolver(actor=_anonymous_actor())
        routes = MediaIntegrityGetRoutes(
            service_provider=_StubServiceProvider(service),
            actor_resolver=resolver,
        )
        handler = MockControllerHandler()

        routes.handle_status(handler)

        assert handler.captured.status == 401
        body = json.loads(handler.captured.body)
        assert body == {"error": "authentication required"}
        # Service must not be reached on an unauthenticated request.
        assert service.status_calls == 0

    def test_returns_401_when_resolver_raises_authorization_error(
        self,
    ) -> None:
        """Pin the narrow-catch behaviour: only ``AuthorizationError``
        from the resolver maps to a 401. Anything else propagates to
        the dispatcher's 500 handler — see
        ``test_unexpected_resolver_error_propagates`` for that pin.
        """
        service = _StubMediaIntegrityService()
        routes = MediaIntegrityGetRoutes(
            service_provider=_StubServiceProvider(service),
            actor_resolver=_StubActorResolver(raise_unauthorized=True),
        )
        handler = MockControllerHandler()

        routes.handle_status(handler)

        assert handler.captured.status == 401
        assert service.status_calls == 0


class TestProgressRoute:
    """``GET /api/media-integrity/progress`` — in-flight snapshot."""

    def test_returns_idle_progress_payload(self) -> None:
        service = _StubMediaIntegrityService(
            progress_payload={"in_progress": False},
        )
        routes = MediaIntegrityGetRoutes(
            service_provider=_StubServiceProvider(service),
            actor_resolver=_StubActorResolver(actor=_authenticated_actor()),
        )
        handler = MockControllerHandler()

        routes.handle_progress(handler)

        assert handler.captured.status == 200
        body = json.loads(handler.captured.body)
        assert body == {"in_progress": False}
        assert service.progress_calls == 1

    def test_returns_running_progress_payload(self) -> None:
        running_payload = {
            "in_progress": True,
            "op": "reconcile",
            "phase": "running",
            "started_at": "2026-04-25T14:30:22Z",
        }
        service = _StubMediaIntegrityService(progress_payload=running_payload)
        routes = MediaIntegrityGetRoutes(
            service_provider=_StubServiceProvider(service),
            actor_resolver=_StubActorResolver(actor=_authenticated_actor()),
        )
        handler = MockControllerHandler()

        routes.handle_progress(handler)

        assert handler.captured.status == 200
        body = json.loads(handler.captured.body)
        assert body == running_payload

    def test_returns_503_when_service_not_configured(self) -> None:
        routes = MediaIntegrityGetRoutes(
            service_provider=_StubServiceProvider(None),
            actor_resolver=_StubActorResolver(actor=_authenticated_actor()),
        )
        handler = MockControllerHandler()

        routes.handle_progress(handler)

        assert handler.captured.status == 503
        body = json.loads(handler.captured.body)
        assert body == {"error": "media-integrity service not configured"}

    def test_returns_401_for_anonymous_actor(self) -> None:
        service = _StubMediaIntegrityService()
        routes = MediaIntegrityGetRoutes(
            service_provider=_StubServiceProvider(service),
            actor_resolver=_StubActorResolver(actor=_anonymous_actor()),
        )
        handler = MockControllerHandler()

        routes.handle_progress(handler)

        assert handler.captured.status == 401
        assert service.progress_calls == 0


class TestUnexpectedResolverError:
    """The resolver's narrow-catch is intentional — anything outside
    ``AuthorizationError`` propagates so the dispatcher's 500 handler
    sees a real failure instead of an invented 401."""

    def test_unexpected_resolver_error_propagates(self) -> None:
        class _ExplodingResolver:
            def resolve(self, handler: Any, body: dict | None = None) -> Actor:
                raise RuntimeError("session store corrupt")

        service = _StubMediaIntegrityService()
        routes = MediaIntegrityGetRoutes(
            service_provider=_StubServiceProvider(service),
            actor_resolver=_ExplodingResolver(),
        )
        handler = MockControllerHandler()

        try:
            routes.handle_status(handler)
        except RuntimeError as exc:
            assert "session store corrupt" in str(exc)
        else:
            raise AssertionError(
                "expected RuntimeError to propagate past the route",
            )


# ---------------------------------------------------------------------
# Service-provider default
# ---------------------------------------------------------------------


class TestLegacyHandlerServiceProvider:
    """The default provider reads off the legacy handler's module-
    level ``_instance``. Pin both branches (service set / unset) so
    a future refactor that breaks the read surfaces here first."""

    def test_returns_none_when_legacy_service_unset(self) -> None:
        from media_stack.api.services import media_integrity_handlers
        with patch.object(
            media_integrity_handlers._instance, "_service", None,
        ):
            assert _LegacyHandlerServiceProvider().get() is None

    def test_returns_legacy_service_when_set(self) -> None:
        from media_stack.api.services import media_integrity_handlers
        sentinel = object()
        with patch.object(
            media_integrity_handlers._instance, "_service", sentinel,
        ):
            assert _LegacyHandlerServiceProvider().get() is sentinel


# ---------------------------------------------------------------------
# Router-integration sanity
# ---------------------------------------------------------------------


class TestRoutingIntegration:
    """Pin auto-discovery + spec parity for the media-integrity GET
    domain. Production wiring must register both paths through the
    default ``Router``; if a future refactor accidentally drops the
    class, this fires before any per-route test does."""

    def test_both_routes_registered(self) -> None:
        harness = RouteDispatchHarness.with_default_router()
        expected = {
            "/api/media-integrity/status",
            "/api/media-integrity/progress",
        }
        registered = {
            r.path
            for r in harness._dispatcher._router.registered_routes()
            if r.path in expected
        }
        assert registered == expected, (
            f"Missing media-integrity GET routes: {expected - registered}"
        )

    def test_post_to_status_path_returns_method_not_allowed(self) -> None:
        """``/api/media-integrity/status`` is GET-only in the spec —
        POST against it returns 405 even though POST is a verb the
        ``/api/media-integrity/reconcile`` neighbour does declare.
        """
        harness = RouteDispatchHarness.with_default_router()
        outcome, _ = harness.try_dispatch(
            "POST", "/api/media-integrity/status",
        )
        assert outcome == DispatchOutcome.METHOD_NOT_ALLOWED

    def test_dispatch_via_default_router_authenticated(self) -> None:
        """End-to-end: with the legacy service singleton mocked to
        a stub and the actor-resolver patched to return an
        authenticated user, ``with_default_router`` should round-trip
        a GET through the production wiring and emit the stub's
        payload."""
        from media_stack.api.services import media_integrity_handlers

        stub = _StubMediaIntegrityService(
            status_payload={"policy_version": 7},
        )
        with patch.object(
            media_integrity_handlers._instance, "_service", stub,
        ), patch(
            "media_stack.api.routes.media_integrity_get."
            "HandlerActorResolverFactory.resolve",
            return_value=_authenticated_actor(),
        ):
            harness = RouteDispatchHarness.with_default_router()
            response = harness.dispatch(
                "GET", "/api/media-integrity/status",
            )

        assert response.status == 200
        body = json.loads(response.body)
        assert body == {"policy_version": 7}

    def test_dispatch_via_default_router_anonymous_returns_401(
        self,
    ) -> None:
        """Same end-to-end flow with the resolver returning an
        anonymous actor — the route must emit 401 even when the
        legacy service singleton IS wired."""
        from media_stack.api.services import media_integrity_handlers

        stub = _StubMediaIntegrityService()
        with patch.object(
            media_integrity_handlers._instance, "_service", stub,
        ), patch(
            "media_stack.api.routes.media_integrity_get."
            "HandlerActorResolverFactory.resolve",
            return_value=_anonymous_actor(),
        ):
            harness = RouteDispatchHarness.with_default_router()
            response = harness.dispatch(
                "GET", "/api/media-integrity/progress",
            )

        assert response.status == 401
        assert stub.progress_calls == 0


# ---------------------------------------------------------------------
# Custom-router integration (proves constructor injection works
# end-to-end without touching the default singleton)
# ---------------------------------------------------------------------


class TestCustomRouterIntegration:
    """Build a Router that auto-discovers route modules but, after
    discovery, swap the constructor-injected collaborators on our
    class. Proves the route module's seams hold up under the
    production dispatch path without needing the default singleton.
    """

    def test_status_routes_through_custom_router_with_stubs(self) -> None:
        """We can't easily replace the auto-discovered instance after
        the fact, so we verify the same outcome by patching the
        legacy module's ``_instance._service`` plus the resolver and
        invoking through a freshly-built ``Router``. This exercises
        ``Router.match`` -> ``RouterDispatcher.try_dispatch`` -> the
        registered handler end-to-end with a non-singleton router.
        """
        from media_stack.api.services import media_integrity_handlers

        stub = _StubMediaIntegrityService(
            status_payload={"policy_version": 11, "missing_api_keys": []},
        )
        with patch.object(
            media_integrity_handlers._instance, "_service", stub,
        ), patch(
            "media_stack.api.routes.media_integrity_get."
            "HandlerActorResolverFactory.resolve",
            return_value=_authenticated_actor(),
        ):
            router = Router()
            dispatcher = RouterDispatcher(router)
            handler = MockControllerHandler(
                path="/api/media-integrity/status",
            )
            outcome = dispatcher.try_dispatch(
                "GET", "/api/media-integrity/status", handler,
            )

        assert outcome == DispatchOutcome.HANDLED
        assert handler.captured.status == 200
        body = json.loads(handler.captured.body)
        assert body == {"policy_version": 11, "missing_api_keys": []}


# ---------------------------------------------------------------------
# Sanity: the route class is a no-arg-instantiable RouteModule.
# This is the contract Router._collect_route_specs relies on; any
# regression breaking it would surface here before the integration
# test runs.
# ---------------------------------------------------------------------


class TestRouteModuleContract:
    def test_class_is_instantiable_with_no_args(self) -> None:
        """Pin the no-arg constructor — Router auto-discovery
        instantiates with ``module_class()``; a future refactor that
        adds a required positional arg would break startup."""
        instance = MediaIntegrityGetRoutes()
        # Both collaborators must materialize to non-None defaults.
        assert instance._service_provider is not None
        assert instance._actor_resolver is not None

    def test_no_arg_instance_is_a_route_module_subclass(self) -> None:
        from media_stack.api.routing import RouteModule
        assert isinstance(MediaIntegrityGetRoutes(), RouteModule)


# ---------------------------------------------------------------------
# Reuse the SimpleNamespace pattern for handlers so a future state-
# read in the route surfaces here. Currently the route doesn't read
# ``handler.state`` (the service holds all relevant state), so this
# test guards against a regression that would silently start
# reading off ``state`` without updating the test fixtures.
# ---------------------------------------------------------------------


class TestHandlerStateIsolation:
    def test_handler_state_is_not_read_by_routes(self) -> None:
        """If a future refactor accidentally starts reading
        ``handler.state.something`` here, this test fails because
        the state is a sentinel that explodes on attribute access.
        """

        class _SentinelState:
            def __getattr__(self, name: str) -> Any:
                raise AssertionError(
                    f"route should not read handler.state.{name}",
                )

        service = _StubMediaIntegrityService(status_payload={"ok": True})
        routes = MediaIntegrityGetRoutes(
            service_provider=_StubServiceProvider(service),
            actor_resolver=_StubActorResolver(actor=_authenticated_actor()),
        )
        handler = MockControllerHandler(state=_SentinelState())
        # Both routes must succeed without poking ``state``.
        routes.handle_status(handler)
        assert handler.captured.status == 200
        handler2 = MockControllerHandler(state=_SentinelState())
        routes.handle_progress(handler2)
        assert handler2.captured.status == 200


# Silence unused-import warnings for SimpleNamespace until a future
# state-shaped test materialises; keep the import to mirror the
# sibling test modules' shape.
_ = SimpleNamespace
