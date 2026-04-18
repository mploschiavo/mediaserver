"""JSON-file invite store.

An invite records an admin's intent to add a user without exposing a
password out-of-band. The admin generates an invite; the user visits
the link, sets their own password, and the controller creates their
account on the fly.

Security model:
- The token is shown to the admin ONCE at creation and never stored in
  plaintext; only its SHA-256 hash is persisted.
- Single-use: accepted_at != "" marks it consumed.
- TTL (default 7 days) is enforced at consume time.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import tempfile
import threading
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

from media_stack.core.auth.users.models import Invite


_DEFAULT_TTL_HOURS = 24


class InviteStore:

    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        self._lock = threading.Lock()
        self._cache: dict[str, Invite] | None = None

    def _now_iso(self) -> str:
        return datetime.now(timezone.utc).isoformat(timespec="seconds")

    def hash_token(self, token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def _load(self) -> dict[str, Invite]:
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
        invites: dict[str, Invite] = {}
        for row in data.get("invites", []):
            try:
                inv = Invite(
                    id=str(row["id"]),
                    email=str(row.get("email", "")),
                    role_slug=str(row.get("role_slug", "")),
                    created_by=str(row.get("created_by", "")),
                    created_at=str(row.get("created_at", "")),
                    expires_at=str(row.get("expires_at", "")),
                    token_hash=str(row.get("token_hash", "")),
                    accepted_at=str(row.get("accepted_at", "")),
                )
                invites[inv.id] = inv
            except (KeyError, ValueError):
                continue
        self._cache = invites
        return self._cache

    def _write(self, invites: dict[str, Invite]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "updated_at": self._now_iso(),
            "invites": [i.to_dict() for i in invites.values()],
        }
        fd, tmp_path = tempfile.mkstemp(
            prefix="invites.", suffix=".json.tmp", dir=str(self._path.parent),
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

    def create(self, *, email: str, role_slug: str, created_by: str,
               ttl_hours: int = _DEFAULT_TTL_HOURS) -> tuple[Invite, str]:
        """Create an invite, return (invite, plaintext_token).

        The plaintext token is NOT stored; it's only returned to the caller.
        """
        token = secrets.token_urlsafe(24)
        now = datetime.now(timezone.utc)
        exp = now + timedelta(hours=max(1, int(ttl_hours)))
        invite = Invite(
            id=str(uuid.uuid4()),
            email=email.strip(),
            role_slug=role_slug,
            created_by=created_by,
            created_at=now.isoformat(timespec="seconds"),
            expires_at=exp.isoformat(timespec="seconds"),
            token_hash=self.hash_token(token),
        )
        with self._lock:
            invites = self._load()
            invites[invite.id] = invite
            self._write(invites)
        return invite, token

    def find_by_token(self, token: str) -> Invite | None:
        target = self.hash_token(token)
        with self._lock:
            for inv in self._load().values():
                if inv.token_hash == target:
                    return inv
        return None

    def accept(self, invite_id: str) -> Invite:
        """Mark the invite as consumed. Caller must verify expiry + token
        separately before calling this.
        """
        with self._lock:
            invites = self._load()
            inv = invites.get(invite_id)
            if not inv:
                raise KeyError(f"unknown invite: {invite_id}")
            if inv.accepted_at:
                raise ValueError(f"invite {invite_id} already accepted")
            inv.accepted_at = self._now_iso()
            self._write(invites)
            return inv

    def list_pending(self) -> list[Invite]:
        now = datetime.now(timezone.utc)
        out: list[Invite] = []
        with self._lock:
            for inv in self._load().values():
                if inv.accepted_at:
                    continue
                if not self._is_future(inv.expires_at, now):
                    continue
                out.append(inv)
        return out

    def list_all(self) -> list[Invite]:
        with self._lock:
            return list(self._load().values())

    def _is_future(self, iso: str, now: datetime) -> bool:
        try:
            exp = datetime.fromisoformat(iso)
        except ValueError:
            return False
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        return exp > now

    def is_expired(self, invite: Invite) -> bool:
        return not self._is_future(invite.expires_at, datetime.now(timezone.utc))

    def revoke(self, invite_id: str) -> None:
        with self._lock:
            invites = self._load()
            if invite_id in invites:
                del invites[invite_id]
                self._write(invites)


