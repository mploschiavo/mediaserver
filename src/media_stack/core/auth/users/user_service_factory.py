"""UserServiceFactory — constructs the concrete service + providers.

Separated from ``user_service.py`` so that module stays under the
files-over-400-lines ratchet and so env/IO resolution is clearly
localized here.
"""

from __future__ import annotations

import importlib
import os
from pathlib import Path
from typing import Any

import yaml

from media_stack.core.auth.basic_auth_verifier import BasicAuthVerifier
from media_stack.core.auth.users.audit_log import AuditLog
from media_stack.core.auth.users.legacy_service_admin_adapter import (
    LegacyServiceAdminAdapter,
)
from media_stack.core.auth.users.provider import UserProvider
from media_stack.core.auth.users.role_catalog import RoleCatalog
from media_stack.core.auth.users.role_policy_mapper import RolePolicyMapper
from media_stack.core.auth.users.service_admin_provider import ServiceAdminProvider
from media_stack.core.auth.users.user_service import UserService
from media_stack.core.auth.users.user_store import UserStore

try:
    from media_stack.api.services.admin import (
        reset_password as _LEGACY_RESET_PASSWORD_FN,
    )
except ImportError:
    _LEGACY_RESET_PASSWORD_FN = None


_DEFAULT_CONFIG_ROOT = "/srv-config"


class UserServiceFactory:
    """Build a UserService from env + contract files.

    All os.environ access + path resolution lives here so core data
    modules stay env-agnostic. Providers are dynamic-imported from
    ``contracts/user_providers.yaml`` — no backend names in core.
    """

    def __init__(self) -> None:
        self._env = os.environ

    def build(self) -> UserService:
        env = self._env
        config_root = Path(env.get("CONFIG_ROOT", _DEFAULT_CONFIG_ROOT))
        roles_path = self._find_contract(
            env.get("ROLE_CATALOG_PATH", ""), "roles.yaml",
        )
        providers_path = self._find_contract(
            env.get("USER_PROVIDERS_PATH", ""), "user_providers.yaml",
        )
        admins_path = self._find_contract(
            env.get("SERVICE_ADMINS_PATH", ""),
            "service_admin_providers.yaml",
        )
        store = UserStore(config_root / "controller" / "users.json")
        catalog = RoleCatalog(roles_path)
        audit = AuditLog(config_root / "controller" / "audit.log.jsonl")
        providers = self._load_providers(providers_path, env, config_root)
        service_admins = self._load_service_admins(admins_path)
        return UserService(
            store=store, role_catalog=catalog, mapper=RolePolicyMapper(),
            providers=providers, audit=audit,
            service_admins=service_admins,
        )

    def build_auth_verifier(self) -> BasicAuthVerifier:
        env = self._env
        config_root = Path(env.get("CONFIG_ROOT", _DEFAULT_CONFIG_ROOT))
        users_db_path = Path(
            env.get("AUTHELIA_USERS_DB")
            or config_root / "authelia" / "users_database.yml"
        )
        roles_path = self._find_contract(
            env.get("ROLE_CATALOG_PATH", ""), "roles.yaml",
        )
        return BasicAuthVerifier(
            store=UserStore(config_root / "controller" / "users.json"),
            role_catalog=RoleCatalog(roles_path),
            users_db_path=users_db_path,
            fallback_username=env.get("STACK_ADMIN_USERNAME", "admin"),
            fallback_password=env.get("STACK_ADMIN_PASSWORD", ""),
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

    def _load_service_admins(self, path: Path) -> list[ServiceAdminProvider]:
        if not path.is_file():
            return []
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        specs = data.get("providers") or []
        config_root = Path(self._env.get("CONFIG_ROOT", _DEFAULT_CONFIG_ROOT))
        adapters: list[ServiceAdminProvider] = []
        for spec in specs:
            if not isinstance(spec, dict):
                continue
            svc_id = str(spec.get("id", "")).strip()
            if not svc_id:
                continue
            adapter = self._build_service_admin(spec, svc_id, config_root)
            if adapter is not None:
                adapters.append(adapter)
        return adapters

    def _build_service_admin(self, spec: dict, svc_id: str,
                             config_root: Path) -> ServiceAdminProvider | None:
        module_name = str(spec.get("module", "")).strip()
        class_name = str(spec.get("class", "")).strip()
        if module_name and class_name:
            kwargs = self._resolve_args(
                spec.get("args") or {}, self._env, config_root,
            )
            mod = importlib.import_module(module_name)
            return getattr(mod, class_name)(**kwargs)
        # Fall back to the legacy adapter that routes through
        # api/services/admin.py:reset_password with a single-service filter.
        if _LEGACY_RESET_PASSWORD_FN is None:
            return None
        return LegacyServiceAdminAdapter(
            svc_id, reset_fn=_LEGACY_RESET_PASSWORD_FN,
        )


_default_factory = UserServiceFactory()
build_default_service = _default_factory.build
build_default_auth_verifier = _default_factory.build_auth_verifier
