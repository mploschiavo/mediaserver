"""UserWriteService — state-changing operations for UserService.

Split out of user_service.py so that file stays under the
FILES_OVER_400_LINES ratchet. UserService composes this via MRO.
"""

from __future__ import annotations

import logging
import secrets
from typing import Any

from media_stack.core.auth.users.models import User, UserState
from media_stack.core.auth.users.user_service_base import (
    UserServiceBase,
    UserServiceError,
)

_log = logging.getLogger("media_stack")

_PASSWORD_ENTROPY_BYTES = 16
_ERR_LEN = 99


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

        admin_supplied = bool(password)
        password = password or self._generate_password()
        if admin_supplied:
            _check = self._policy.check_candidate(password, history_hashes=[])
            if not _check.ok:
                raise UserServiceError(_check.reason or "password rejected by policy")
        sso_groups = self._sso_groups_for(role_slug)

        user = self._store.create(
            email=email, username=username, display_name=display_name,
            role_slug=role_slug, state=UserState.ACTIVE,
        )
        initial_history = self._policy.push_history([], password)
        if initial_history:
            self._store.update(user.id, password_history=initial_history)
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
        admin_supplied = bool(password)
        password = password or self._generate_password()
        if admin_supplied:
            _check = self._policy.check_candidate(
                password, history_hashes=user.password_history,
            )
            if not _check.ok:
                raise UserServiceError(_check.reason or "password rejected by policy")
        new_history = self._policy.push_history(
            user.password_history, password,
        )
        self._store.update(user_id, password_history=new_history)
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
