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

from media_stack.core.auth.api_token_store import ApiTokenStore
from media_stack.core.auth.basic_auth_verifier import BasicAuthVerifier
from media_stack.core.auth.failed_login_tracker import FailedLoginTracker
from media_stack.core.auth.users.audit_chain_verifier import AuditChainVerifier
from media_stack.core.auth.users.audit_log import AuditLog
from media_stack.core.auth.users.invite_service import InviteService
from media_stack.core.auth.users.invite_store import InviteStore
from media_stack.core.auth.users.legacy_service_admin_adapter import (
    LegacyServiceAdminAdapter,
)
from media_stack.core.auth.users.password_policy import PasswordPolicy
from media_stack.core.auth.users.provider import UserProvider
from media_stack.core.auth.users.role_catalog import RoleCatalog
from media_stack.core.auth.users.role_policy_mapper import RolePolicyMapper
from media_stack.core.auth.users.scheduled_reconcile import ScheduledReconciler
from media_stack.core.auth.users.service_admin_provider import ServiceAdminProvider
from media_stack.core.auth.users.user_service import UserService
from media_stack.core.auth.users.user_store import UserStore

try:
    from media_stack.api.services.admin import (
        reset_password as _LEGACY_RESET_PASSWORD_FN,
    )
except ImportError:
    _LEGACY_RESET_PASSWORD_FN = None


_CONFIG_ROOT_ENV = "CONFIG_ROOT"
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
        config_root = Path(env.get(_CONFIG_ROOT_ENV, _DEFAULT_CONFIG_ROOT))
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

    def build_scheduled_reconciler(self) -> ScheduledReconciler:
        interval = int(self._env.get("RECONCILE_INTERVAL_SEC", 60 * 60))
        return ScheduledReconciler(
            service_factory=self.build,
            interval_sec=interval,
        )

    def build_invite_service(self) -> InviteService:
        env = self._env
        config_root = Path(env.get(_CONFIG_ROOT_ENV, _DEFAULT_CONFIG_ROOT))
        audit = AuditLog(config_root / "controller" / "audit.log.jsonl")
        invites = InviteStore(config_root / "controller" / "invites.json")
        service = self.build()
        return InviteService(
            invites=invites,
            user_creator=service.create_user,
            audit=audit,
        )

    def build_auth_verifier(self) -> BasicAuthVerifier:
        env = self._env
        config_root = Path(env.get(_CONFIG_ROOT_ENV, _DEFAULT_CONFIG_ROOT))
        users_db_path = Path(
            env.get("AUTHELIA_USERS_DB")
            or config_root / "authelia" / "users_database.yml"
        )
        roles_path = self._find_contract(
            env.get("ROLE_CATALOG_PATH", ""), "roles.yaml",
        )
        audit = AuditLog(config_root / "controller" / "audit.log.jsonl")
        tracker = _SHARED_TRACKER

        def _alert(username: str, count: int) -> None:
            audit.append(
                actor="auth-watchdog",
                action="brute_force_alert",
                target=username,
                result="alert",
                detail={"failed_count": count,
                        "window_seconds": tracker.window_seconds},
            )

        return BasicAuthVerifier(
            store=UserStore(config_root / "controller" / "users.json"),
            role_catalog=RoleCatalog(roles_path),
            users_db_path=users_db_path,
            fallback_username=env.get("STACK_ADMIN_USERNAME", "admin"),
            fallback_password=env.get("STACK_ADMIN_PASSWORD", ""),
            failed_login_tracker=tracker,
            alert_fn=_alert,
        )

    def build_audit_chain_verifier(self) -> AuditChainVerifier:
        """Background thread that periodically verifies the audit log's
        hash chain. On tamper detection it writes a loud ERROR line and
        appends an 'audit_chain_tamper' entry so the event is itself
        chained (before the corruption, at least)."""
        config_root = Path(self._env.get(_CONFIG_ROOT_ENV, _DEFAULT_CONFIG_ROOT))
        audit_path = config_root / "controller" / "audit.log.jsonl"

        def _alert(detail: str) -> None:
            try:
                AuditLog(audit_path).append(
                    actor="audit-verifier", action="audit_chain_tamper",
                    target=str(audit_path), result="alert",
                    detail={"message": detail[:99]},
                )
            except Exception as exc:  # noqa: BLE001
                # Already logged upstream by the verifier; writing to a
                # corrupt chain is also likely to fail — swallow at DEBUG.
                import logging as _logging
                _logging.getLogger("media_stack").debug(
                    "[DEBUG] audit-chain alert append failed: %s", exc,
                )

        return AuditChainVerifier(
            audit_factory=lambda: AuditLog(audit_path),
            alert_fn=_alert,
        )

    def build_api_token_store(self) -> ApiTokenStore:
        config_root = Path(self._env.get(_CONFIG_ROOT_ENV, _DEFAULT_CONFIG_ROOT))
        return ApiTokenStore(config_root / "controller" / "api_tokens.json")

    def resolve_roles_path(self) -> Path:
        """Public accessor for callers that need to edit roles.yaml in place."""
        return self._find_contract(
            self._env.get("ROLE_CATALOG_PATH", ""), "roles.yaml",
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
        config_root = Path(self._env.get(_CONFIG_ROOT_ENV, _DEFAULT_CONFIG_ROOT))
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


# One process-wide tracker so repeated verify() calls across
# build_auth_verifier() invocations share the same burst counters.
_SHARED_TRACKER = FailedLoginTracker()

_default_factory = UserServiceFactory()
build_default_service = _default_factory.build
build_default_auth_verifier = _default_factory.build_auth_verifier
build_default_invite_service = _default_factory.build_invite_service
build_default_scheduled_reconciler = _default_factory.build_scheduled_reconciler
build_default_audit_chain_verifier = _default_factory.build_audit_chain_verifier
build_default_api_token_store = _default_factory.build_api_token_store
resolve_default_roles_path = _default_factory.resolve_roles_path
