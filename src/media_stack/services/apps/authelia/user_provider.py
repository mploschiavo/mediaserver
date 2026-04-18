"""Authelia UserProvider — file-based, mutates users_database.yml.

Source of truth for authentication today; Authentik will plug into the
same UserProvider protocol later without touching orchestration code.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from argon2 import PasswordHasher

from media_stack.core.auth.users.provider import (
    ExternalUser,
    ProviderCapabilities,
    ProviderHealth,
)
from media_stack.core.auth.users.safe_yaml_edit import SafeYamlEditor

_DISPLAYNAME_KEY = "displayname"


class AutheliaProviderError(RuntimeError):
    pass


class AutheliaFileProvider:

    name = "authelia"
    capabilities = ProviderCapabilities(
        source_of_truth=True,
        supports_groups=True,
        supports_password=True,
        supports_policy=False,
        auto_provisions_on_login=False,
    )

    def __init__(self, users_db_path: Path, hasher: PasswordHasher | None = None) -> None:
        self._path = Path(users_db_path)
        self._hasher = hasher or PasswordHasher()

    def _validator(self, data: dict[str, Any]) -> None:
        if not isinstance(data.get("users"), dict):
            raise ValueError("users_database.yml must have a top-level 'users' map")
        for username, entry in data["users"].items():
            if not isinstance(entry, dict):
                raise ValueError(f"user {username!r}: entry must be a dict")
            # Password absence is allowed — Authelia accepts users without a
            # password hash (they simply can't authenticate via the file
            # backend until one is set). We only reject an explicitly empty
            # string, which Authelia treats as a corrupt hash.
            pw = entry.get("password")
            if pw is not None and not str(pw).strip():
                raise ValueError(f"user {username!r}: password set but empty")

    def _editor(self) -> SafeYamlEditor:
        return SafeYamlEditor(self._path, validator=self._validator)

    def health_check(self) -> ProviderHealth:
        if not self._path.is_file():
            return ProviderHealth(ok=False, detail=f"users_database missing: {self._path}")
        return ProviderHealth(ok=True)

    def list_users(self) -> list[ExternalUser]:
        if not self._path.is_file():
            return []
        import yaml
        try:
            data = yaml.safe_load(self._path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError:
            return []
        users = data.get("users", {}) or {}
        results: list[ExternalUser] = []
        for username, entry in users.items():
            if not isinstance(entry, dict):
                continue
            results.append(ExternalUser(
                external_id=str(username),
                username=str(username),
                email=str(entry.get("email", "")),
                groups=list(entry.get("groups", []) or []),
                extra={
                    _DISPLAYNAME_KEY: entry.get(_DISPLAYNAME_KEY, ""),
                    "disabled": bool(entry.get("disabled", False)),
                },
            ))
        return results

    def create_user(self, *, username: str, email: str, display_name: str,
                    password: str, groups: list[str],
                    policy: dict[str, Any] | None = None) -> ExternalUser:
        del policy  # not applicable to Authelia
        hashed = self._hasher.hash(password)
        entry = {
            _DISPLAYNAME_KEY: display_name or username,
            "password": hashed,
            "email": email,
            "groups": list(groups),
        }

        def _mutate(current: dict[str, Any]) -> dict[str, Any]:
            users = dict(current.get("users") or {})
            if username in users:
                raise AutheliaProviderError(f"user {username!r} already exists")
            users[username] = entry
            new = dict(current)
            new["users"] = users
            return new

        self._editor().edit(_mutate)
        return ExternalUser(
            external_id=username, username=username, email=email,
            groups=list(groups), extra={_DISPLAYNAME_KEY: display_name},
        )

    def update_user(self, external_id: str, *, display_name: str = "",
                    email: str = "",
                    groups: list[str] | None = None,
                    policy: dict[str, Any] | None = None) -> ExternalUser:
        del policy

        def _mutate(current: dict[str, Any]) -> dict[str, Any]:
            users = dict(current.get("users") or {})
            entry = users.get(external_id)
            if not isinstance(entry, dict):
                raise AutheliaProviderError(f"user {external_id!r} not found")
            entry = dict(entry)
            if display_name:
                entry[_DISPLAYNAME_KEY] = display_name
            if email:
                entry["email"] = email
            if groups is not None:
                entry["groups"] = list(groups)
            users[external_id] = entry
            new = dict(current)
            new["users"] = users
            return new

        new_data = self._editor().edit(_mutate)
        entry = new_data.get("users", {}).get(external_id, {}) or {}
        return ExternalUser(
            external_id=external_id,
            username=external_id,
            email=str(entry.get("email", "")),
            groups=list(entry.get("groups", []) or []),
            extra={_DISPLAYNAME_KEY: entry.get(_DISPLAYNAME_KEY, "")},
        )

    def delete_user(self, external_id: str) -> None:
        def _mutate(current: dict[str, Any]) -> dict[str, Any]:
            users = dict(current.get("users") or {})
            if external_id not in users:
                return current
            del users[external_id]
            new = dict(current)
            new["users"] = users
            return new

        self._editor().edit(_mutate)

    def set_password(self, external_id: str, password: str) -> None:
        hashed = self._hasher.hash(password)

        def _mutate(current: dict[str, Any]) -> dict[str, Any]:
            users = dict(current.get("users") or {})
            entry = users.get(external_id)
            if not isinstance(entry, dict):
                raise AutheliaProviderError(f"user {external_id!r} not found")
            entry = dict(entry)
            entry["password"] = hashed
            users[external_id] = entry
            new = dict(current)
            new["users"] = users
            return new

        self._editor().edit(_mutate)

    def set_groups(self, external_id: str, groups: list[str]) -> None:
        self.update_user(external_id, groups=groups)

    def list_sessions(self, external_id: str) -> list:
        """File backend has no session introspection endpoint."""
        del external_id
        return []

    def last_activity(self, external_id: str) -> str:
        """File backend doesn't track per-user login timestamps."""
        del external_id
        return ""

    def revoke_sessions(self, external_id: str) -> None:
        """No-op for the file backend.

        Authelia file-auth sessions live in its own session DB (cookie
        TTL). When a user is deleted from users_database.yml their cookie
        becomes useless on the next request anyway because the user no
        longer exists. Nothing to do here.
        """
        del external_id
