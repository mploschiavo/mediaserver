"""User-sessions routes (ADR-0007 Phase 2 wave 8 group 1).

Two routes -- both touching the per-user session list:

* ``GET  /api/users/{user_id}/sessions``                    -- list sessions
* ``POST /api/users/{user_id}/sessions/{session_id}/revoke`` -- revoke one

The GET surface is migrated from the legacy
``handlers_get._UserMgmtGetHelper._dispatch_singleton`` branch (the
``parts[4] == "sessions"`` check). The POST surface is migrated from
``api/services/security_post_handlers.SecurityPostHandlers._revoke_session``
(addressed via the ``_REVOKE_RE`` regex). Both stay 1:1 with their
legacy contracts; the route handlers are thin gateways over the
existing services.

Patterns:

* **Repository** -- ``UserSessionsRepository`` wraps both the
  user-service-factory's ``list_sessions`` call AND the
  ``security_post_handlers.SecurityPostHandlers`` collaborator
  used for the revoke. Routes never reach for module attributes
  mid-method.
* **CSRF** -- POST route uses the shared ``PostMutationGate``;
  GET is exempt by design.
"""

from __future__ import annotations

from http import HTTPStatus
from typing import Any, Callable

from media_stack.api.routes.post_admin_ops import PostMutationGate
from media_stack.api.routes.post_users import ActorResolution
from media_stack.api.routing import RouteModule, get, post
from media_stack.core.auth.users import (
    user_service_factory as _user_service_factory_module,
)


_ERR_LEN = 99


class UserSessionsRepository:
    """Repository over the user-service ``list_sessions`` plus the
    security-post handler dispatcher.

    Resolves the underlying collaborators via FRESH module attribute
    lookup so ``mock.patch`` flips them per-test (the wave-3+4
    lazy-cache anti-pattern guard).
    """

    def __init__(
        self,
        *,
        service_builder: Callable[[], Any] | None = None,
        security_dispatcher: Any | None = None,
    ) -> None:
        self._explicit_service = service_builder
        self._explicit_security = security_dispatcher

    def list_sessions(self, user_id: str) -> list[dict[str, Any]]:
        return self._service().list_sessions(user_id)

    def dispatch_revoke(
        self,
        handler: Any,
        path: str,
        body: dict[str, Any],
        actor: Any,
    ) -> None:
        self._security().dispatch(handler, path, body, actor)

    # --- internals ---------------------------------------------------

    def _service(self) -> Any:
        if self._explicit_service is not None:
            return self._explicit_service()
        return _user_service_factory_module.build_default_service()

    def _security(self) -> Any:
        if self._explicit_security is not None:
            return self._explicit_security
        from media_stack.api.services.security_post_handlers import (
            _security_post_handlers,
        )
        return _security_post_handlers


class UserSessionsRoutes(RouteModule):
    """GET + POST routes for per-user session management.

    Constructor defaults preserve the Router's zero-arg auto-
    discovery while letting tests swap any collaborator.
    """

    def __init__(
        self,
        *,
        mutation_gate: PostMutationGate | None = None,
        repository: UserSessionsRepository | None = None,
        actor_resolution: ActorResolution | None = None,
    ) -> None:
        self._gate = mutation_gate or PostMutationGate()
        self._repo = repository or UserSessionsRepository()
        self._actor = actor_resolution or ActorResolution()

    def _gated(self, handler: Any) -> bool:
        if not self._gate.verify(handler):
            self._gate.reject(handler)
            return False
        return True

    @get("/api/users/{user_id}/sessions")
    def handle_user_sessions_list(
        self, handler: Any, *, user_id: str,
    ) -> None:
        """List all live sessions for a user (admin surface)."""
        sessions = self._repo.list_sessions(user_id)
        handler._json_response(
            HTTPStatus.OK, {"sessions": sessions},
        )

    @post("/api/users/{user_id}/sessions/{session_id}/revoke")
    def handle_session_revoke(
        self,
        handler: Any,
        *,
        user_id: str,
        session_id: str,
    ) -> None:
        """Admin-revoke a single session.

        Delegates to ``SecurityPostHandlers.dispatch`` so the
        idempotency-key + audit-log + event-bus wiring is preserved
        without re-implementing it inline.
        """
        if not self._gated(handler):
            return
        body = handler._read_json_body() or {}
        actor = self._actor.resolve(handler, body)
        # The security dispatcher keys off ``handler.path``; the
        # path-param-bearing path is what we received already, so
        # forwarding ``handler.path`` is correct.
        self._repo.dispatch_revoke(handler, handler.path, body, actor)


__all__ = [
    "UserSessionsRepository",
    "UserSessionsRoutes",
]
