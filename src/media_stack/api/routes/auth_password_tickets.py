"""Auth password-ticket GET routes (ADR-0007 Phase 2 wave 7).

One route migrated off the ``handlers_get.handle()`` elif chain:

* ``GET /api/password-tickets/{ticket_id}`` — single-use retrieval
  of a generated plaintext password. Admin-only, rate-limited in
  the shared ``pw-reset`` bucket, audit-logged on every attempt.

The OpenAPI spec already declares the path at line 8318 with the
correct security declarations and 200/403/404/429 responses.
No spec edit needed.

Security preservation — identical to legacy
(``handlers_get._PasswordTicketConsumer``):

* Admin gate: authenticated user must have ``controller_admin`` role
  via user store + roles map. Unknown user or unknown role is
  admitted as admin (RBAC fallback mirrors ``_ControllerRBAC``).
  The env-var admin (``STACK_ADMIN_USERNAME``) is admitted regardless
  of store state.

* Rate limit: shares ``_pw_reset_limiter`` from
  ``media_stack.api.handlers_post``. Imported lazily inside the
  service method so the module import does not trigger
  ``handlers_post``'s module-level side effects at Router build time.

* Audit: appended via ``build_default_service()._audit.append`` on
  every request that passes the admin + rate-limit gates — including
  expired/unknown tickets. Audit failure is swallowed via
  ``log_swallowed`` so the plaintext is never hidden from a
  legitimate admin.

Actor resolution: three tiers matching legacy
``_PasswordTicketConsumer._resolve_actor_username``:
session cookie -> trusted-proxy header -> Basic-auth decode.

Env-var access: ``STACK_ADMIN_USERNAME`` read inside the service
class (services are at the boundary where env reads are acceptable).
"""

from __future__ import annotations

import base64
import os
from http import HTTPStatus
from typing import Any

from media_stack.api.routing import RouteModule, get
from media_stack.core.logging_utils import log_swallowed


_STACK_ADMIN_USERNAME_ENV = "STACK_ADMIN_USERNAME"
_STACK_ADMIN_USERNAME_DEFAULT = "admin"


class PasswordTicketConsumerService:
    """Business logic for ``GET /api/password-tickets/{ticket_id}``.

    Constructor-injects every collaborator with module-default
    fallbacks that preserve the Router's zero-arg auto-discovery.
    Tests pass stubs without monkey-patching.

    Collaborators:
    * ``ticket_store_fn``  -- callable -> ``PasswordTicketStore``.
    * ``user_service_fn``  -- callable -> ``build_default_service()``.
    * ``limiter_fn``       -- callable -> ``_pw_reset_limiter``.
    * ``actor_resolver``   -- callable ``(handler) -> str``.
    * ``admin_username_fn``-- callable ``() -> str`` reading the env.
    """

    def __init__(
        self,
        *,
        ticket_store_fn: Any = None,
        user_service_fn: Any = None,
        limiter_fn: Any = None,
        actor_resolver: Any = None,
        admin_username_fn: Any = None,
    ) -> None:
        self._ticket_store_fn = ticket_store_fn
        self._user_service_fn = user_service_fn
        self._limiter_fn = limiter_fn
        self._actor_resolver = actor_resolver or self._default_resolve_actor
        self._admin_username_fn = (
            admin_username_fn or self._default_admin_username
        )

    # --- default collaborator implementations --------------------------

    def _default_admin_username(self) -> str:
        return (
            os.environ.get(
                _STACK_ADMIN_USERNAME_ENV, _STACK_ADMIN_USERNAME_DEFAULT,
            ) or ""
        ).strip()

    def _default_resolve_actor(self, handler: Any) -> str:
        """Three-tier actor resolution matching legacy
        ``_PasswordTicketConsumer._resolve_actor_username``:
        session-cookie -> trusted-proxy header -> Basic-auth decode.
        """
        from media_stack.api.session_singletons import (
            session_cookie_reader,
            trusted_proxy_auth,
        )
        username = session_cookie_reader.username_for_handler(handler) or ""
        if not username:
            username = trusted_proxy_auth.identity(handler) or ""
        if username:
            return username
        auth_header = ""
        try:
            auth_header = handler.headers.get("Authorization", "") or ""
        except AttributeError:
            return ""
        if auth_header.startswith("Basic "):
            try:
                decoded = base64.b64decode(auth_header[6:]).decode(
                    "utf-8", "replace",
                )
                return decoded.partition(":")[0] or ""
            except (ValueError, UnicodeDecodeError) as exc:
                log_swallowed(exc, context="password-ticket-basic-decode")
                return ""
        return ""

    # --- collaborator accessors ----------------------------------------

    def _ticket_store(self) -> Any:
        if self._ticket_store_fn is not None:
            return self._ticket_store_fn()
        from media_stack.core.auth.users.password_ticket_store import (
            get_default_store,
        )
        return get_default_store()

    def _user_service(self) -> Any:
        if self._user_service_fn is not None:
            return self._user_service_fn()
        from media_stack.core.auth.users.user_service_factory import (
            build_default_service,
        )
        return build_default_service()

    def _limiter(self) -> Any:
        if self._limiter_fn is not None:
            return self._limiter_fn()
        from media_stack.api.services.rate_limiters import (
            _pw_reset_limiter,
        )
        return _pw_reset_limiter

    # --- admin check ---------------------------------------------------

    def _requester_is_admin(self, handler: Any, username: str) -> bool:
        """True when the authenticated user's role carries
        ``controller_admin``.

        Fallback: env-var admin is treated as admin regardless of
        store state. Unknown user or unknown role is also admitted
        (RBAC fallback mirrors ``_ControllerRBAC``).
        """
        env_admin = self._admin_username_fn()
        if username and env_admin and username == env_admin:
            return True
        try:
            svc = self._user_service()
            user = svc._store.get_by_username(username)
            if user is None:
                return True  # unknown user -- RBAC fallback
            role = svc._roles.get(user.role_slug)
            if role is None:
                return True
            return bool(getattr(role, "controller_admin", True))
        except Exception as exc:  # noqa: BLE001
            log_swallowed(exc, context="password-ticket-admin-check")
            return True

    # --- main consume logic --------------------------------------------

    def consume(self, handler: Any, ticket_id: str) -> None:
        """Execute the full consume flow and write the response.

        Gate order: admin check -> rate-limit -> store consume ->
        audit -> respond. Mirrors legacy ``_PasswordTicketConsumer.handle``
        exactly.
        """
        from media_stack.core.auth.users.audit_actions import (
            PASSWORD_TICKET_CONSUMED,
        )

        actor_username = self._actor_resolver(handler)

        if not self._requester_is_admin(handler, actor_username):
            handler._json_response(
                HTTPStatus.FORBIDDEN, {"error": "admin required"},
            )
            return

        limiter = self._limiter()
        if not limiter.allow(client_id=ticket_id or "empty", bucket="pw-reset"):
            handler._json_response(
                HTTPStatus.TOO_MANY_REQUESTS,
                {"error": "rate limit exceeded"},
            )
            return

        store = self._ticket_store()
        # Peek BEFORE consume so the audit entry has the bound user
        # even when the ticket has already expired.
        bound_user = store.peek_user_id(ticket_id) or ""
        plaintext = store.consume(ticket_id)

        svc = self._user_service()
        audit_detail: dict[str, Any] = {
            "ticket_id_len": len(ticket_id),
            "bound_user_id": bound_user,
            "result": "ok" if plaintext else "expired_or_unknown",
        }
        try:
            svc._audit.append(
                actor=actor_username or "anonymous",
                action=PASSWORD_TICKET_CONSUMED,
                target=bound_user or "unknown",
                result="ok" if plaintext else "expired",
                detail=audit_detail,
            )
        except Exception as exc:  # noqa: BLE001
            # Audit failure must not hide the plaintext from a
            # legitimate admin -- same reasoning as legacy.
            log_swallowed(exc, context="password-ticket-audit-append")

        if plaintext is None:
            handler._json_response(
                HTTPStatus.NOT_FOUND,
                {"error": "ticket expired, unknown, or already consumed"},
            )
            return

        handler._json_response(
            HTTPStatus.OK,
            {"password": plaintext, "user_id": bound_user},
        )


class AuthPasswordTicketsGetRoutes(RouteModule):
    """Auth password-ticket GET route.

    The Router auto-discovers + instantiates this class at startup.
    Constructor defaults keep auto-discovery zero-arg; tests pass a
    stub ``PasswordTicketConsumerService`` to avoid touching the real
    ticket store, rate limiter, or audit log.
    """

    def __init__(
        self,
        *,
        consumer_service: PasswordTicketConsumerService | None = None,
    ) -> None:
        self._consumer = (
            consumer_service or PasswordTicketConsumerService()
        )

    @get("/api/password-tickets/{ticket_id}")
    def handle_password_ticket(
        self, handler: Any, *, ticket_id: str,
    ) -> None:
        """Retrieve a generated plaintext password exactly once.

        Admin-only, rate-limited, audit-logged. The ticket is burned
        on first successful consume; subsequent requests for the same
        ``ticket_id`` return 404. Mirrors legacy
        ``_PasswordTicketConsumer.handle`` at
        ``handlers_get.py:2441-2501``.
        """
        self._consumer.consume(handler, ticket_id)


__all__ = [
    "AuthPasswordTicketsGetRoutes",
    "PasswordTicketConsumerService",
]
