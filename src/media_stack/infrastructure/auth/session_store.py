"""Controller session store.

Session cookies replace (and eventually retire) basic auth for browser
users. A successful POST /api/auth/login mints an opaque random
session token, stores its SHA-256 hash + owner + expiry in an in-memory
map, and returns the plaintext as a ``Set-Cookie: ms_session=...; HttpOnly;
Secure; SameSite=Strict`` response. Every subsequent request presents
the cookie; we hash it and look up the record.

Sessions are in-memory on purpose -- a controller restart logs
everyone out, which is desired after a redeploy. For durability across
reboots we'd add JSON persistence mirroring ApiTokenStore.

Session-visibility extensions
-----------------------------
Beyond the minimum "create / get / revoke" surface, this module
records enough per-session metadata to power the admin
"sessions/active" UI (which device? which /24? idle for how long?)
and to bind a cookie to its originating IP prefix + device class so
that a stolen cookie replayed from a different network or form
factor is rejected instead of silently accepted. See
``verify_binding`` and ``BindingStatus``.
"""

from __future__ import annotations

import hashlib
import ipaddress
import secrets
import threading
import time
from dataclasses import dataclass
from enum import Enum

from media_stack.domain.auth.users.device_classifier import classify_class
from media_stack.core.time_utils import utcnow_iso


_TOKEN_BYTES = 32  # 256 bits of entropy

# Default prefix widths for session-token binding. IPv4 /24 tolerates
# the typical carrier-grade-NAT churn a mobile client sees on the same
# tower; IPv6 /48 matches the routing boundary most ISPs hand out per
# site so the prefix is stable across internal SLAAC re-rolls.
_DEFAULT_V4_PREFIX_BITS = 24
_DEFAULT_V6_PREFIX_BITS = 48


def ip_prefix_for(ip: str, *, v4_bits: int = _DEFAULT_V4_PREFIX_BITS,
                  v6_bits: int = _DEFAULT_V6_PREFIX_BITS) -> str:
    """Return the network-prefix CIDR that brackets a single IP.

    The result is the ``str(ip_network(..., strict=False))`` canonical
    form, e.g. ``"203.0.113.0/24"`` for an IPv4 host or
    ``"2001:db8::/48"`` for an IPv6 host. We truncate to a coarse
    prefix on purpose: a stable binding key must survive a mobile
    client moving between cell towers on the same carrier ASN, a
    residential ISP rotating the last octet on DHCP lease renewal,
    or IPv6 temporary-address regeneration every few hours. /24 and
    /48 are the empirical cut points where those churn sources stop
    and intentional network moves begin.

    Args:
        ip: The observed client IP as a string. Leading/trailing
            whitespace is tolerated. Anything that isn't a valid
            IPv4 or IPv6 literal returns ``""`` rather than raising
            -- the callers (login path, session binding) cannot abort
            on a malformed header from an untrusted client.
        v4_bits: Prefix width for IPv4 (default 24).
        v6_bits: Prefix width for IPv6 (default 48).

    Returns:
        A CIDR string, or ``""`` if the input is not parseable.
    """
    if not isinstance(ip, str):
        return ""
    text = ip.strip()
    if not text:
        return ""
    try:
        addr = ipaddress.ip_address(text)
    except ValueError:
        return ""
    try:
        if isinstance(addr, ipaddress.IPv4Address):
            network = ipaddress.ip_network(f"{addr}/{int(v4_bits)}",
                                            strict=False)
        else:
            network = ipaddress.ip_network(f"{addr}/{int(v6_bits)}",
                                            strict=False)
    except (ValueError, TypeError):
        return ""
    return str(network)


class BindingStatus(str, Enum):
    """Outcome of ``SessionStore.verify_binding``.

    Subclasses ``str`` so the value is JSON-serialisable without a
    custom encoder -- the security-counter layer records these
    directly in structured logs.
    """

    OK = "OK"
    IP_MISMATCH = "IP_MISMATCH"
    DEVICE_MISMATCH = "DEVICE_MISMATCH"
    UNKNOWN_SESSION = "UNKNOWN_SESSION"


@dataclass
class Session:
    """One active browser session.

    ``created_at`` is ISO-8601 zulu for the audit trail; ``expires_at``
    and ``last_used_at`` stay as epoch floats because they participate
    in numeric TTL arithmetic on the hot path and converting both
    directions on every request would be wasteful. The binding fields
    (``ip_prefix``, ``device_class``, ``user_agent``) are stored even
    when empty so downstream renderers can count on the attributes
    being present on every record.
    """

    id: str
    token_hash: str
    owner_username: str
    created_at: str
    expires_at: float  # absolute seconds since epoch; 0 = never expires
    last_used_at: float = 0.0
    ip_prefix: str = ""
    device_class: str = ""
    user_agent: str = ""
    logout_reason: str = ""


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
        # RLock (not Lock) because verify_binding + list_all_active do
        # work that would otherwise be naturally split into helpers
        # that each re-acquire; a re-entrant lock keeps those helpers
        # readable without risking self-deadlock.
        self._lock = threading.RLock()
        self._sessions: dict[str, Session] = {}  # keyed by token_hash
        self._default_ttl = max(60, int(default_ttl_seconds))
        self._idle_ttl = max(0, int(idle_ttl_seconds))
        self._cap = max(100, int(absolute_cap))

    def hash_token(self, plaintext: str) -> str:
        return hashlib.sha256(plaintext.encode("utf-8")).hexdigest()

    def create(self, owner_username: str, *, ttl_seconds: int | None = None,
               now: float | None = None,
               client_ip: str = "",
               user_agent: str = "") -> tuple[Session, str]:
        """Mint a new session and return ``(record, plaintext_token)``.

        ``client_ip`` and ``user_agent`` are optional: legacy callers
        that only know the username still work, and the session
        record simply carries empty binding fields -- which
        ``verify_binding`` treats as "nothing to compare against, pass
        whatever matches the empty baseline". The moment a caller
        starts passing them, binding enforcement activates for that
        session.
        """
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
            created_at=utcnow_iso(),
            expires_at=ts + ttl,
            last_used_at=ts,
            ip_prefix=ip_prefix_for(client_ip) if client_ip else "",
            device_class=(classify_class(user_agent).value
                          if user_agent else ""),
            user_agent=user_agent or "",
        )
        with self._lock:
            self._evict_expired(ts)
            if len(self._sessions) >= self._cap:
                # Evict the oldest entry to bound growth under pathological
                # load. ``created_at`` is an ISO string and sorts
                # lexically in the same order as wall-clock time.
                oldest_key = min(self._sessions,
                                  key=lambda k: self._sessions[k].created_at)
                del self._sessions[oldest_key]
            self._sessions[token_hash] = session
        return session, plaintext

    def revoke(self, plaintext: str) -> bool:
        if not plaintext:
            return False
        with self._lock:
            return self._sessions.pop(self.hash_token(plaintext), None) is not None

    def revoke_by_id(self, session_id: str, *,
                     reason: str = "admin_revoke") -> bool:
        """Remove a single session referenced by its public ``id``.

        Returns ``True`` if the session existed (and is now gone),
        ``False`` if nothing matched. The caller's reason string is
        stamped onto the ephemeral record before it is popped so any
        downstream observer holding a reference (event-bus consumers
        in the admin audit layer) can read it.
        """
        if not session_id:
            return False
        with self._lock:
            for key, sess in self._sessions.items():
                if sess.id == session_id:
                    sess.logout_reason = reason or "admin_revoke"
                    self._sessions.pop(key, None)
                    return True
        return False

    def revoke_all_for(self, owner_username: str) -> int:
        with self._lock:
            matches = [k for k, s in self._sessions.items()
                       if s.owner_username == owner_username]
            for k in matches:
                self._sessions.pop(k, None)
            return len(matches)

    def revoke_all(self, *, reason: str = "emergency_revoke") -> list[str]:
        """Terminate every live session. Returns the list of session
        ``id``s that were revoked.

        Used by the incident-response emergency-revoke endpoint. The
        ``reason`` is stamped on each record before pop so any
        in-flight observer can correlate the terminations with the
        triggering event.
        """
        with self._lock:
            ids: list[str] = []
            for key, sess in list(self._sessions.items()):
                sess.logout_reason = reason or "emergency_revoke"
                ids.append(sess.id)
                self._sessions.pop(key, None)
            return ids

    def list_for(self, username: str) -> list[Session]:
        """All currently-valid sessions owned by ``username``.

        Expired sessions are filtered (and opportunistically purged)
        so the admin UI never has to decide how to render a record
        that is technically dead.
        """
        if not username:
            return []
        ts = time.time()
        with self._lock:
            self._evict_expired(ts)
            return [s for s in self._sessions.values()
                    if s.owner_username == username
                    and not self._is_expired(s, ts)]

    def list_all_active(self) -> list[Session]:
        """Every live session, most-recently-active first.

        The admin "sessions/active" view sorts by ``last_used_at``
        descending because "who's doing something right now" is the
        most useful ordering when triaging a suspected session
        takeover. Ties fall back to ``created_at`` (newer first) so
        the order is stable when two sessions share a single-second
        ``last_used_at`` bucket.
        """
        ts = time.time()
        with self._lock:
            self._evict_expired(ts)
            live = [s for s in self._sessions.values()
                    if not self._is_expired(s, ts)]
        live.sort(key=lambda s: (s.last_used_at, s.created_at), reverse=True)
        return live

    def get(self, plaintext_or_id: str, *,
            now: float | None = None) -> Session | None:
        """Resolve a session.

        Overload on the single positional arg:
        - If the value matches the hash of a stored token, treat it as
          a plaintext cookie and slide the idle timer.
        - Otherwise, fall back to a by-id lookup (read-only).

        This keeps the hot cookie path unchanged while letting admin
        tooling call ``store.get(session_id)`` without a second
        method name. Old callers that always passed plaintext keep
        their exact previous behaviour.
        """
        if not plaintext_or_id:
            return None
        ts = time.time() if now is None else float(now)
        needle = self.hash_token(plaintext_or_id)
        with self._lock:
            sess = self._sessions.get(needle)
            if sess is not None:
                if sess.expires_at and sess.expires_at <= ts:
                    self._sessions.pop(needle, None)
                    return None
                if (self._idle_ttl and sess.last_used_at
                        and (ts - sess.last_used_at) > self._idle_ttl):
                    self._sessions.pop(needle, None)
                    return None
                sess.last_used_at = ts
                return sess
            # Fall through to id lookup. Admin paths call this with
            # the short session id; no idle-timer update there.
            for candidate in self._sessions.values():
                if candidate.id == plaintext_or_id:
                    if self._is_expired(candidate, ts):
                        return None
                    return candidate
        return None

    def verify_binding(self, token: str, *,
                       observed_ip: str,
                       observed_user_agent: str) -> BindingStatus:
        """Check that a presented cookie is being replayed from the
        network prefix and device class it was issued to.

        Returns:
            - ``OK`` if both binding dimensions match (or were never
              recorded, i.e. legacy session).
            - ``IP_MISMATCH`` if the stored prefix was non-empty and
              the observed IP falls into a different prefix.
            - ``DEVICE_MISMATCH`` if the stored device class was
              non-empty and the observed UA classifies differently.
            - ``UNKNOWN_SESSION`` if the token does not resolve
              (wrong hash, expired, or idle-timed-out).

        IP_MISMATCH is checked before DEVICE_MISMATCH because the IP
        signal is stronger -- a cookie being replayed from a new /24
        is very likely stolen, whereas a UA-class change can also
        happen when an admin re-parses an old UA with new rules.
        """
        if not token:
            return BindingStatus.UNKNOWN_SESSION
        ts = time.time()
        needle = self.hash_token(token)
        with self._lock:
            sess = self._sessions.get(needle)
            if sess is None or self._is_expired(sess, ts):
                return BindingStatus.UNKNOWN_SESSION
            stored_prefix = sess.ip_prefix
            stored_class = sess.device_class
        if stored_prefix:
            observed_prefix = ip_prefix_for(observed_ip or "")
            if observed_prefix != stored_prefix:
                return BindingStatus.IP_MISMATCH
        if stored_class:
            observed_class = classify_class(observed_user_agent or "").value
            if observed_class != stored_class:
                return BindingStatus.DEVICE_MISMATCH
        return BindingStatus.OK

    def count(self) -> int:
        with self._lock:
            return len(self._sessions)

    # ---- internal helpers ------------------------------------------------

    def _is_expired(self, sess: Session, now_ts: float) -> bool:
        if sess.expires_at and sess.expires_at <= now_ts:
            return True
        if (self._idle_ttl and sess.last_used_at
                and (now_ts - sess.last_used_at) > self._idle_ttl):
            return True
        return False

    def _evict_expired(self, now_ts: float) -> None:
        dead = [k for k, s in self._sessions.items()
                if self._is_expired(s, now_ts)]
        for k in dead:
            self._sessions.pop(k, None)
