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
from media_stack.core.auth.users.visibility_protocols import (
    APIToken,
    MFAState,
)

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
                    # Never leak the hash itself — just a boolean
                    # so the admin-bootstrap flow can decide whether
                    # to seed a password when linking a row that
                    # was created without one (fresh-install path).
                    "has_password": bool(
                        str(entry.get("password", "") or "").strip(),
                    ),
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

    def revoke_session(self, external_id: str, session_id: str) -> None:
        """Per-session revoke is not reachable via the file backend.

        The canonical way to kill a specific Authelia session is to
        delete its row from the ``user_sessions`` table in
        ``db.sqlite3``. That's a separate ``AutheliaSessionAdmin``
        concern and lives in ``services.apps.authelia.session_admin``
        (planned). Calling revoke here is a best-effort request; the
        null behaviour is safe because disabling the user (see
        ``disable_user``) already prevents any future request on that
        session from being authorized.
        """
        del external_id, session_id

    # ---- AccountStateProvider -------------------------------------------
    #
    # Authelia's users_database.yml supports a ``disabled: true`` flag
    # per user. When set, its auth middleware rejects the user at the
    # next request with 403 regardless of password correctness. Flag
    # changes are picked up by the watch: true config option within
    # a few seconds, so no explicit reload is needed on this path.

    def disable_user(self, external_id: str) -> None:
        self._set_disabled(external_id, True)

    def enable_user(self, external_id: str) -> None:
        self._set_disabled(external_id, False)

    def is_disabled(self, external_id: str) -> bool:
        if not self._path.is_file():
            return False
        import yaml
        try:
            data = yaml.safe_load(self._path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError:
            return False
        entry = (data.get("users", {}) or {}).get(external_id)
        if not isinstance(entry, dict):
            return False
        return bool(entry.get("disabled", False))

    def _set_disabled(self, external_id: str, disabled: bool) -> None:
        def _mutate(current: dict[str, Any]) -> dict[str, Any]:
            users = dict(current.get("users") or {})
            entry = users.get(external_id)
            if not isinstance(entry, dict):
                raise AutheliaProviderError(
                    f"user {external_id!r} not found",
                )
            entry = dict(entry)
            if disabled:
                entry["disabled"] = True
            else:
                entry.pop("disabled", None)
            users[external_id] = entry
            new = dict(current)
            new["users"] = users
            return new

        self._editor().edit(_mutate)

    # ---- MFAStateProvider (best-effort, file backend) ------------------

    def mfa_state(self, external_id: str) -> MFAState:
        """File backend reports MFA only from the ``groups`` heuristic.

        Authelia's real MFA state (TOTP secrets, WebAuthn creds) lives
        in the Authelia sqlite DB, which we don't touch from this
        provider. A dedicated ``AutheliaSessionAdmin`` reads it — see
        the planned ``services.apps.authelia.session_admin`` module.
        For users, returning ``MFAState.none()`` here is the correct
        conservative answer: "don't claim MFA we can't verify."
        """
        del external_id
        return MFAState.none()

    # ---- APITokenProvider ------------------------------------------------

    def list_api_tokens(self, external_id: str) -> list[APIToken]:
        """Authelia file-backend does not issue long-lived API tokens.

        OIDC short-lived tokens are minted on the fly by Authelia's
        OIDC provider and are not persisted in a way the file backend
        can enumerate. A dedicated session-admin impl handles the
        OIDC surface; this one reports no tokens.
        """
        del external_id
        return []

    def revoke_api_token(self, external_id: str, token_id: str) -> None:
        del external_id, token_id
