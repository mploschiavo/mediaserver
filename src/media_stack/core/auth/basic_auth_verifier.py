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

from pathlib import Path

import yaml
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

from media_stack.core.auth.users.role_catalog import RoleCatalog
from media_stack.core.auth.users.user_store import UserStore


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
    ) -> None:
        self._store = store
        self._roles = role_catalog
        self._users_db_path = Path(users_db_path)
        self._fallback_username = fallback_username
        self._fallback_password = fallback_password
        self._hasher = hasher or PasswordHasher()

    def verify(self, username: str, password: str) -> bool:
        """Return True if the credentials authenticate a controller admin."""
        if not username or password is None:
            return False
        if self._verify_from_store(username, password):
            return True
        return self._verify_fallback(username, password)

    def _verify_from_store(self, username: str, password: str) -> bool:
        user = self._store.get_by_username(username)
        if user is None or user.state.value != "active":
            return False
        role = self._roles.get(user.role_slug)
        # Only roles with propagate_to_service_admins may authenticate to
        # the controller UI directly. End-user roles (adult/teen/kid) log
        # into apps via Authelia SSO, not the controller.
        if role is None or not role.propagate_to_service_admins:
            return False
        stored_hash = self._lookup_provider_hash(user)
        if not stored_hash:
            return False
        try:
            return bool(self._hasher.verify(stored_hash, password))
        except VerifyMismatchError:
            return False
        except Exception:  # noqa: BLE001
            return False

    def _lookup_provider_hash(self, user) -> str:
        # Source-of-truth provider is Authelia (file-backed). Read the
        # hash directly — avoids an import cycle on the provider module.
        if not self._users_db_path.is_file():
            return ""
        authelia_ref = user.provider_refs.get("authelia", "")
        if not authelia_ref:
            return ""
        try:
            data = yaml.safe_load(
                self._users_db_path.read_text(encoding="utf-8"),
            ) or {}
        except yaml.YAMLError:
            return ""
        entry = (data.get("users") or {}).get(authelia_ref) or {}
        return str(entry.get("password") or "")

    def _verify_fallback(self, username: str, password: str) -> bool:
        return (
            username == self._fallback_username
            and password == self._fallback_password
            and bool(self._fallback_password)
        )
