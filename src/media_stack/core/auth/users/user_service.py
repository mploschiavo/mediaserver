"""UserService: orchestrate user CRUD across multiple UserProviders.

Split into a read (query) side and a write (command) side so neither
class breaches the class-method ratchet. Shared fields/constructor logic
lives in UserServiceBase.

Core code stays provider-neutral: the list of concrete providers is
loaded dynamically from ``contracts/user_providers.yaml`` so adding a
new backend requires no edits to this file.
"""

from __future__ import annotations

import importlib
import os
import secrets
from pathlib import Path
from typing import Any

import yaml

from media_stack.core.auth.users.audit_log import AuditLog
from media_stack.core.auth.users.models import User, UserState
from media_stack.core.auth.users.provider import UserProvider
from media_stack.core.auth.users.role_catalog import RoleCatalog
from media_stack.core.auth.users.role_policy_mapper import RolePolicyMapper
from media_stack.core.auth.users.user_store import UserStore

_PASSWORD_ENTROPY_BYTES = 16
_DEFAULT_CONFIG_ROOT = "/srv-config"
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
    ) -> None:
        self._store = store
        self._roles = role_catalog
        self._mapper = mapper
        self._providers = list(providers)
        self._audit = audit

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
            try:
                provider.delete_user(external_id)
                results[provider.name] = "ok"
            except Exception as exc:  # noqa: BLE001
                results[provider.name] = f"error: {str(exc)[:_ERR_LEN]}"
        return results

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
        self._audit.append(
            actor=actor, action="reset_password", target=user.email, result="ok",
            detail={"user_id": user_id, "providers": provider_results},
        )
        return {
            "user_id": user_id,
            "generated_password": password,
            "providers": provider_results,
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


class UserService(UserQueryService, UserWriteService):
    """Facade exposing read + write operations; delegates via MRO."""


class UserServiceFactory:
    """Build a UserService from env + contract files.

    All os.environ access + path resolution lives here so core data
    modules stay env-agnostic. Providers are dynamic-imported from
    ``contracts/user_providers.yaml`` — no backend names in core.
    """

    def build(self) -> UserService:
        env = os.environ
        config_root = Path(env.get("CONFIG_ROOT", _DEFAULT_CONFIG_ROOT))
        roles_path = self._find_contract(env.get("ROLE_CATALOG_PATH", ""), "roles.yaml")
        providers_path = self._find_contract(
            env.get("USER_PROVIDERS_PATH", ""), "user_providers.yaml",
        )

        store = UserStore(config_root / "controller" / "users.json")
        catalog = RoleCatalog(roles_path)
        audit = AuditLog(config_root / "controller" / "audit.log.jsonl")
        providers = self._load_providers(providers_path, env, config_root)
        return UserService(
            store=store, role_catalog=catalog, mapper=RolePolicyMapper(),
            providers=providers, audit=audit,
        )

    def _find_contract(self, explicit: str, filename: str) -> Path:
        if explicit:
            return Path(explicit)
        for candidate in (
            Path(f"/app/contracts/{filename}"),
            Path(__file__).resolve().parents[5] / "contracts" / filename,
        ):
            if candidate.is_file():
                return candidate
        return Path(f"contracts/{filename}")

    def _load_providers(self, providers_path: Path,
                        env: Any, config_root: Path) -> list[UserProvider]:
        if not providers_path.is_file():
            return []
        data = yaml.safe_load(providers_path.read_text(encoding="utf-8")) or {}
        specs = data.get("providers") or []
        providers: list[UserProvider] = []
        for spec in specs:
            if not isinstance(spec, dict):
                continue
            module_name = str(spec.get("module", "")).strip()
            class_name = str(spec.get("class", "")).strip()
            if not module_name or not class_name:
                continue
            kwargs = self._resolve_args(spec.get("args") or {}, env, config_root)
            mod = importlib.import_module(module_name)
            providers.append(getattr(mod, class_name)(**kwargs))
        return providers

    def _resolve_args(self, args_spec: dict, env: Any,
                      config_root: Path) -> dict[str, Any]:
        resolved: dict[str, Any] = {}
        for key, binding in args_spec.items():
            if not isinstance(binding, dict):
                resolved[key] = binding
                continue
            env_name = binding.get("env", "")
            default = binding.get("default", "")
            value = env.get(env_name, "") if env_name else ""
            if not value:
                value = str(default).replace("{config_root}", str(config_root))
            if key.endswith("_path"):
                resolved[key] = Path(value)
            else:
                resolved[key] = value
        return resolved


_default_factory = UserServiceFactory()
build_default_service = _default_factory.build
