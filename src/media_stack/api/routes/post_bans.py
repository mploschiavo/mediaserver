"""Bans POST routes (ADR-0007 Phase 2 wave 8 group 2).

Migrates the four ban add/remove endpoints off the
``handlers_post.handle()`` elif chain onto the OpenAPI Router.
The legacy chain dispatched these through
``SecurityPostHandlers.dispatch`` after admin/idempotency/audit
gating — this module preserves that pipeline by delegating the
business logic back to the same handler. The route layer is
responsible for:

* CSRF re-application (the legacy ``_global_preflight`` gate is
  bypassed when the Router dispatches).
* Body parsing.
* Actor resolution via the same ``_HandlerActorResolverFactory``
  the legacy chain used.
* Forwarding the path/body/actor triple to
  ``SecurityPostHandlers.dispatch`` which owns admin checks,
  audit logs, idempotency cache, and event publishing.

Routes:

* ``POST /api/bans/ips``                          — add IP ban.
* ``POST /api/bans/ips/{cidr}/remove``            — remove IP ban.
* ``POST /api/bans/users``                        — add user ban.
* ``POST /api/bans/users/{username}/remove``      — remove user ban.

OO discipline:

* ``BansPostRoutes`` is a ``RouteModule`` subclass with instance
  methods only. Constructor-injects the security-post handler +
  actor resolver + mutation gate so tests can swap any
  collaborator without monkey-patching.

Anti-pattern guard rails:

* No lazy-cache resolver shape — the security-post-handler /
  actor-resolver collaborators are resolved fresh per request
  via the constructor-injected factory callables.
* Path params (``cidr`` / ``username``) flow through the Router's
  regex capture; the handler skips the legacy ``path.split("/")``
  re-parsing.
"""

from __future__ import annotations

from typing import Any, Callable

from media_stack.api.routes.post_admin_ops import PostMutationGate
from media_stack.api.routing import RouteModule, post


class _SecurityPostHandlerProvider:
    """Adapter that resolves the shared ``SecurityPostHandlers``
    singleton with a fresh module-attribute lookup each call so
    a ``mock.patch`` of the canonical symbol takes effect.
    """

    def __init__(
        self,
        getter: Callable[[], Any] | None = None,
    ) -> None:
        self._getter = getter

    def get(self) -> Any:
        if self._getter is not None:
            return self._getter()
        from media_stack.api.services.security_post_handlers import (
            _security_post_handlers,
        )
        return _security_post_handlers


class _ActorResolverProvider:
    """Adapter onto the ``_HandlerActorResolverFactory`` the legacy
    chain uses — kept module-default fall-through so the
    Router's zero-arg auto-discovery works.
    """

    def __init__(
        self,
        resolver: Any = None,
    ) -> None:
        self._resolver = resolver

    def resolve(self, handler: Any, body: dict[str, Any]) -> Any:
        if self._resolver is not None:
            return self._resolver.resolve(handler, body)
        from media_stack.api.handlers_post import _actor_resolver
        return _actor_resolver.resolve(handler, body)


class BansPostRoutes(RouteModule):
    """Bans (user + IP) add/remove POST routes.

    The Router auto-discovers + instantiates this class + walks
    its tagged methods at startup. Constructor defaults keep
    auto-discovery zero-arg while letting tests swap any
    collaborator.
    """

    def __init__(
        self,
        *,
        mutation_gate: PostMutationGate | None = None,
        security_handler_provider: _SecurityPostHandlerProvider | None = None,
        actor_resolver_provider: _ActorResolverProvider | None = None,
    ) -> None:
        self._gate = mutation_gate or PostMutationGate()
        self._security_handler = (
            security_handler_provider or _SecurityPostHandlerProvider()
        )
        self._actor_resolver = (
            actor_resolver_provider or _ActorResolverProvider()
        )

    # --- gate helper ---------------------------------------------------

    def _gated(self, handler: Any) -> bool:
        if not self._gate.verify(handler):
            self._gate.reject(handler)
            return False
        return True

    def _dispatch_to_security(
        self, handler: Any, path: str,
    ) -> None:
        """Re-create the legacy ``_handle_security_post`` flow:
        read body, resolve actor, dispatch to
        ``SecurityPostHandlers.dispatch``.
        """
        body = handler._read_json_body() or {}
        actor = self._actor_resolver.resolve(handler, body)
        self._security_handler.get().dispatch(
            handler, path, body, actor,
        )

    # --- routes --------------------------------------------------------

    @post("/api/bans/ips")
    def handle_add_ip_ban(self, handler: Any) -> None:
        """Add an IP/CIDR ban.

        Body: ``{cidr: str, reason?: str, reason_detail?: str,
        expires_at?: str}``. Admin-only; idempotency honored via
        the ``Idempotency-Key`` header.
        """
        if not self._gated(handler):
            return
        self._dispatch_to_security(handler, "/api/bans/ips")

    @post("/api/bans/ips/{cidr}/remove")
    def handle_remove_ip_ban(
        self, handler: Any, *, cidr: str,
    ) -> None:
        """Remove an IP/CIDR ban by CIDR. Admin-only."""
        if not self._gated(handler):
            return
        # ``SecurityPostHandlers._route`` recognises the path via
        # ``_IBAN_REM_RE`` — pass the canonical path so the regex
        # match captures ``cidr``. The Router has already extracted
        # the param and bound it as a kwarg; re-emitting the
        # canonical path keeps the security handler's pattern
        # contract intact.
        self._dispatch_to_security(
            handler, f"/api/bans/ips/{cidr}/remove",
        )

    @post("/api/bans/users")
    def handle_add_user_ban(self, handler: Any) -> None:
        """Add a user ban.

        Body: ``{username: str, reason?: str, reason_detail?: str,
        expires_at?: str}``. Admin-only.
        """
        if not self._gated(handler):
            return
        self._dispatch_to_security(handler, "/api/bans/users")

    @post("/api/bans/users/{username}/remove")
    def handle_remove_user_ban(
        self, handler: Any, *, username: str,
    ) -> None:
        """Remove a user ban by username. Admin-only."""
        if not self._gated(handler):
            return
        self._dispatch_to_security(
            handler, f"/api/bans/users/{username}/remove",
        )


__all__ = [
    "BansPostRoutes",
    "_ActorResolverProvider",
    "_SecurityPostHandlerProvider",
]
