"""UserService: orchestrate user CRUD across multiple UserProviders.

Split into a read (query) side and a write (command) side so neither
class breaches the class-method ratchet. Shared fields/constructor logic
lives in UserServiceBase.

Core code stays provider-neutral: the list of concrete providers is
loaded dynamically from ``contracts/user_providers.yaml`` so adding a
new backend requires no edits to this file.
"""

from __future__ import annotations

import logging
import secrets
from typing import Any

from media_stack.core.auth.users.audit_log import AuditLog

_log = logging.getLogger("media_stack")
from media_stack.core.auth.users.models import User, UserState
from media_stack.core.auth.users.provider import UserProvider
from media_stack.core.auth.users.reconcile import UserReconciler
from media_stack.core.auth.users.role_catalog import RoleCatalog
from media_stack.core.auth.users.role_policy_mapper import RolePolicyMapper
from media_stack.core.auth.users.service_admin_provider import (
    ServiceAdminProvider,
)
from media_stack.core.auth.users.user_store import UserStore

_PASSWORD_ENTROPY_BYTES = 16
_ERR_LEN = 99


class UserServiceError(RuntimeError):
    pass


class UserServiceBase:
    """Shared state + helpers for UserQueryService and UserWriteService."""

    def __init__(
        self,
        *,
        store: UserStore,
        role_catalog: RoleCatalog,
        mapper: RolePolicyMapper,
        providers: list[UserProvider],
        audit: AuditLog,
        service_admins: list[ServiceAdminProvider] | None = None,
    ) -> None:
        self._store = store
        self._roles = role_catalog
        self._mapper = mapper
        self._providers = list(providers)
        self._audit = audit
        self._service_admins = list(service_admins or [])

    def _source_of_truth(self) -> UserProvider | None:
        for p in self._providers:
            if getattr(p.capabilities, "source_of_truth", False):
                return p
        return None

    def _secondary_providers(self) -> list[UserProvider]:
        return [p for p in self._providers
                if not getattr(p.capabilities, "source_of_truth", False)]


class UserQueryService(UserServiceBase):
    """Read-only projections over users, roles, providers, audit."""

    def list_users(self, include_deleted: bool = False) -> list[dict[str, Any]]:
        return [u.to_dict() for u in self._store.list_all(include_deleted=include_deleted)]

    def get_user(self, user_id: str) -> dict[str, Any] | None:
        user = self._store.get(user_id)
        return user.to_dict() if user else None

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


class UserReconcileService(UserServiceBase):
    """Drift reconciliation commands (orphan import, ghost unlink)."""

    def _reconciler(self) -> UserReconciler:
        return UserReconciler(
            store=self._store, providers=self._providers, audit=self._audit,
        )

    def import_orphan(self, *, provider_name: str, external_id: str,
                      role_slug: str, actor: str = "system") -> dict[str, Any]:
        if not self._roles.get(role_slug):
            raise UserServiceError(f"unknown role: {role_slug}")
        return self._reconciler().import_orphan(
            provider_name=provider_name, external_id=external_id,
            role_slug=role_slug, actor=actor,
        )

    def unlink_ghost(self, *, user_id: str, provider_name: str,
                     actor: str = "system") -> dict[str, Any]:
        return self._reconciler().unlink_ghost(
            user_id=user_id, provider_name=provider_name, actor=actor,
        )


class UserWriteService(UserServiceBase):
    """State-changing operations: create, delete, role, state, password."""

    def _generate_password(self) -> str:
        return secrets.token_urlsafe(_PASSWORD_ENTROPY_BYTES)

    def _sso_groups_for(self, role_slug: str) -> list[str]:
        return self._mapper.sso_groups(self._roles.require(role_slug))

    def _payload_for(self, role_slug: str, provider_id: str) -> dict[str, Any]:
        return self._mapper.payload_for(self._roles.require(role_slug), provider_id)

    def create_user(
        self,
        *,
        email: str,
        username: str,
        display_name: str,
        role_slug: str,
        password: str = "",
        actor: str = "system",
    ) -> dict[str, Any]:
        if not email or not username:
            raise UserServiceError("email and username are required")
        if not self._roles.get(role_slug):
            raise UserServiceError(f"unknown role: {role_slug}")
        source = self._source_of_truth()
        if source is None:
            raise UserServiceError("no source-of-truth provider configured")

        password = password or self._generate_password()
        sso_groups = self._sso_groups_for(role_slug)

        user = self._store.create(
            email=email, username=username, display_name=display_name,
            role_slug=role_slug, state=UserState.ACTIVE,
        )
        self._provision_sot(source, user, password, display_name, sso_groups, actor)
        secondary = self._provision_secondaries(
            user, password, display_name, sso_groups, role_slug,
        )

        self._audit.append(
            actor=actor, action="create_user", target=user.email, result="ok",
            detail={"user_id": user.id, "role": role_slug, "secondary": secondary},
        )
        result = user.to_dict()
        result["generated_password"] = password
        result["secondary_results"] = secondary
        return result

    def _provision_sot(self, source, user, password, display_name, sso_groups, actor):
        try:
            ext = source.create_user(
                username=user.username, email=user.email, display_name=display_name,
                password=password, groups=sso_groups,
            )
            self._store.update(user.id, provider_refs={source.name: ext.external_id})
        except Exception as exc:  # noqa: BLE001
            self._store.soft_delete(user.id)
            self._audit.append(actor=actor, action="create_user",
                               target=user.email, result="error",
                               detail={"error": str(exc)[:_ERR_LEN]})
            raise UserServiceError(f"source-of-truth failed: {exc}") from exc

    def _provision_secondaries(self, user, password, display_name, sso_groups, role_slug):
        results: dict[str, Any] = {}
        for provider in self._secondary_providers():
            if provider.capabilities.auto_provisions_on_login:
                results[provider.name] = "deferred_oidc_first_login"
                continue
            try:
                payload = self._payload_for(role_slug, provider.name)
                ext = provider.create_user(
                    username=user.username, email=user.email, display_name=display_name,
                    password=password, groups=sso_groups, policy=payload or None,
                )
                self._store.update(user.id, provider_refs={provider.name: ext.external_id})
                results[provider.name] = "ok"
            except Exception as exc:  # noqa: BLE001
                results[provider.name] = f"error: {str(exc)[:_ERR_LEN]}"
        return results

    def delete_user(self, user_id: str, *, actor: str = "system") -> dict[str, Any]:
        user = self._store.get(user_id)
        if not user:
            raise UserServiceError(f"user not found: {user_id}")
        provider_results = self._forall_providers_delete(user)
        self._store.soft_delete(user_id)
        self._audit.append(
            actor=actor, action="delete_user", target=user.email, result="ok",
            detail={"user_id": user_id, "providers": provider_results},
        )
        return {"user_id": user_id, "providers": provider_results}

    def _forall_providers_delete(self, user: User) -> dict[str, Any]:
        results: dict[str, Any] = {}
        for provider in self._providers:
            external_id = user.provider_refs.get(provider.name)
            if not external_id:
                results[provider.name] = "no_ref"
                continue
            self._revoke_sessions_best_effort(provider, external_id)
            try:
                provider.delete_user(external_id)
                results[provider.name] = "ok"
            except Exception as exc:  # noqa: BLE001
                results[provider.name] = f"error: {str(exc)[:_ERR_LEN]}"
        return results

    def _revoke_sessions_best_effort(self, provider, external_id: str) -> None:
        revoke = getattr(provider, "revoke_sessions", None)
        if revoke is None:
            return
        try:
            revoke(external_id)
        except Exception as exc:  # noqa: BLE001
            # Session revocation is best-effort; we never block the
            # delete on it.
            _log.debug("[DEBUG] revoke_sessions failed for %s/%s: %s",
                       provider.name, external_id, exc)

    def set_role(self, user_id: str, role_slug: str,
                 *, actor: str = "system") -> dict[str, Any]:
        user = self._store.get(user_id)
        if not user:
            raise UserServiceError(f"user not found: {user_id}")
        if not self._roles.get(role_slug):
            raise UserServiceError(f"unknown role: {role_slug}")
        provider_results = self._apply_role_to_providers(user, role_slug)
        self._store.update(user_id, role_slug=role_slug)
        self._audit.append(
            actor=actor, action="set_role", target=user.email, result="ok",
            detail={"user_id": user_id, "role": role_slug, "providers": provider_results},
        )
        return {"user_id": user_id, "role_slug": role_slug, "providers": provider_results}

    def _apply_role_to_providers(self, user, role_slug):
        sso_groups = self._sso_groups_for(role_slug)
        results: dict[str, Any] = {}
        for provider in self._providers:
            external_id = user.provider_refs.get(provider.name)
            if not external_id:
                results[provider.name] = "no_ref"
                continue
            try:
                kwargs: dict[str, Any] = {}
                if provider.capabilities.supports_groups:
                    kwargs["groups"] = sso_groups
                if provider.capabilities.supports_policy:
                    kwargs["policy"] = self._payload_for(role_slug, provider.name)
                provider.update_user(external_id, **kwargs)
                results[provider.name] = "ok"
            except Exception as exc:  # noqa: BLE001
                results[provider.name] = f"error: {str(exc)[:_ERR_LEN]}"
        return results

    def set_state(self, user_id: str, state: UserState,
                  *, actor: str = "system") -> dict[str, Any]:
        user = self._store.get(user_id)
        if not user:
            raise UserServiceError(f"user not found: {user_id}")
        self._store.update(user_id, state=state)
        self._audit.append(
            actor=actor, action="set_state", target=user.email, result="ok",
            detail={"user_id": user_id, "state": state.value},
        )
        return {"user_id": user_id, "state": state.value}

    def reset_password(self, user_id: str, *, password: str = "",
                       actor: str = "system") -> dict[str, Any]:
        user = self._store.get(user_id)
        if not user:
            raise UserServiceError(f"user not found: {user_id}")
        password = password or self._generate_password()
        provider_results = self._forall_providers_set_password(user, password)
        role = self._roles.get(user.role_slug)
        service_admin_results: dict[str, Any] = {}
        if role is not None and role.propagate_to_service_admins:
            service_admin_results = self._propagate_to_service_admins(password)
        self._audit.append(
            actor=actor, action="reset_password", target=user.email, result="ok",
            detail={
                "user_id": user_id,
                "providers": provider_results,
                "service_admins": service_admin_results,
            },
        )
        return {
            "user_id": user_id,
            "generated_password": password,
            "providers": provider_results,
            "service_admins": service_admin_results,
        }

    def _forall_providers_set_password(self, user, password):
        results: dict[str, Any] = {}
        for provider in self._providers:
            if not provider.capabilities.supports_password:
                continue
            external_id = user.provider_refs.get(provider.name)
            if not external_id:
                results[provider.name] = "no_ref"
                continue
            try:
                provider.set_password(external_id, password)
                results[provider.name] = "ok"
            except Exception as exc:  # noqa: BLE001
                results[provider.name] = f"error: {str(exc)[:_ERR_LEN]}"
        return results

    def _propagate_to_service_admins(self, password: str) -> dict[str, Any]:
        results: dict[str, Any] = {}
        for adapter in self._service_admins:
            try:
                adapter.set_admin_password(password)
                results[adapter.name] = "ok"
            except Exception as exc:  # noqa: BLE001
                results[adapter.name] = f"error: {str(exc)[:_ERR_LEN]}"
        return results


class UserService(UserQueryService, UserWriteService, UserReconcileService):
    """Facade exposing query + write + reconcile operations via MRO."""


# Factory + default-service builders are imported here for backward
# compatibility; the implementation lives in user_service_factory to
# keep this module under the files-over-400-lines ratchet.
from media_stack.core.auth.users.user_service_factory import (  # noqa: E402
    UserServiceFactory,
    build_default_auth_verifier,
    build_default_service,
)

__all__ = [
    "UserService",
    "UserServiceError",
    "UserServiceBase",
    "UserQueryService",
    "UserWriteService",
    "UserReconcileService",
    "UserServiceFactory",
    "build_default_service",
    "build_default_auth_verifier",
]
