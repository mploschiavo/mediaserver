"""JSON-file repository for controller-owned user metadata.

Authelia stays the source of truth for auth (username, password hash,
groups). This store tracks controller-side projections and provisioning
state the SSO backend does not know about: stable UUID, role slug,
last-login timestamp, per-provider external-ID refs.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from media_stack.core.auth.users.models import User, UserState

_SCHEMA_VERSION = 1


class UserStore:

    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        self._lock = threading.Lock()
        self._cache: dict[str, User] | None = None

    def _now_iso(self) -> str:
        return datetime.now(timezone.utc).isoformat(timespec="seconds")

    def _load(self) -> dict[str, User]:
        if self._cache is not None:
            return self._cache
        if not self._path.is_file():
            self._cache = {}
            return self._cache
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            self._cache = {}
            return self._cache
        users = {}
        for u in data.get("users", []):
            try:
                user = User.from_dict(u)
                users[user.id] = user
            except (KeyError, ValueError):
                continue
        self._cache = users
        return self._cache

    def _write(self, users: dict[str, User]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": _SCHEMA_VERSION,
            "updated_at": self._now_iso(),
            "users": [u.to_dict(include_sensitive=True) for u in users.values()],
        }
        fd, tmp_path = tempfile.mkstemp(
            prefix="users.", suffix=".json.tmp", dir=str(self._path.parent)
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, self._path)
        except Exception:
            Path(tmp_path).unlink(missing_ok=True)
            raise

    def reload(self) -> None:
        with self._lock:
            self._cache = None

    def list_all(self, include_deleted: bool = False) -> list[User]:
        with self._lock:
            users = list(self._load().values())
        if include_deleted:
            return users
        return [u for u in users if u.state != UserState.DELETED]

    def get(self, user_id: str) -> User | None:
        with self._lock:
            return self._load().get(user_id)

    def get_by_email(self, email: str) -> User | None:
        """Return the ACTIVE user with this email. Prefer active over
        soft-deleted when duplicates exist — same tie-breaker as
        get_by_username so logins via email can't hit a dead row."""
        target = email.strip().lower()
        with self._lock:
            candidates = [
                u for u in self._load().values()
                if u.email.strip().lower() == target
            ]
        if not candidates:
            return None
        for u in candidates:
            if u.state.value == "active":
                return u
        return candidates[0]

    def get_by_username(self, username: str) -> User | None:
        """Return the ACTIVE user with this username. If multiple
        records share the same username (a recreated-after-delete
        scenario — original row is soft-deleted, new row is active),
        return the active one, not the first match. Without this
        tie-breaker the login verifier reads the deleted row's
        empty provider_refs and rejects every credential."""
        target = username.strip().lower()
        with self._lock:
            candidates = [
                u for u in self._load().values()
                if u.username.strip().lower() == target
            ]
        if not candidates:
            return None
        for u in candidates:
            if u.state.value == "active":
                return u
        return candidates[0]

    def create(self, email: str, username: str, display_name: str, role_slug: str,
               state: UserState = UserState.ACTIVE, source: str = "") -> User:
        now = self._now_iso()
        user = User(
            id=str(uuid.uuid4()),
            email=email.strip(),
            username=username.strip(),
            display_name=display_name.strip() or username.strip(),
            state=state,
            role_slug=role_slug,
            created_at=now,
            updated_at=now,
            source=source,
        )
        with self._lock:
            users = self._load()
            for existing in users.values():
                if existing.state == UserState.DELETED:
                    continue
                if existing.email.lower() == user.email.lower():
                    raise ValueError(f"email already in use: {user.email}")
                if existing.username.lower() == user.username.lower():
                    raise ValueError(f"username already in use: {user.username}")
            users[user.id] = user
            self._write(users)
        return user

    def update(self, user_id: str, **fields: Any) -> User:
        allowed = {"email", "username", "display_name", "state", "role_slug",
                   "last_login_at", "provider_refs", "password_history",
                   "source"}
        with self._lock:
            users = self._load()
            user = users.get(user_id)
            if not user:
                raise KeyError(f"user {user_id} not found")
            for key, value in fields.items():
                self._apply_field(user, key, value, allowed)
            user.updated_at = self._now_iso()
            self._write(users)
        return user

    def _apply_field(self, user: User, key: str, value: Any, allowed: set) -> None:
        if key not in allowed:
            return
        if key == "state":
            user.state = value if isinstance(value, UserState) else UserState(value)
            return
        if key == "provider_refs":
            user.provider_refs = self._merge_provider_refs(user.provider_refs, value)
            return
        setattr(user, key, value)

    def _merge_provider_refs(self, existing: dict, new: Any) -> dict:
        if new is None:
            return {}
        merged = dict(existing)
        merged.update(new)
        return merged

    def soft_delete(self, user_id: str) -> User:
        return self.update(user_id, state=UserState.DELETED)

    def purge(self, user_id: str) -> None:
        with self._lock:
            users = self._load()
            if user_id in users:
                del users[user_id]
                self._write(users)
