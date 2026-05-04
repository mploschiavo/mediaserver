"""Self-service + emergency POST routes (ADR-0007 Phase 2 wave 8 group 1).

Three routes lifted off the legacy chain
``handlers_post.PostRequestHandler._handle_security_post`` ->
``api.services.security_post_handlers.SecurityPostHandlers.dispatch``:

* ``POST /api/me/revoke-others``       -- self-revoke other sessions
* ``POST /api/me/this-wasnt-me``       -- credential-stuffing escape hatch
* ``POST /api/emergency-revoke-all``   -- admin nuclear button

The route handlers are thin gateways over the existing
:class:`SecurityPostHandlers` collaborator -- the idempotency-key
cache, audit-log writes, and event-bus publishing already live
there and stay 1:1 with the legacy contract.

Patterns:

* **Repository / Adapter** -- ``SecurityHandlersRepository`` wraps
  :class:`SecurityPostHandlers` so the route module collaborates
  with an injected dispatcher object. Default path resolves the
  module-level singleton via fresh import.
* **CSRF** -- shared ``PostMutationGate`` enforces double-submit.
* **Authz** -- the security handlers themselves enforce
  admin-only / authenticated-only gates per route; this module
  doesn't second-guess that.
"""

from __future__ import annotations

from typing import Any

from media_stack.api.routes.post_admin_ops import PostMutationGate
from media_stack.api.routes.post_users import ActorResolution
from media_stack.api.routing import RouteModule, post


class SecurityHandlersRepository:
    """Adapter onto :class:`SecurityPostHandlers`.

    The route module dispatches via this collaborator so tests
    can swap the underlying handler with a stub without
    monkeypatching the singleton module attribute.
    """

    def __init__(self, dispatcher: Any | None = None) -> None:
        self._explicit = dispatcher

    def dispatch(
        self,
        handler: Any,
        path: str,
        body: dict[str, Any],
        actor: Any,
    ) -> None:
        self._dispatcher().dispatch(handler, path, body, actor)

    def _dispatcher(self) -> Any:
        if self._explicit is not None:
            return self._explicit
        from media_stack.api.services.security_post_handlers import (
            _security_post_handlers,
        )
        return _security_post_handlers


class MePostRoutes(RouteModule):
    """Self-service + emergency POST routes."""

    def __init__(
        self,
        *,
        mutation_gate: PostMutationGate | None = None,
        repository: SecurityHandlersRepository | None = None,
        actor_resolution: ActorResolution | None = None,
    ) -> None:
        self._gate = mutation_gate or PostMutationGate()
        self._repo = repository or SecurityHandlersRepository()
        self._actor = actor_resolution or ActorResolution()

    def _gated(self, handler: Any) -> bool:
        if not self._gate.verify(handler):
            self._gate.reject(handler)
            return False
        return True

    def _delegate(self, handler: Any, path: str) -> None:
        body = handler._read_json_body() or {}
        actor = self._actor.resolve(handler, body)
        self._repo.dispatch(handler, path, body, actor)

    @post("/api/me/revoke-others")
    def handle_me_revoke_others(self, handler: Any) -> None:
        """Revoke every session for the caller EXCEPT the one
        making this request. Authentication required (the
        underlying handler enforces this); CSRF gated upstream."""
        if not self._gated(handler):
            return
        self._delegate(handler, "/api/me/revoke-others")

    @post("/api/me/this-wasnt-me")
    def handle_me_this_wasnt_me(self, handler: Any) -> None:
        """Anomaly escape hatch -- revoke ALL sessions for the
        caller, force a password rotation, and clear the session
        cookie. Used when the user spots a login from an unknown
        IP/UA in their history panel."""
        if not self._gated(handler):
            return
        self._delegate(handler, "/api/me/this-wasnt-me")

    @post("/api/emergency-revoke-all")
    def handle_emergency_revoke_all(self, handler: Any) -> None:
        """Admin-only nuclear button -- revoke ALL sessions across
        ALL users, rotate token-signing secrets, force admin
        password rotation. Requires a 5+ char ``reason`` field
        for the audit trail."""
        if not self._gated(handler):
            return
        self._delegate(handler, "/api/emergency-revoke-all")


__all__ = [
    "MePostRoutes",
    "SecurityHandlersRepository",
]
