"""User-management POST routes (ADR-0007 Phase 2 wave 8 group 1).

Migrates the eight user-CRUD POST surfaces off the legacy
``handlers_post.PostRequestHandler._dispatch_user_mgmt`` chain onto
the OpenAPI Router:

* ``POST /api/users``                                   -- create user
* ``POST /api/users-bulk-import``                       -- bulk import
* ``POST /api/users-reconcile/import``                  -- import orphan
* ``POST /api/users-reconcile/unlink``                  -- unlink ghost
* ``POST /api/users/{user_id}/delete``                  -- soft delete
* ``POST /api/users/{user_id}/reset-password``          -- reset pw
* ``POST /api/users/{user_id}/revoke-sessions``         -- cross-provider revoke
* ``POST /api/users/{user_id}/role``                    -- set role
* ``POST /api/users/{user_id}/state``                   -- set state

Implementation patterns:

* **Repository** -- ``UserMgmtRepository`` wraps the
  ``UserService`` factory + ``_UserMgmtPostHelper`` callsites
  behind a single dependency-inverted surface. Routes never reach
  for module-level helpers.
* **Strategy / Adapter** -- ``ActorResolution`` builds an
  :class:`Actor` per request via the existing :class:`ActorResolver`,
  matching the shape ``post_user_resources`` already uses.
* **CSRF** -- shared ``PostMutationGate`` from ``post_admin_ops``
  enforces double-submit on every route here. Routes migrated to
  the Router bypass server.py's ``_global_preflight``, so the gate
  is the only line of defence.
* **Narrow ``except``** -- only :class:`UserServiceError` (policy
  violations) is surfaced as 400; anything else propagates so the
  dispatcher's top-level guard records it.
"""

from __future__ import annotations

from http import HTTPStatus
from typing import Any, Callable

from media_stack.api import session_singletons as _session_singletons_module
from media_stack.api.actor_resolver import ActorResolver
from media_stack.api.routes.post_admin_ops import PostMutationGate
from media_stack.api.routing import RouteModule, post
from media_stack.application.auth.users import bulk_ops as _bulk_ops_module
from media_stack.core.auth.users import (
    password_ticket_store as _password_ticket_store_module,
)
from media_stack.core.auth.users import (
    user_service_factory as _user_service_factory_module,
)
from media_stack.core.auth.users.models import UserState
from media_stack.core.auth.users.user_service import UserServiceError


_ERR_LEN = 99


class UserMgmtRepository:
    """Repository over the production user-service factory.

    Each call resolves the underlying service via FRESH module
    attribute lookup so ``mock.patch`` against
    ``user_service_factory.build_default_service`` takes effect
    in unit tests (the wave-3+4 lazy-cache anti-pattern guard).
    """

    def __init__(
        self,
        *,
        service_builder: Callable[[], Any] | None = None,
    ) -> None:
        self._explicit = service_builder

    def service(self) -> Any:
        if self._explicit is not None:
            return self._explicit()
        return _user_service_factory_module.build_default_service()


class ActorResolution:
    """Adapter onto the production
    :class:`_HandlerActorResolverFactory` shape.

    Constructor-injected for tests; production constructs a fresh
    :class:`ActorResolver` per request using the legacy chain's
    inputs (session-cookie / trusted-proxy identity, plus
    ``client_ip`` from the trusted-proxy auth singleton).
    """

    def __init__(
        self,
        *,
        resolver_factory: Callable[[Any, dict[str, Any]], Any] | None = None,
    ) -> None:
        self._explicit = resolver_factory

    def resolve(self, handler: Any, body: dict[str, Any]) -> Any:
        if self._explicit is not None:
            return self._explicit(handler, body)
        # Re-read singletons / factory off their owning modules per
        # call so test patches against
        # ``session_singletons.session_cookie_reader`` and
        # ``user_service_factory.build_default_service`` win.
        session_cookie_reader = (
            _session_singletons_module.session_cookie_reader
        )
        trusted_proxy_auth = (
            _session_singletons_module.trusted_proxy_auth
        )
        build_default_service = (
            _user_service_factory_module.build_default_service
        )
        merged = dict(body or {})
        if not str(merged.get("_actor", "") or "").strip():
            cookie_user = ""
            try:
                cookie_user = (
                    session_cookie_reader.username_for_handler(handler)
                    or ""
                )
            except (AttributeError, KeyError, ValueError):
                cookie_user = ""
            if not cookie_user:
                try:
                    cookie_user = str(
                        trusted_proxy_auth.identity(handler) or "",
                    )
                except (AttributeError, KeyError, ValueError):
                    cookie_user = ""
            if cookie_user:
                merged["_actor"] = cookie_user
        impl = ActorResolver(
            build_service=build_default_service,
            client_ip_for=trusted_proxy_auth.client_ip,
        )
        return impl.resolve(handler, merged)


class LegacyHelperAdapter:
    """Adapter onto the bulk-import + revoke-sessions services lifted
    out of the legacy ``_UserMgmtPostHelper``.

    Constructor-injectable so tests pin behaviour without
    monkey-patching the underlying service singletons.
    """

    def __init__(
        self,
        *,
        importer: Any | None = None,
        revoker: Any | None = None,
    ) -> None:
        self._importer = importer
        self._revoker = revoker

    def _resolve_importer(self) -> Any:
        if self._importer is not None:
            return self._importer
        return _bulk_ops_module.UserBulkImporter()

    def _resolve_revoker(self) -> Any:
        if self._revoker is not None:
            return self._revoker
        return _bulk_ops_module.UserSessionRevoker()

    def bulk_import(
        self, svc: Any, body: dict[str, Any], actor: Any,
    ) -> dict[str, Any]:
        return self._resolve_importer().import_rows(svc, body, actor)

    def revoke_sessions(
        self, svc: Any, user_id: str, actor: Any,
    ) -> dict[str, Any]:
        return self._resolve_revoker().revoke_for_user(svc, user_id, actor)


class LegacyPlaintextStripper:
    """Belt-and-braces: swap legacy ``generated_password`` for a
    one-shot retrieval ticket.

    Same shape as ``handlers_post._strip_legacy_plaintext``.
    Lifted here so the route module has zero coupling to the
    legacy module's free-function surface.
    """

    def __init__(
        self,
        *,
        ticket_minter: Callable[[str, str], dict[str, Any]] | None = None,
    ) -> None:
        # Default ``None`` means "look up
        # ``password_ticket_store.mint_ticket_fields`` at call time"
        # so ``mock.patch`` against that module's attribute takes
        # effect from the test caller. An explicit override (test
        # injection) wins.
        self._mint_override = ticket_minter

    def strip(
        self, result: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        """Mutate-and-return: pop the plaintext, mint a ticket, and
        merge the ticket fields back onto ``result``. Pass-through
        for non-dict / no-plaintext inputs."""
        if not isinstance(result, dict):
            return result
        plaintext = result.pop("generated_password", None)
        if plaintext:
            user_id = str(
                result.get("user_id") or result.get("id") or "",
            )
            if user_id:
                minter = (
                    self._mint_override
                    if self._mint_override is not None
                    else _password_ticket_store_module.mint_ticket_fields
                )
                result.update(minter(user_id, str(plaintext)))
        return result


_INSTANCE = LegacyPlaintextStripper()

# Module-level alias preserving the legacy underscore-name surface
# (`from ... import _strip_legacy_plaintext`) used by tests.
_strip_legacy_plaintext = _INSTANCE.strip


class UsersPostRoutes(RouteModule):
    """User-CRUD POST routes lifted off the legacy elif chain.

    Constructor defaults keep the Router's zero-arg auto-discovery
    intact while letting tests swap any collaborator.
    """

    def __init__(
        self,
        *,
        mutation_gate: PostMutationGate | None = None,
        repository: UserMgmtRepository | None = None,
        actor_resolution: ActorResolution | None = None,
        legacy_helper: LegacyHelperAdapter | None = None,
    ) -> None:
        self._gate = mutation_gate or PostMutationGate(rate_limit=True)
        self._repo = repository or UserMgmtRepository()
        self._actor = actor_resolution or ActorResolution()
        self._helper = legacy_helper or LegacyHelperAdapter()

    # --- gate helper ---------------------------------------------------

    def _gated(self, handler: Any) -> bool:
        if not self._gate.verify(handler):
            self._gate.reject(handler)
            return False
        return True

    def _err400(self, handler: Any, exc: Exception) -> None:
        handler._json_response(
            HTTPStatus.BAD_REQUEST, {"error": str(exc)[:_ERR_LEN]},
        )

    # --- routes --------------------------------------------------------

    @post("/api/users")
    def handle_user_create(self, handler: Any) -> None:
        """Create a user. Admin-only (svc enforces actor.is_admin)."""
        if not self._gated(handler):
            return
        body = handler._read_json_body() or {}
        actor = self._actor.resolve(handler, body)
        try:
            result = self._repo.service().create_user(
                email=str(body.get("email", "")).strip(),
                username=str(body.get("username", "")).strip(),
                display_name=str(body.get("display_name", "")).strip(),
                role_slug=str(body.get("role_slug", "")).strip(),
                password=str(body.get("password", "") or ""),
                actor=actor,
            )
        except UserServiceError as exc:
            self._err400(handler, exc)
            return
        handler._json_response(
            HTTPStatus.OK, _strip_legacy_plaintext(result) or {},
        )

    @post("/api/users-bulk-import")
    def handle_bulk_import(self, handler: Any) -> None:
        """Batch-create users; per-row errors are surfaced in the
        response envelope rather than aborting the whole operation."""
        if not self._gated(handler):
            return
        body = handler._read_json_body() or {}
        actor = self._actor.resolve(handler, body)
        try:
            result = self._helper.bulk_import(
                self._repo.service(), body, actor,
            )
        except UserServiceError as exc:
            self._err400(handler, exc)
            return
        handler._json_response(HTTPStatus.OK, result)

    @post("/api/users-reconcile/import")
    def handle_reconcile_import(self, handler: Any) -> None:
        """Import a provider orphan into the controller user-store."""
        if not self._gated(handler):
            return
        body = handler._read_json_body() or {}
        actor = self._actor.resolve(handler, body)
        try:
            result = self._repo.service().import_orphan(
                provider_name=str(body.get("provider_name", "")),
                external_id=str(body.get("external_id", "")),
                role_slug=str(body.get("role_slug", "")),
                actor=actor,
            )
        except UserServiceError as exc:
            self._err400(handler, exc)
            return
        handler._json_response(HTTPStatus.OK, result)

    @post("/api/users-reconcile/unlink")
    def handle_reconcile_unlink(self, handler: Any) -> None:
        """Drop a stale provider link from a controller user."""
        if not self._gated(handler):
            return
        body = handler._read_json_body() or {}
        actor = self._actor.resolve(handler, body)
        try:
            result = self._repo.service().unlink_ghost(
                user_id=str(body.get("user_id", "")),
                provider_name=str(body.get("provider_name", "")),
                actor=actor,
            )
        except UserServiceError as exc:
            self._err400(handler, exc)
            return
        handler._json_response(HTTPStatus.OK, result)

    @post("/api/users/{user_id}/delete")
    def handle_user_delete(
        self, handler: Any, *, user_id: str,
    ) -> None:
        """Soft-delete a user. Idempotent; the service no-ops on
        an already-deleted account."""
        if not self._gated(handler):
            return
        body = handler._read_json_body() or {}
        actor = self._actor.resolve(handler, body)
        try:
            result = self._repo.service().delete_user(user_id, actor=actor)
        except UserServiceError as exc:
            self._err400(handler, exc)
            return
        handler._json_response(HTTPStatus.OK, result)

    @post("/api/users/{user_id}/reset-password")
    def handle_user_reset_password(
        self, handler: Any, *, user_id: str,
    ) -> None:
        """Admin-driven password reset. Returns a one-shot retrieval
        ticket -- never the plaintext directly."""
        if not self._gated(handler):
            return
        body = handler._read_json_body() or {}
        actor = self._actor.resolve(handler, body)
        try:
            result = self._repo.service().reset_password(
                user_id,
                password=str(body.get("password", "") or ""),
                actor=actor,
            )
        except UserServiceError as exc:
            self._err400(handler, exc)
            return
        handler._json_response(
            HTTPStatus.OK, _strip_legacy_plaintext(result) or {},
        )

    @post("/api/users/{user_id}/revoke-sessions")
    def handle_user_revoke_sessions(
        self, handler: Any, *, user_id: str,
    ) -> None:
        """Cross-provider revoke -- walks every configured provider
        and signals it to drop the user's sessions."""
        if not self._gated(handler):
            return
        body = handler._read_json_body() or {}
        actor = self._actor.resolve(handler, body)
        try:
            result = self._helper.revoke_sessions(
                self._repo.service(), user_id, actor,
            )
        except UserServiceError as exc:
            self._err400(handler, exc)
            return
        handler._json_response(HTTPStatus.OK, result)

    @post("/api/users/{user_id}/role")
    def handle_user_set_role(
        self, handler: Any, *, user_id: str,
    ) -> None:
        """Change a user's role-slug."""
        if not self._gated(handler):
            return
        body = handler._read_json_body() or {}
        actor = self._actor.resolve(handler, body)
        try:
            result = self._repo.service().set_role(
                user_id,
                str(body.get("role_slug", "")).strip(),
                actor=actor,
            )
        except UserServiceError as exc:
            self._err400(handler, exc)
            return
        handler._json_response(HTTPStatus.OK, result)

    @post("/api/users/{user_id}/state")
    def handle_user_set_state(
        self, handler: Any, *, user_id: str,
    ) -> None:
        """Activate / suspend / etc. -- accepts any
        :class:`UserState` member name."""
        if not self._gated(handler):
            return
        body = handler._read_json_body() or {}
        actor = self._actor.resolve(handler, body)
        try:
            result = self._repo.service().set_state(
                user_id,
                UserState(str(body.get("state", "active"))),
                actor=actor,
            )
        except (UserServiceError, ValueError) as exc:
            self._err400(handler, exc)
            return
        handler._json_response(HTTPStatus.OK, result)


__all__ = [
    "ActorResolution",
    "LegacyHelperAdapter",
    "LegacyPlaintextStripper",
    "UserMgmtRepository",
    "UsersPostRoutes",
]
