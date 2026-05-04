"""Identity-resolution service for POST handlers.

Lifted from ``media_stack.api.handlers_post`` during ADR-0007 Phase 2
Phase E (legacy-handler retirement).

This module owns the request-context actor resolver used by every
POST route that needs to record "who triggered this mutation":
session cookie -> trusted-proxy ``Remote-User`` header -> body
``_actor`` field. Returning a typed :class:`Actor` lets the user
service / token store / audit log persist a real username instead
of the literal placeholder ``"controller-ui"`` the bootstrap
default would yield.
"""

from __future__ import annotations

from typing import Any

from media_stack.api.actor_resolver import ActorResolver as _ActorResolver
from media_stack.api.session_singletons import (
    session_cookie_reader as _session_cookie_reader,
    trusted_proxy_auth as _trusted_proxy_auth,
)
from media_stack.core.auth.authz import Actor
from media_stack.core.auth.users.user_service_factory import (
    build_default_service,
)


class HandlerActorResolver:
    """Lazy wrapper around :class:`ActorResolver`.

    Mirrors the GET-side factory in ``security_get_deps.py``. POST
    bodies on /me-style mutations (token mint, session revoke,
    "this wasn't me") rarely carry an explicit ``_actor`` field --
    the caller is acting on themselves and the controller is
    expected to figure out who they are from the request context.
    Without a hint, the underlying :class:`ActorResolver` falls back
    to the literal placeholder ``"controller-ui"``, which then
    lands as ``actor.username`` and gets persisted by callees
    (token store, audit log) under the wrong identity. Pre-resolve
    the caller's real username from the session cookie first, then
    the trusted-proxy ``Remote-User`` header, and only inject it
    into ``body`` when the caller didn't already supply one
    (preserving admin impersonation flows that pass ``_actor``
    explicitly).

    A bare ``ActorResolver(build_service=build_default_service)``
    captures the import-time name and sails past a ``mock.patch``
    of ``build_default_service`` / ``_trusted_proxy_auth``; this
    wrapper does a fresh module-attribute lookup per call so the
    test harness's patches still take effect.
    """

    def resolve(self, handler: Any, body: dict) -> Actor:
        impl = _ActorResolver(
            build_service=build_default_service,
            client_ip_for=_trusted_proxy_auth.client_ip,
        )
        merged = dict(body or {})
        if not str(merged.get("_actor", "") or "").strip():
            identity = self._identity_from_request(handler)
            if identity:
                merged["_actor"] = identity
        return impl.resolve(handler, merged)

    def _identity_from_request(self, handler: Any) -> str:
        """Return the authenticated username on this request, or ''.

        Resolution order: session cookie wins, then the trusted-
        proxy ``Remote-User`` header (Authelia via Envoy ext_authz).
        Returning '' lets the underlying resolver fall back to its
        bootstrap default for the rare unauthenticated POST that
        still reaches this layer (e.g. login itself).
        """
        try:
            cookie_user = _session_cookie_reader.username_for_handler(
                handler,
            )
        except Exception:  # noqa: BLE001
            cookie_user = ""
        if cookie_user:
            return cookie_user
        try:
            return str(_trusted_proxy_auth.identity(handler) or "")
        except Exception:  # noqa: BLE001
            return ""


# Module-level singleton for callers that want a stable instance --
# the resolver is stateless so a single instance is safe to share.
_actor_resolver = HandlerActorResolver()


__all__ = ["HandlerActorResolver", "_actor_resolver"]
