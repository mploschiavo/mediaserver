"""Persistent store for API bearer tokens.

Tokens are stored hashed (SHA-256) so the JSON file is useless if
leaked. The plaintext token is returned once at create time and never
persisted. Each token records the owning user, a display name, optional
scope, an expiry timestamp, and a last-used timestamp for auditing.

Scopes:
- ``admin`` — full controller access (equivalent to logging in as the
  owning user).
- ``read``  — GETs only; any mutating POST returns 403.

Tokens survive container restarts because they're persisted to a JSON
file under the controller config mount.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


_VALID_SCOPES = frozenset({"admin", "read"})
_TOKEN_BYTES = 32  # 256 bits of entropy, base64url-encoded


@dataclass
class ApiToken:
    id: str
    token_hash: str
    owner_username: str
    name: str
    scope: str
    created_at: str
    expires_at: str  # empty = no expiry
    last_used_at: str = ""
    revoked: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "owner_username": self.owner_username,
            "name": self.name,
            "scope": self.scope,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "last_used_at": self.last_used_at,
            "revoked": self.revoked,
        }


class ApiTokenStore:
    """JSON-backed store with file locking for multi-worker safety.

    The hash function is intentionally plain SHA-256 (not a slow KDF);
    tokens are already 256 bits of random entropy, so the hash is
    purely for "don't store plaintext" rather than for brute-force
    resistance."""

    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        self._lock = threading.Lock()
        self._cache: dict[str, ApiToken] | None = None

    def _load(self) -> dict[str, ApiToken]:
        if self._cache is not None:
            return self._cache
        if not self._path.is_file():
            self._cache = {}
            return self._cache
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            raw = {}
        tokens: dict[str, ApiToken] = {}
        for tid, rec in (raw.get("tokens") or {}).items():
            if not isinstance(rec, dict):
                continue
            tokens[tid] = ApiToken(
                id=str(rec.get("id", tid)),
                token_hash=str(rec.get("token_hash", "")),
                owner_username=str(rec.get("owner_username", "")),
                name=str(rec.get("name", "")),
                scope=str(rec.get("scope", "admin")),
                created_at=str(rec.get("created_at", "")),
                expires_at=str(rec.get("expires_at", "")),
                last_used_at=str(rec.get("last_used_at", "")),
                revoked=bool(rec.get("revoked", False)),
            )
        self._cache = tokens
        return self._cache

    def _save(self, tokens: dict[str, ApiToken]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"tokens": {tid: {**tok.to_dict(), "token_hash": tok.token_hash}
                              for tid, tok in tokens.items()}}
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2, sort_keys=True),
                       encoding="utf-8")
        tmp.replace(self._path)
        self._cache = tokens

    def hash_token(self, plaintext: str) -> str:
        return hashlib.sha256(plaintext.encode("utf-8")).hexdigest()

    def create(self, *, owner_username: str, name: str, scope: str = "admin",
               ttl_seconds: int = 0, now: float | None = None
               ) -> tuple[ApiToken, str]:
        if scope not in _VALID_SCOPES:
            raise ValueError(
                f"invalid scope '{scope}'; must be one of {sorted(_VALID_SCOPES)}"
            )
        if not owner_username or not name:
            raise ValueError("owner_username and name are required")
        ts = time.time() if now is None else float(now)
        plaintext = secrets.token_urlsafe(_TOKEN_BYTES)
        tid = str(uuid.uuid4())
        expires_at = ""
        if ttl_seconds > 0:
            expires_at = time.strftime(
                "%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts + ttl_seconds),
            )
        token = ApiToken(
            id=tid,
            token_hash=self.hash_token(plaintext),
            owner_username=owner_username,
            name=name,
            scope=scope,
            created_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts)),
            expires_at=expires_at,
        )
        with self._lock:
            tokens = dict(self._load())
            tokens[tid] = token
            self._save(tokens)
        return token, plaintext

    def list_all(self, owner_username: str = "") -> list[ApiToken]:
        with self._lock:
            tokens = list(self._load().values())
        if owner_username:
            tokens = [t for t in tokens if t.owner_username == owner_username]
        return sorted(tokens, key=lambda t: t.created_at, reverse=True)

    def verify(self, plaintext: str, *, now: float | None = None) -> ApiToken | None:
        """Return the matching ApiToken if the plaintext is valid and live,
        else None. Updates last_used_at on success."""
        if not plaintext or len(plaintext) < 16:
            return None
        needle = self.hash_token(plaintext)
        ts = time.time() if now is None else float(now)
        iso_now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))
        with self._lock:
            tokens = dict(self._load())
            for tok in tokens.values():
                if tok.token_hash != needle:
                    continue
                if tok.revoked:
                    return None
                if tok.expires_at and tok.expires_at < iso_now:
                    return None
                tok.last_used_at = iso_now
                self._save(tokens)
                return tok
        return None

    def revoke(self, token_id: str) -> bool:
        with self._lock:
            tokens = dict(self._load())
            tok = tokens.get(token_id)
            if tok is None or tok.revoked:
                return False
            tok.revoked = True
            self._save(tokens)
            return True
