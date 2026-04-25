"""UserService: orchestrate user CRUD across multiple UserProviders.

Split into a read (query) side and a write (command) side so neither
class breaches the class-method ratchet. Shared fields/constructor logic
lives in UserServiceBase; write operations in user_write_service.py.

Core code stays provider-neutral: the list of concrete providers is
loaded dynamically from ``contracts/user_providers.yaml`` so adding a
new backend requires no edits to this file.
"""

from __future__ import annotations

import logging
from typing import Any

from media_stack.core.auth.authz import Actor, requires_admin
from media_stack.core.auth.users.reconcile import UserReconciler
from media_stack.core.auth.users.user_service_base import (
    UserServiceBase,
    UserServiceError,
)
from media_stack.core.auth.users.user_write_service import UserWriteService

_log = logging.getLogger("media_stack")


class UserQueryService(UserServiceBase):
    """Read-only projections over users, roles, providers, audit."""

    def list_users(self, include_deleted: bool = False) -> list[dict[str, Any]]:
        return [u.to_dict() for u in self._store.list_all(include_deleted=include_deleted)]

    def get_user(self, user_id: str) -> dict[str, Any] | None:
        user = self._store.get(user_id)
        return user.to_dict() if user else None

    def get_user_by_username(self, username: str) -> dict[str, Any] | None:
        """Resolve an active user by username. Returns ``None`` when no
        user matches — the HTTP layer uses this to populate the Actor's
        role/is_admin for authz decisions, so it MUST be read-only and
        side-effect free (no audit entry, no store mutation).
        """
        target = (username or "").strip()
        if not target:
            return None
        user = self._store.get_by_username(target)
        return user.to_dict() if user else None

    def get_role(self, slug: str) -> dict[str, Any] | None:
        """Resolve a role definition by slug. Returns ``None`` for an
        unknown slug. Used by handlers to map a user's ``role_slug`` to
        ``controller_admin`` when building an Actor.
        """
        role = self._roles.get((slug or "").strip())
        return role.to_dict() if role else None

    def list_roles(self) -> list[dict[str, Any]]:
        return [r.to_dict() for r in self._roles.list_all()]

    def provider_health(self) -> list[dict[str, Any]]:
        out = []
        for p in self._providers:
            health = p.health_check()
            out.append({
                "name": p.name,
                "source_of_truth": bool(getattr(p.capabilities, "source_of_truth", False)),
                "ok": bool(health.ok),
                "detail": health.detail,
            })
        return out

    def audit_recent(self, limit: int = 100, action_filter: str = "",
                     target_filter: str = "") -> list[dict[str, Any]]:
        return self._audit.recent(limit=limit, action_filter=action_filter,
                                  target_filter=target_filter)

    def reconcile_report(self) -> list[dict[str, Any]]:
        reconciler = UserReconciler(
            store=self._store, providers=self._providers, audit=self._audit,
        )
        return [d.to_dict() for d in reconciler.diff()]

    def list_sessions(self, user_id: str) -> list[dict[str, Any]]:
        user = self._store.get(user_id)
        if not user:
            return []
        out: list[dict[str, Any]] = []
        for provider in self._providers:
            ext_id = user.provider_refs.get(provider.name)
            if not ext_id:
                continue
            lister = getattr(provider, "list_sessions", None)
            if lister is None:
                continue
            try:
                sessions = lister(ext_id) or []
            except Exception as exc:  # noqa: BLE001
                _log.debug("[DEBUG] list_sessions %s failed: %s",
                           provider.name, exc)
                continue
            for s in sessions:
                d = s.to_dict() if hasattr(s, "to_dict") else dict(s)
                d["provider"] = provider.name
                out.append(d)
        return out

    def user_detail(self, user_id: str) -> dict[str, Any] | None:
        user = self._store.get(user_id)
        if not user:
            return None
        result = user.to_dict()
        latest = user.last_login_at
        for provider in self._providers:
            ext_id = user.provider_refs.get(provider.name)
            if not ext_id:
                continue
            get_last = getattr(provider, "last_activity", None)
            if get_last is None:
                continue
            try:
                ts = get_last(ext_id) or ""
            except Exception:  # noqa: BLE001
                ts = ""
            if ts and ts > latest:
                latest = ts
        if latest != user.last_login_at:
            self._store.update(user_id, last_login_at=latest)
            result["last_login_at"] = latest
        return result


class UserReconcileService(UserServiceBase):
    """Drift reconciliation commands (orphan import, ghost unlink)."""

    def _reconciler(self) -> UserReconciler:
        return UserReconciler(
            store=self._store, providers=self._providers, audit=self._audit,
        )

    @requires_admin
    def import_orphan(self, *, provider_name: str, external_id: str,
                      role_slug: str,
                      actor: Actor | str = "system") -> dict[str, Any]:
        if not self._roles.get(role_slug):
            raise UserServiceError(f"unknown role: {role_slug}")
        actor_label = actor.audit_label if isinstance(actor, Actor) else str(actor)
        return self._reconciler().import_orphan(
            provider_name=provider_name, external_id=external_id,
            role_slug=role_slug, actor=actor_label,
        )

    @requires_admin
    def unlink_ghost(self, *, user_id: str, provider_name: str,
                     actor: Actor | str = "system") -> dict[str, Any]:
        actor_label = actor.audit_label if isinstance(actor, Actor) else str(actor)
        return self._reconciler().unlink_ghost(
            user_id=user_id, provider_name=provider_name, actor=actor_label,
        )


class UserService(UserQueryService, UserWriteService, UserReconcileService):
    """Facade exposing query + write + reconcile operations via MRO."""


__all__ = [
    "UserService",
    "UserServiceError",
    "UserServiceBase",
    "UserQueryService",
    "UserWriteService",
    "UserReconcileService",
]
