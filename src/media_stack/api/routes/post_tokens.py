"""Token-management POST routes (ADR-0007 Phase 2 wave 8 group 1).

Three routes lifted off the legacy
``_dispatch_user_mgmt`` chain (which delegated to
``_UserMgmtPostHelper.token_*``):

* ``POST /api/tokens``                  -- mint a token
* ``POST /api/tokens/revoke-family``    -- revoke an entire family
* ``POST /api/tokens/{token_id}``       -- revoke a single token

The companion ``POST /api/tokens/refresh`` is NOT in this group --
it stays in the legacy chain (CSRF-exempt by design; the refresh
token IS the credential). Wave 8 only migrates the
non-refresh surface.

Patterns:

* **Repository** -- ``ApiTokenRepository`` wraps the
  ``ApiTokenStore`` factory + the legacy helper for
  ``mint_pair`` / ``rotate``-style operations. Resolved by
  fresh module attribute read so test patches win.
* **CSRF** -- shared ``PostMutationGate`` from ``post_admin_ops``
  enforces double-submit on every route here.
* **Narrow ``except``** -- only :class:`UserServiceError` (policy
  violations like missing required fields) is surfaced as 400.
"""

from __future__ import annotations

from http import HTTPStatus
from typing import Any, Callable

from media_stack.api.routes.post_admin_ops import PostMutationGate
from media_stack.api.routes.post_users import ActorResolution
from media_stack.api.routing import RouteModule, post
from media_stack.core.auth.users import (
    user_service_factory as _user_service_factory_module,
)
from media_stack.core.auth.users.user_service import UserServiceError


_ERR_LEN = 99
_DEFAULT_TOKEN_NAME = "api-token"
_DEFAULT_TOKEN_SCOPE = "admin"
_KIND_REFRESH_PAIR = "refresh_pair"


class ApiTokenRepository:
    """Repository over ``ApiTokenStore`` + legacy helper.

    Each call resolves the underlying store via FRESH module
    attribute lookup so ``mock.patch`` against
    ``user_service_factory.build_default_api_token_store`` takes
    effect in unit tests.
    """

    def __init__(
        self,
        *,
        store_builder: Callable[[], Any] | None = None,
    ) -> None:
        self._explicit = store_builder

    def store(self) -> Any:
        if self._explicit is not None:
            return self._explicit()
        return _user_service_factory_module.build_default_api_token_store()


class TokensPostRoutes(RouteModule):
    """Three token-management POST routes."""

    def __init__(
        self,
        *,
        mutation_gate: PostMutationGate | None = None,
        repository: ApiTokenRepository | None = None,
        actor_resolution: ActorResolution | None = None,
    ) -> None:
        self._gate = mutation_gate or PostMutationGate()
        self._repo = repository or ApiTokenRepository()
        self._actor = actor_resolution or ActorResolution()

    def _gated(self, handler: Any) -> bool:
        if not self._gate.verify(handler):
            self._gate.reject(handler)
            return False
        return True

    def _err400(self, handler: Any, exc: Exception) -> None:
        handler._json_response(
            HTTPStatus.BAD_REQUEST, {"error": str(exc)[:_ERR_LEN]},
        )

    def _actor_username(self, actor: Any) -> str:
        username = getattr(actor, "username", None)
        if isinstance(username, str):
            return username
        if isinstance(actor, str):
            return actor
        return ""

    @post("/api/tokens")
    def handle_token_create(self, handler: Any) -> None:
        """Mint an API token.

        Two modes:
          * default ``kind=long_lived`` -- one token, optional
            ``ttl_seconds``.
          * ``kind=refresh_pair`` -- mints an access+refresh pair
            with a shared ``family_id``. Both plaintexts are
            returned ONCE.

        ``owner_username`` defaults to ``actor.username`` so a
        non-admin caller minting their own token has the right
        ``owner`` field on the persisted record.
        """
        if not self._gated(handler):
            return
        body = handler._read_json_body() or {}
        actor = self._actor.resolve(handler, body)
        store = self._repo.store()
        actor_username = self._actor_username(actor)
        body_owner = str(body.get("owner_username", "") or "").strip()
        owner = body_owner or actor_username
        name = str(body.get("name", "")).strip() or _DEFAULT_TOKEN_NAME
        scope = (
            str(body.get("scope", _DEFAULT_TOKEN_SCOPE)).strip()
            or _DEFAULT_TOKEN_SCOPE
        )
        kind = str(body.get("kind", "long_lived")).strip()
        try:
            if kind == _KIND_REFRESH_PAIR:
                (access, a_plain), (refresh, r_plain) = store.mint_pair(
                    owner_username=owner, name=name, scope=scope,
                )
                handler._json_response(HTTPStatus.OK, {
                    "access": {**access.to_dict(), "token": a_plain},
                    "refresh": {**refresh.to_dict(), "token": r_plain},
                })
                return
            try:
                ttl_seconds = int(body.get("ttl_seconds", 0) or 0)
            except (TypeError, ValueError):
                ttl_seconds = 0
            token, plaintext = store.create(
                owner_username=owner, name=name, scope=scope,
                ttl_seconds=max(0, ttl_seconds),
            )
        except UserServiceError as exc:
            self._err400(handler, exc)
            return
        handler._json_response(
            HTTPStatus.OK, {**token.to_dict(), "token": plaintext},
        )

    @post("/api/tokens/revoke-family")
    def handle_token_family_revoke(self, handler: Any) -> None:
        """Revoke every live token sharing the given ``family_id``.

        400 when ``family_id`` is missing/blank; the service
        normalises and returns the kill-count otherwise."""
        if not self._gated(handler):
            return
        body = handler._read_json_body() or {}
        actor = self._actor.resolve(handler, body)
        family_id = str(body.get("family_id", "")).strip()
        if not family_id:
            handler._json_response(
                HTTPStatus.BAD_REQUEST, {"error": "family_id required"},
            )
            return
        try:
            killed = self._repo.store().revoke_family(family_id)
        except UserServiceError as exc:
            self._err400(handler, exc)
            return
        handler._json_response(HTTPStatus.OK, {
            "family_id": family_id,
            "revoked_count": killed,
            "actor": self._actor_username(actor),
        })

    @post("/api/tokens/{token_id}")
    def handle_token_revoke(
        self, handler: Any, *, token_id: str,
    ) -> None:
        """Revoke a single token by id. Idempotent -- ``revoked``
        is False when the id was already revoked or unknown."""
        if not self._gated(handler):
            return
        body = handler._read_json_body() or {}
        actor = self._actor.resolve(handler, body)
        try:
            ok = self._repo.store().revoke(token_id)
        except UserServiceError as exc:
            self._err400(handler, exc)
            return
        handler._json_response(HTTPStatus.OK, {
            "token_id": token_id,
            "revoked": ok,
            "actor": self._actor_username(actor),
        })


__all__ = [
    "ApiTokenRepository",
    "TokensPostRoutes",
]
