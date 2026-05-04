"""Bulk user-management operations (import, session revocation).

Lifted from ``media_stack.api.handlers_post._UserMgmtPostHelper``
during ADR-0007 Phase 2 Phase E (legacy-handler retirement).

The two operations live here:

* :class:`UserBulkImporter` -- iterates a ``users`` array, calls
  ``svc.create_user`` per row, and aggregates results + per-row
  errors. Used by ``POST /api/users-bulk-import``.
* :class:`UserSessionRevoker` -- walks every provider on the user
  service and calls ``provider.revoke_sessions(external_id)`` for
  the target user, returning a per-provider status map. Used by
  ``POST /api/users/{user_id}/revoke-sessions``.

Both operations write audit entries so the action is visible in
the audit log even when the underlying service call swallows an
error.
"""

from __future__ import annotations

from typing import Any


_ERR_LEN = 99


class UserBulkImporter:
    """Bulk-import wrapper around ``UserService.create_user``.

    Stateless service. The service-class identity is preserved
    (rather than collapsing to a free function) because future
    enhancements -- progress reporting, partial-failure rollback,
    a streaming CSV parser -- want a stable instance to attach
    state to.
    """

    def import_rows(
        self,
        svc: Any,
        body: dict[str, Any],
        actor: Any,
    ) -> dict[str, Any]:
        from media_stack.core.auth.users.user_service import UserServiceError

        rows = body.get("users") or []
        if not isinstance(rows, list):
            raise UserServiceError("users must be a list")
        imported: list[dict[str, Any]] = []
        errors: list[str] = []
        for row in rows:
            try:
                result = svc.create_user(
                    email=str(row.get("email", "")).strip(),
                    username=str(row.get("username", "")).strip(),
                    display_name=str(row.get("display_name", "")).strip(),
                    role_slug=(
                        str(row.get("role_slug", "adult")).strip() or "adult"
                    ),
                    actor=actor,
                )
                row_out: dict[str, Any] = {
                    "email": result["email"],
                    "user_id": result["id"],
                }
                # Service emits the ticket fields; copy them per-row
                # so the caller's JSON has one retrieval handle per
                # imported account. Absent when an admin-supplied
                # password was provided for the row (no ticket needed).
                if "password_ticket" in result:
                    row_out["password_ticket"] = result["password_ticket"]
                if "ticket_expires_at" in result:
                    row_out["ticket_expires_at"] = (
                        result["ticket_expires_at"]
                    )
                imported.append(row_out)
            except Exception as exc:  # noqa: BLE001
                errors.append(
                    f"{row.get('email', '')}: {str(exc)[:_ERR_LEN]}",
                )
        return {
            "imported": imported,
            "errors": errors,
            "count": len(imported),
        }


class UserSessionRevoker:
    """Cross-provider session revocation for a single user.

    Walks every provider configured on the user service and asks
    each to revoke any active sessions for the user's external id
    on that provider. Returns a per-provider status map so the UI
    can show ``{jellyfin: ok, jellyseerr: no_ref}``.
    """

    def revoke_for_user(
        self,
        svc: Any,
        user_id: str,
        actor: Any,
    ) -> dict[str, Any]:
        user = svc._store.get(user_id)
        if user is None:
            return {"user_id": user_id, "error": "not found"}
        results: dict[str, str] = {}
        for provider in svc._providers:
            results[provider.name] = self._revoke_on_provider(provider, user)
        svc._audit.append(
            actor=actor,
            action="revoke_sessions",
            target=user.email,
            result="ok",
            detail={"user_id": user_id, "providers": results},
        )
        return {"user_id": user_id, "providers": results}

    def _revoke_on_provider(self, provider: Any, user: Any) -> str:
        external_id = user.provider_refs.get(provider.name)
        if not external_id:
            return "no_ref"
        revoke = getattr(provider, "revoke_sessions", None)
        if revoke is None:
            return "unsupported"
        try:
            revoke(external_id)
            return "ok"
        except Exception as exc:  # noqa: BLE001
            return f"error: {str(exc)[:_ERR_LEN]}"


__all__ = [
    "UserBulkImporter",
    "UserSessionRevoker",
]
