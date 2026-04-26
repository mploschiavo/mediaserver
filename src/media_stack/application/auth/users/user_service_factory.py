"""UserServiceFactory — constructs the concrete service + providers.

Separated from ``user_service.py`` so that module stays under the
files-over-400-lines ratchet and so env/IO resolution is clearly
localized here.
"""

from __future__ import annotations

import importlib
from media_stack.core.logging_utils import log_swallowed
import os
from pathlib import Path
from typing import Any

import yaml

from media_stack.application.auth.admin_bootstrap import AdminBootstrap
from media_stack.core.auth.api_token_store import ApiTokenStore
from media_stack.application.auth.basic_auth_verifier import BasicAuthVerifier
from media_stack.domain.auth.failed_login_tracker import FailedLoginTracker
from media_stack.application.auth.users.audit_chain_verifier import AuditChainVerifier
from media_stack.core.auth.users.audit_log import AuditLog
from media_stack.application.auth.users.invite_service import InviteService
from media_stack.core.auth.users.invite_store import InviteStore
from media_stack.application.auth.users.legacy_service_admin_adapter import (
    LegacyServiceAdminAdapter,
)
from media_stack.domain.auth.users.password_policy import PasswordPolicy
from media_stack.api.services.password_policy_config import (
    PasswordPolicyConfig as _PasswordPolicyConfig,
)
from media_stack.domain.auth.users.provider import UserProvider
from media_stack.core.auth.users.role_catalog import RoleCatalog
from media_stack.domain.auth.users.role_policy_mapper import RolePolicyMapper
from media_stack.application.auth.users.scheduled_reconcile import ScheduledReconciler
from media_stack.domain.auth.users.service_admin_provider import ServiceAdminProvider
from media_stack.application.auth.users.user_service import UserService
from media_stack.core.auth.users.user_store import UserStore

try:
    from media_stack.api.services.admin import (
        reset_password as _LEGACY_RESET_PASSWORD_FN,
    )
except ImportError:
    _LEGACY_RESET_PASSWORD_FN = None


_CONFIG_ROOT_ENV = "CONFIG_ROOT"
_DEFAULT_CONFIG_ROOT = "/srv-config"

# Controller state directory — DOT-prefixed to match the k8s PVC mount
# and the paths state.py has used since v1.0.162. Previously this
# factory wrote to ``<config_root>/controller/`` (no dot), which on k8s
# landed on the pod's ephemeral overlay because the PVC is mounted at
# ``<config_root>/.controller/``. Effect: every user created via the UI
# survived until the next pod restart, then vanished — the "patches on
# a live system" failure class this session has been fighting.
# (v1.0.169 fix; the migration path below moves old state over.)
_CONTROLLER_STATE_DIR = ".controller"


def _state_path(config_root: Path, filename: str) -> Path:
    """Return the authoritative path for a controller-state file, and
    migrate from the legacy non-dot location if only the legacy file
    exists. Idempotent — once migration happens on first call, every
    subsequent call returns the new path directly.
    """
    new_path = config_root / _CONTROLLER_STATE_DIR / filename
    new_path.parent.mkdir(parents=True, exist_ok=True)
    if new_path.exists():
        return new_path
    legacy_path = config_root / "controller" / filename
    if legacy_path.exists():
        try:
            # Copy rather than rename so a partially-rolled-out fleet
            # (some pods on the fix, some still on the old code) doesn't
            # watch its state disappear from under it. The non-dot copy
            # can be garbage-collected later once every pod's on v1.0.169+.
            new_path.write_bytes(legacy_path.read_bytes())
        except OSError as exc:
            log_swallowed(exc)
    return new_path


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
        store = UserStore(_state_path(config_root, "users.json"))
        catalog = RoleCatalog(roles_path)
        audit = AuditLog(_state_path(config_root, "audit.log.jsonl"))
        providers = self._load_providers(providers_path, env, config_root)
        service_admins = self._load_service_admins(admins_path)
        # Admin-configurable password policy (edited from the Users
        # tab in the dashboard). Falls back to class defaults when the
        # file doesn't exist, so a fresh install enforces a strong
        # default policy out of the box.
        policy = _PasswordPolicyConfig(config_root).build_policy()
        service = UserService(
            store=store, role_catalog=catalog, mapper=RolePolicyMapper(),
            providers=providers, audit=audit,
            service_admins=service_admins,
            password_policy=policy,
        )
        # Seed admin from env if the store is empty. Idempotent —
        # once a superadmin exists this is a no-op. Kept behind a
        # try/except so a seed failure can't block every other
        # UserService client (e.g. the user-list endpoint), EXCEPT
        # for the weak-password refusal: that's fatal by design on
        # internet_exposed deploys and must propagate.
        internet_exposed = str(
            env.get("INTERNET_EXPOSED", "")
        ).strip().lower() in ("1", "true", "yes", "on")
        try:
            result = AdminBootstrap(env=env).run(
                service, internet_exposed=internet_exposed,
            )
            self._log_bootstrap_state(result)
        except AdminBootstrap.WeakPasswordError:
            raise
        except Exception as exc:  # noqa: BLE001
            import logging as _logging
            _logging.getLogger("media_stack").warning(
                "[WARN] admin-bootstrap failed on build: %s", exc,
            )
        return service

    def _log_bootstrap_state(self, result: dict) -> None:
        """Emit a single, machine-greppable boot line naming the
        bootstrap state so operators can confirm at a glance which
        path fired. See project memory 'Admin bootstrap redesign'."""
        import logging as _logging
        log = _logging.getLogger("media_stack")
        action = result.get("action", "")
        source = result.get("source", "")
        reason = result.get("reason", "")
        message, level = self._bootstrap_log_line(result, action, source, reason)
        if not message:
            return
        (log.warning if level == "warn" else log.info)(message)

    def _bootstrap_log_line(
        self, result: dict, action: str, source: str, reason: str,
    ) -> tuple[str, str]:
        """Resolve action → (message, severity). Flat dispatch so the
        enclosing logger method stays under the deeply-nested ratchet."""
        if action == "seeded":
            return f"[OK] admin_bootstrap: seeded from env (source={source})", "info"
        if action == "linked":
            suffix = " + password seeded" if result.get("password_seeded") else ""
            return (f"[OK] admin_bootstrap: linked existing provider "
                    f"admin (source={source}{suffix})", "info")
        if action == "skipped" and reason == "existing_superadmin":
            return ("[OK] admin_bootstrap: existing superadmin in "
                    "store; env fallback gated on source", "info")
        if action == "skipped" and reason == "no_credential":
            return ("[WARN] admin_bootstrap: no STACK_ADMIN_PASSWORD "
                    "set and store is empty; no one can log in yet", "warn")
        if action == "error":
            return (f"[WARN] admin_bootstrap: error: "
                    f"{result.get('error', '')[:99]}", "warn")
        return "", "info"

    def build_scheduled_reconciler(self) -> ScheduledReconciler:
        interval = int(self._env.get("RECONCILE_INTERVAL_SEC", 60 * 60))
        return ScheduledReconciler(
            service_factory=self.build,
            interval_sec=interval,
        )

    def build_invite_service(self) -> InviteService:
        env = self._env
        config_root = Path(env.get(_CONFIG_ROOT_ENV, _DEFAULT_CONFIG_ROOT))
        audit = AuditLog(_state_path(config_root, "audit.log.jsonl"))
        invites = InviteStore(_state_path(config_root, "invites.json"))
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
        audit = AuditLog(_state_path(config_root, "audit.log.jsonl"))
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
            store=UserStore(_state_path(config_root, "users.json")),
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
        audit_path = _state_path(config_root, "audit.log.jsonl")

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
        return ApiTokenStore(_state_path(config_root, "api_tokens.json"))

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
