"""Basic-auth verifier for the controller API.

Three-tier verification, tried in order:

1. Controller user store + source-of-truth provider: if the username
   matches an active user whose role has ``propagate_to_service_admins``,
   verify their password against the provider's stored hash.
2. Legacy env-var fallback (``STACK_ADMIN_PASSWORD``): used before any
   admin has been reconciled/imported into the controller DB, and as a
   break-glass path if the provider is unreachable.

This means resetting the admin's password via the user-management UI
takes effect immediately for controller access — no restart needed.
"""

from __future__ import annotations

import logging
from pathlib import Path

import yaml
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

from media_stack.core.auth.failed_login_tracker import FailedLoginTracker
from media_stack.core.auth.users.role_catalog import RoleCatalog
from media_stack.core.auth.users.user_store import UserStore

_log = logging.getLogger("media_stack")


class BasicAuthVerifier:
    """Verifies username+password against the controller user store first,
    then an env-var fallback.
    """

    def __init__(
        self,
        *,
        store: UserStore,
        role_catalog: RoleCatalog,
        users_db_path: Path,
        fallback_username: str,
        fallback_password: str,
        hasher: PasswordHasher | None = None,
        failed_login_tracker: FailedLoginTracker | None = None,
        alert_fn=None,  # callable(username, count) for brute-force alerts
    ) -> None:
        self._store = store
        self._roles = role_catalog
        self._users_db_path = Path(users_db_path)
        self._fallback_username = fallback_username
        self._fallback_password = fallback_password
        self._hasher = hasher or PasswordHasher()
        self._failed = failed_login_tracker
        self._alert_fn = alert_fn

    def verify(self, username: str, password: str) -> bool:
        """Return True if the credentials authenticate a controller admin."""
        if not username or password is None:
            return False
        ok = self._verify_from_store(username, password) \
            or self._verify_fallback(username, password)
        self._record_result(username, ok)
        return ok

    def _record_result(self, username: str, ok: bool) -> None:
        if self._failed is None:
            return
        if ok:
            self._failed.register_success(username)
            return
        alert, count = self._failed.register_failure(username)
        if alert and self._alert_fn is not None:
            try:
                self._alert_fn(username, count)
            except Exception as exc:  # noqa: BLE001
                _log.debug("[DEBUG] failed-login alert_fn raised: %s", exc)

    def _verify_from_store(self, username: str, password: str) -> bool:
        user = self._store.get_by_username(username)
        if user is None or user.state.value != "active":
            return False
        role = self._roles.get(user.role_slug)
        if role is None or not role.propagate_to_service_admins:
            return False
        entry = self._read_authelia_entry(user)
        stored_hash = str(entry.get("password") or "") if entry else ""
        if not stored_hash:
            return False
        try:
            password_ok = bool(self._hasher.verify(stored_hash, password))
        except VerifyMismatchError:
            return False
        except Exception:  # noqa: BLE001
            return False
        if not password_ok:
            return False
        if role.require_2fa and not self._has_2fa_enrolled(entry):
            return False
        return True

    def _has_2fa_enrolled(self, entry: dict) -> bool:
        if entry.get("has_2fa") is True:
            return True
        method = str(entry.get("method", "")).lower()
        return method in ("totp", "webauthn")

    def _read_authelia_entry(self, user) -> dict:
        if not self._users_db_path.is_file():
            return {}
        authelia_ref = user.provider_refs.get("authelia", "")
        if not authelia_ref:
            return {}
        try:
            data = yaml.safe_load(
                self._users_db_path.read_text(encoding="utf-8"),
            ) or {}
        except yaml.YAMLError:
            return {}
        return (data.get("users") or {}).get(authelia_ref) or {}

    def _verify_fallback(self, username: str, password: str) -> bool:
        return (
            username == self._fallback_username
            and password == self._fallback_password
            and bool(self._fallback_password)
        )
