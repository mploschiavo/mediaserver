"""UserWriteService — state-changing operations for UserService.

Split out of user_service.py so that file stays under the
FILES_OVER_400_LINES ratchet. UserService composes this via MRO.
"""

from __future__ import annotations

import logging
import secrets
from typing import Any

from media_stack.core.auth.authz import Actor, requires_admin, requires_self_or_admin
from media_stack.core.auth.users.models import User, UserState
from media_stack.core.auth.users.async_propagation import (
    run_service_admin_propagation_async,
)
from media_stack.core.auth.users.orphan_adoption import (
    OrphanAdoptionFinder, adopt_into_provider,
)
from media_stack.core.auth.users.password_ticket_store import (
    mint_ticket_fields as _mint_password_ticket,
)
from media_stack.core.auth.users.provider_self_heal import (
    heal_missing_provider_user as _heal_missing_provider_user,
)
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

    @requires_admin
    def create_user(
        self,
        *,
        email: str,
        username: str,
        display_name: str,
        role_slug: str,
        password: str = "",
        actor: Actor | str = "system",
        skip_policy_check: bool = False,
    ) -> dict[str, Any]:
        # skip_policy_check is for bootstrap flows given an operator-
        # supplied password (STACK_ADMIN_PASSWORD, restored backups)
        # which may be weak — forced-rotation flows catch those.
        if not email or not username:
            raise UserServiceError("email and username are required")
        if not self._roles.get(role_slug):
            raise UserServiceError(f"unknown role: {role_slug}")
        source = self._source_of_truth()
        if source is None:
            raise UserServiceError("no source-of-truth provider configured")

        actor_label = actor.audit_label

        admin_supplied = bool(password)
        password = password or self._generate_password()
        if admin_supplied and not skip_policy_check:
            _check = self._policy.check_candidate(password, history_hashes=[])
            if not _check.ok:
                raise UserServiceError(_check.reason or "password rejected by policy")
        sso_groups = self._sso_groups_for(role_slug)

        # Early orphan-adoption: scan providers for an existing record
        # matching this username/email. Running BEFORE _store.create()
        # avoids a create/soft-delete round-trip. See orphan_adoption.py.
        candidate = OrphanAdoptionFinder(
            self._providers, source.name,
        ).find(username=username, email=email)
        if candidate and candidate.is_source_of_truth:
            return self._create_via_adoption(
                source=source, candidate=candidate,
                email=email, username=username, display_name=display_name,
                role_slug=role_slug, password=password,
                sso_groups=sso_groups, actor=actor_label,
                admin_supplied=admin_supplied,
            )

        user = self._store.create(
            email=email, username=username, display_name=display_name,
            role_slug=role_slug, state=UserState.ACTIVE,
        )
        initial_history = self._policy.push_history([], password)
        if initial_history:
            self._store.update(user.id, password_history=initial_history)
        self._provision_sot(source, user, password, display_name, sso_groups, actor_label)
        secondary = self._provision_secondaries(
            user, password, display_name, sso_groups, role_slug,
        )

        self._audit.append(
            actor=actor_label, action="create_user", target=user.email, result="ok",
            detail={"user_id": user.id, "role": role_slug, "secondary": secondary},
        )
        result = user.to_dict()
        result["secondary_results"] = secondary
        # Admin-supplied passwords bypass the ticket (caller already
        # knows plaintext); generated ones go through the store.
        if not admin_supplied:
            result.update(_mint_password_ticket(user.id, password))
        return result

    def _create_via_adoption(self, *, source, candidate, email, username,
                             display_name, role_slug, password,
                             sso_groups, actor,
                             admin_supplied: bool = False) -> dict[str, Any]:
        """Link a fresh central row to an existing source-of-truth
        record — the adoption branch of create_user. The central
        store gets a normal row (with a newly-generated id), its
        provider_refs point at the found external_id, and the
        external record's password + groups get replaced so the
        operator's intent wins over whatever stale state existed.
        """
        user = self._store.create(
            email=email, username=username, display_name=display_name,
            role_slug=role_slug, state=UserState.ACTIVE,
        )
        initial_history = self._policy.push_history([], password)
        if initial_history:
            self._store.update(user.id, password_history=initial_history)
        self._store.update(
            user.id, provider_refs={source.name: candidate.external_id},
        )
        replace_status = adopt_into_provider(
            source, candidate.external_id,
            password=password, sso_groups=sso_groups,
        )
        # Secondaries (jellyfin, jellyseerr) still run — they may have
        # the user too (additional adopt branches would help later) or
        # be missing it entirely (best-effort create handles both).
        secondary = self._provision_secondaries(
            user, password, display_name, sso_groups, role_slug,
        )
        self._audit.append(
            actor=actor, action="create_user_via_adoption",
            target=user.email, result="ok",
            detail={
                "user_id": user.id, "role": role_slug,
                "adopted_from": {
                    "provider": candidate.provider_name,
                    "external_id": candidate.external_id,
                    "match": candidate.match,
                },
                "replace": replace_status,
                "secondary": secondary,
            },
        )
        result = user.to_dict()
        result["secondary_results"] = secondary
        result["adopted"] = True
        if not admin_supplied:
            result.update(_mint_password_ticket(user.id, password))
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

    @requires_admin
    def delete_user(self, user_id: str, *, actor: Actor | str = "system") -> dict[str, Any]:
        user = self._store.get(user_id)
        if not user:
            raise UserServiceError(f"user not found: {user_id}")
        provider_results = self._forall_providers_delete(user)
        self._store.soft_delete(user_id)
        self._audit.append(
            actor=actor.audit_label, action="delete_user", target=user.email, result="ok",
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

    @requires_admin
    def set_role(self, user_id: str, role_slug: str,
                 *, actor: Actor | str = "system") -> dict[str, Any]:
        user = self._store.get(user_id)
        if not user:
            raise UserServiceError(f"user not found: {user_id}")
        if not self._roles.get(role_slug):
            raise UserServiceError(f"unknown role: {role_slug}")
        provider_results = self._apply_role_to_providers(user, role_slug)
        self._store.update(user_id, role_slug=role_slug)
        self._audit.append(
            actor=actor.audit_label, action="set_role", target=user.email, result="ok",
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

    @requires_admin
    def set_state(self, user_id: str, state: UserState,
                  *, actor: Actor | str = "system") -> dict[str, Any]:
        user = self._store.get(user_id)
        if not user:
            raise UserServiceError(f"user not found: {user_id}")
        self._store.update(user_id, state=state)
        self._audit.append(
            actor=actor.audit_label, action="set_state", target=user.email, result="ok",
            detail={"user_id": user_id, "state": state.value},
        )
        return {"user_id": user_id, "state": state.value}

    @requires_self_or_admin(param="user_id")
    def reset_password(self, user_id: str, *, password: str = "",
                       actor: Actor | str = "system") -> dict[str, Any]:
        user = self._store.get(user_id)
        if not user:
            raise UserServiceError(f"user not found: {user_id}")
        actor_label = actor.audit_label
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
        # If this row was still sitting on the bootstrap source, the
        # rotation flips it to ``rotated`` so the env fallback can be
        # disabled. See BasicAuthVerifier._fallback_still_active.
        update_fields: dict[str, Any] = {"password_history": new_history}
        source = str(getattr(user, "source", "") or "").strip().lower()
        if source in ("env-seed", "env-legacy"):
            update_fields["source"] = "rotated"
        self._store.update(user_id, **update_fields)
        # Provider propagation is synchronous: the source-of-truth
        # (Authelia) must be updated before we return, otherwise the
        # user logs out and cannot sign in with the new password.
        provider_results = self._forall_providers_set_password(user, password)
        # Service-admin propagation (Sonarr/Radarr/qBittorrent/etc.)
        # is downstream replica work — runs in a daemon thread so the
        # response ships as soon as Authelia is sync'd. Errors from
        # the background path land in the audit log when they happen.
        role = self._roles.get(user.role_slug)
        propagated_in_background = bool(
            role is not None and role.propagate_to_service_admins
        )
        if propagated_in_background:
            self._propagate_to_service_admins_async(
                password, user_id=user_id, user_email=user.email,
                actor=actor_label,
            )
        self._audit.append(
            actor=actor_label, action="reset_password", target=user.email, result="ok",
            detail={
                "user_id": user_id,
                "providers": provider_results,
                "service_admins": (
                    "scheduled_async" if propagated_in_background else "n/a"
                ),
            },
        )
        response: dict[str, Any] = {
            "user_id": user_id, "providers": provider_results,
            "service_admins": (
                "scheduled_async" if propagated_in_background else {}),
        }
        if not admin_supplied:
            response.update(_mint_password_ticket(user_id, password))
        return response

    def _propagate_to_service_admins_async(
        self, password: str, *,
        user_id: str, user_email: str, actor: str,
    ) -> None:
        run_service_admin_propagation_async(
            self._propagate_to_service_admins, self._audit,
            password=password, user_id=user_id,
            user_email=user_email, actor=actor,
        )

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
                # Self-heal: if the provider has lost the user record
                # (e.g. users_database.yml was rebuilt but the
                # controller store still has the provider_ref), try
                # recreating the user with the current password instead
                # of returning a silent "error: user not found".
                role = self._roles.get(user.role_slug)
                if _heal_missing_provider_user(provider, user, password,
                                               exc, role):
                    results[provider.name] = "healed"
                else:
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

