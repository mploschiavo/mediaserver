"""Controller session store.

Session cookies replace (and eventually retire) basic auth for browser
users. A successful POST /api/auth/login mints an opaque random
session token, stores its SHA-256 hash + owner + expiry in an in-memory
map, and returns the plaintext as a ``Set-Cookie: ms_session=…; HttpOnly;
Secure; SameSite=Strict`` response. Every subsequent request presents
the cookie; we hash it and look up the record.

Sessions are in-memory on purpose — a controller restart logs
everyone out, which is desired after a redeploy. For durability across
reboots we'd add JSON persistence mirroring ApiTokenStore.
"""

from __future__ import annotations

import hashlib
import secrets
import threading
import time
from dataclasses import dataclass


_TOKEN_BYTES = 32  # 256 bits of entropy


@dataclass
class Session:
    id: str
    token_hash: str
    owner_username: str
    created_at: float
    expires_at: float  # absolute seconds since epoch; 0 = never expires
    last_used_at: float = 0.0


class SessionStore:
    """In-memory session table with lazy expiry + idle timeout.

    Two TTLs stack:
      - ``default_ttl_seconds`` caps the ABSOLUTE session lifetime
        (8h by default). No matter how active a user is, the session
        dies at this horizon. Re-login required.
      - ``idle_ttl_seconds`` caps INACTIVITY. On every ``get()`` we
        update ``last_used_at``; if now - last_used_at exceeds this
        window, the session is treated as expired. Default 30 min.
        Set to 0 to disable idle timeout.
    """

    def __init__(self, *, default_ttl_seconds: int = 8 * 60 * 60,
                 idle_ttl_seconds: int = 30 * 60,
                 absolute_cap: int = 100 * 100) -> None:
        self._lock = threading.Lock()
        self._sessions: dict[str, Session] = {}  # keyed by token_hash
        self._default_ttl = max(60, int(default_ttl_seconds))
        self._idle_ttl = max(0, int(idle_ttl_seconds))
        self._cap = max(100, int(absolute_cap))

    def hash_token(self, plaintext: str) -> str:
        return hashlib.sha256(plaintext.encode("utf-8")).hexdigest()

    def create(self, owner_username: str, *, ttl_seconds: int | None = None,
               now: float | None = None) -> tuple[Session, str]:
        if not owner_username:
            raise ValueError("owner_username required")
        ts = time.time() if now is None else float(now)
        ttl = self._default_ttl if ttl_seconds is None else max(60, int(ttl_seconds))
        plaintext = secrets.token_urlsafe(_TOKEN_BYTES)
        token_hash = self.hash_token(plaintext)
        session = Session(
            id=secrets.token_urlsafe(12),
            token_hash=token_hash,
            owner_username=owner_username,
            created_at=ts,
            expires_at=ts + ttl,
            last_used_at=ts,
        )
        with self._lock:
            self._evict_expired(ts)
            if len(self._sessions) >= self._cap:
                # Evict the oldest entry to bound growth under pathological load.
                oldest_key = min(self._sessions,
                                  key=lambda k: self._sessions[k].created_at)
                del self._sessions[oldest_key]
            self._sessions[token_hash] = session
        return session, plaintext

    def get(self, plaintext: str, *, now: float | None = None) -> Session | None:
        if not plaintext:
            return None
        ts = time.time() if now is None else float(now)
        needle = self.hash_token(plaintext)
        with self._lock:
            sess = self._sessions.get(needle)
            if sess is None:
                return None
            if sess.expires_at and sess.expires_at <= ts:
                self._sessions.pop(needle, None)
                return None
            if (self._idle_ttl and sess.last_used_at
                    and (ts - sess.last_used_at) > self._idle_ttl):
                # Session has been idle past the cutoff — kill it.
                self._sessions.pop(needle, None)
                return None
            sess.last_used_at = ts
            return sess

    def revoke(self, plaintext: str) -> bool:
        if not plaintext:
            return False
        with self._lock:
            return self._sessions.pop(self.hash_token(plaintext), None) is not None

    def revoke_all_for(self, owner_username: str) -> int:
        with self._lock:
            matches = [k for k, s in self._sessions.items()
                       if s.owner_username == owner_username]
            for k in matches:
                self._sessions.pop(k, None)
            return len(matches)

    def count(self) -> int:
        with self._lock:
            return len(self._sessions)

    def _evict_expired(self, now_ts: float) -> None:
        dead = [k for k, s in self._sessions.items()
                if s.expires_at and s.expires_at <= now_ts]
        for k in dead:
            self._sessions.pop(k, None)
