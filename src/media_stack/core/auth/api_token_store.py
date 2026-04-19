"""Persistent store for API bearer tokens.

Tokens are stored hashed (SHA-256) so the JSON file is useless if
leaked. The plaintext token is returned once at create time and never
persisted. Each token records the owning user, a display name, optional
scope, an expiry timestamp, and a last-used timestamp for auditing.

Scopes:
- ``admin`` — full controller access (equivalent to logging in as the
  owning user).
- ``read``  — GETs only; any mutating POST returns 403.

Kinds:
- ``long_lived`` — single token, no refresh, optional TTL. The legacy
  path used by CI / automation that mints once and rotates manually.
- ``access`` — short-lived (minutes). Used to authenticate requests.
  Part of a family with a paired refresh token.
- ``refresh`` — long-lived (days). Only valid at POST /api/tokens/refresh
  to mint a new access + refresh pair. Each refresh use ROTATES the
  refresh token (old is revoked) so a stolen refresh has at most one
  unauthorized use before the legitimate holder detects it (they'll
  get "refresh token revoked" on their next rotation).

Family revocation: all tokens with the same ``family_id`` can be
revoked in one call (``revoke_family``). Use when a refresh leaks —
the whole chain is killed regardless of how many rotations have
happened.

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
_VALID_KINDS = frozenset({"long_lived", "access", "refresh"})
_TOKEN_BYTES = 32  # 256 bits of entropy, base64url-encoded
_ISO_FMT = "%Y-%m-%dT%H:%M:%SZ"

_DEFAULT_ACCESS_TTL_SECONDS = 15 * 60        # 15 min
_DEFAULT_REFRESH_TTL_SECONDS = 30 * 24 * 60 * 60  # 30 days


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
    kind: str = "long_lived"
    family_id: str = ""     # groups an access+refresh pair + their rotations
    parent_id: str = ""     # previous refresh that minted this one

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
            "kind": self.kind,
            "family_id": self.family_id,
            "parent_id": self.parent_id,
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
                kind=str(rec.get("kind", "long_lived")),
                family_id=str(rec.get("family_id", "")),
                parent_id=str(rec.get("parent_id", "")),
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
                _ISO_FMT, time.gmtime(ts + ttl_seconds),
            )
        token = ApiToken(
            id=tid,
            token_hash=self.hash_token(plaintext),
            owner_username=owner_username,
            name=name,
            scope=scope,
            created_at=time.strftime(_ISO_FMT, time.gmtime(ts)),
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
        """Return the matching ApiToken if the plaintext is valid, live,
        and NOT a refresh token. Refresh tokens can only be presented
        to the rotate() endpoint — they never authenticate API calls
        directly, which prevents a stolen refresh from being used as
        a long-lived admin token.
        """
        tok = self._lookup(plaintext, now=now)
        if tok is None or tok.kind == "refresh":
            return None
        return tok

    def _lookup(self, plaintext: str, *,
                now: float | None = None) -> ApiToken | None:
        """Internal: match plaintext → ApiToken ignoring kind.
        Callers decide whether the token's kind is acceptable."""
        if not plaintext or len(plaintext) < 16:
            return None
        needle = self.hash_token(plaintext)
        ts = time.time() if now is None else float(now)
        iso_now = time.strftime(_ISO_FMT, time.gmtime(ts))
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

    def mint_pair(self, *, owner_username: str, name: str,
                  scope: str = "admin",
                  access_ttl_seconds: int = _DEFAULT_ACCESS_TTL_SECONDS,
                  refresh_ttl_seconds: int = _DEFAULT_REFRESH_TTL_SECONDS,
                  now: float | None = None,
                  ) -> tuple[tuple[ApiToken, str], tuple[ApiToken, str]]:
        """Mint a new (access, refresh) token pair. Both share a
        family_id so they can be revoked together. Returns a pair of
        (ApiToken, plaintext) tuples — plaintext only returned here.
        """
        if scope not in _VALID_SCOPES:
            raise ValueError(f"invalid scope '{scope}'")
        if not owner_username or not name:
            raise ValueError("owner_username and name are required")
        ts = time.time() if now is None else float(now)
        family_id = str(uuid.uuid4())
        access, access_plain = self._make_token(
            owner_username=owner_username, name=f"{name} (access)",
            scope=scope, kind="access", family_id=family_id,
            parent_id="", ts=ts, ttl_seconds=access_ttl_seconds,
        )
        refresh, refresh_plain = self._make_token(
            owner_username=owner_username, name=f"{name} (refresh)",
            scope=scope, kind="refresh", family_id=family_id,
            parent_id="", ts=ts, ttl_seconds=refresh_ttl_seconds,
        )
        with self._lock:
            tokens = dict(self._load())
            tokens[access.id] = access
            tokens[refresh.id] = refresh
            self._save(tokens)
        return (access, access_plain), (refresh, refresh_plain)

    def rotate(self, refresh_plaintext: str, *,
               access_ttl_seconds: int = _DEFAULT_ACCESS_TTL_SECONDS,
               refresh_ttl_seconds: int = _DEFAULT_REFRESH_TTL_SECONDS,
               now: float | None = None,
               ) -> tuple[tuple[ApiToken, str], tuple[ApiToken, str]] | None:
        """Exchange a refresh token for a new (access, refresh) pair.

        The presented refresh token is revoked in the same operation
        (refresh-token rotation) — if the presented token has already
        been used once, a replay returns None AND we revoke the entire
        family as a paranoid response to a suspected leak.
        """
        tok = self._lookup(refresh_plaintext, now=now)
        if tok is None or tok.kind != "refresh":
            # Even if we can't identify the family here, returning None
            # short-circuits the attacker.
            return None
        ts = time.time() if now is None else float(now)
        access, access_plain = self._make_token(
            owner_username=tok.owner_username,
            name=tok.name.replace("(refresh)", "(access)") or "rotated (access)",
            scope=tok.scope, kind="access", family_id=tok.family_id,
            parent_id=tok.id, ts=ts, ttl_seconds=access_ttl_seconds,
        )
        new_refresh, refresh_plain = self._make_token(
            owner_username=tok.owner_username, name=tok.name,
            scope=tok.scope, kind="refresh", family_id=tok.family_id,
            parent_id=tok.id, ts=ts, ttl_seconds=refresh_ttl_seconds,
        )
        with self._lock:
            tokens = dict(self._load())
            old = tokens.get(tok.id)
            if old is not None:
                old.revoked = True
            tokens[access.id] = access
            tokens[new_refresh.id] = new_refresh
            self._save(tokens)
        return (access, access_plain), (new_refresh, refresh_plain)

    def _make_token(self, *, owner_username: str, name: str, scope: str,
                    kind: str, family_id: str, parent_id: str,
                    ts: float, ttl_seconds: int) -> tuple[ApiToken, str]:
        plaintext = secrets.token_urlsafe(_TOKEN_BYTES)
        tid = str(uuid.uuid4())
        expires_at = ""
        if ttl_seconds > 0:
            expires_at = time.strftime(
                _ISO_FMT, time.gmtime(ts + ttl_seconds),
            )
        token = ApiToken(
            id=tid, token_hash=self.hash_token(plaintext),
            owner_username=owner_username, name=name, scope=scope,
            created_at=time.strftime(_ISO_FMT, time.gmtime(ts)),
            expires_at=expires_at, kind=kind, family_id=family_id,
            parent_id=parent_id,
        )
        return token, plaintext

    def revoke(self, token_id: str) -> bool:
        with self._lock:
            tokens = dict(self._load())
            tok = tokens.get(token_id)
            if tok is None or tok.revoked:
                return False
            tok.revoked = True
            self._save(tokens)
            return True

    def revoke_family(self, family_id: str) -> int:
        """Revoke every live token sharing this family_id. Returns the
        number of tokens that flipped to revoked. Intended use: a
        refresh token leaks → burn the whole family."""
        if not family_id:
            return 0
        count = 0
        with self._lock:
            tokens = dict(self._load())
            for tok in tokens.values():
                if tok.family_id == family_id and not tok.revoked:
                    tok.revoked = True
                    count += 1
            if count:
                self._save(tokens)
        return count
