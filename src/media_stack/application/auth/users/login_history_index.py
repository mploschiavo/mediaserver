"""In-memory derived index for the admin Security tab.

The session-visibility UI needs to answer three questions on every
login (and on demand when an operator opens the Security panel):

  1. *Is this login from a new location for this user?* — the admin
     UI pins a small "new location" badge to the login row so the
     operator can spot first-time café / hotel sign-ins immediately.
  2. *How many concurrent sessions does this user have right now?* —
     an elevated count is not by itself a breach, but it is a signal
     worth surfacing alongside every fresh login.
  3. *Is this user's login pattern "impossible travel"?* — two
     successful logins from networks that are far apart, inside a
     tight time window, strongly implies credential theft or a shared
     account.

This module derives all three from two existing primitives — the
hash-chained ``AuditLog`` (durable, append-only, rotated at 10 MiB)
and the live ``SessionStore`` (in-memory, authoritative for
"currently connected"). Both are finalised upstream contracts; we
read from them and build an in-memory index that stays fresh via
``observe`` on new logins and can be rebuilt from disk on startup
(or on demand when a test or admin wants a clean slate).

Design notes
------------

* **Prefix-based "first seen" instead of full IP.** Two sign-ins
  from the same home ISP commonly differ in the last octet (DHCP
  lease rolls) but share the /24. We key the "seen" cache by the
  prefix returned from ``session_store.ip_prefix_for`` so the
  recurring home IP doesn't register as a "new location" on every
  lease renewal. For "impossible travel" we widen to /16 because
  mobile carriers hand out /24s that are geographically close but
  administratively distinct; /16 empirically corresponds to a
  single metro POP on the big carriers.
* **Impossible travel proxy, not geoip.** We deliberately avoid a
  geoip database — adding a runtime dep and a network side-channel
  for an already-best-effort signal is the wrong tradeoff. Prefix
  distance gets us "same metro vs. different metro" reliably
  enough to catch the canonical scenario (user logs in from home,
  then from another continent's NAT 60 seconds later) without the
  weight of a MaxMind file on disk.
* **Bounded per-user recent-login deque.** The impossible-travel
  check only cares about the tail of the user's recent login
  stream. Keeping a fixed-size deque (50) keeps detection O(1)
  per observe call and caps memory at O(users * 50) regardless of
  traffic.
* **Re-entrant lock.** ``observe`` and the three query methods can
  end up in a read-then-update sequence when they share internals
  with each other; an RLock keeps that ergonomic without splitting
  each public method into an outer/inner helper.
* **Protocol for downstream consumers.** The planned
  ``SessionAggregator`` will depend on this index to stamp every
  emitted ``LoginSucceeded`` event with ``first_seen_ip`` and
  ``concurrent_count``. It imports the *protocol*, not the class,
  so tests can inject a recording fake and the aggregator never
  needs to know whether the index is backed by the audit log or
  something else.
"""

from __future__ import annotations

import threading
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Protocol, runtime_checkable

from media_stack.core.auth.session_store import SessionStore, ip_prefix_for
from media_stack.domain.auth.users.audit_actions import AUTH_EVENTS, LOGIN_SUCCESS
from media_stack.core.auth.users.audit_log import AuditLog
from media_stack.core.time_utils import parse_iso

__all__ = [
    "LoginEvent",
    "LoginHistoryIndex",
    "LoginHistoryProtocol",
]


# Per-user cap on the recent-login ring buffer. 50 covers many hours
# of normal-user activity without growing unbounded; a spammy bot's
# burst is naturally clipped to this window which is exactly what the
# impossible-travel check wants (only the tail is informative).
_RECENT_LOGIN_CAP = 50

# Prefix widths: /24 for first-seen (stable against DHCP churn), /16
# for impossible-travel (widen so same-metro mobile POPs don't look
# "different"). IPv6 uses /48 and /32 respectively for the analogous
# reasons — /48 is the usual site boundary, /32 is a typical ISP
# allocation.
_FIRST_SEEN_V4_BITS = 24
_FIRST_SEEN_V6_BITS = 48
_TRAVEL_V4_BITS = 16
_TRAVEL_V6_BITS = 32


@dataclass(frozen=True)
class LoginEvent:
    """One successful login, reduced to the fields the index needs.

    ``ip_prefix_16`` / ``ip_prefix_24`` are precomputed so the
    impossible-travel and first-seen checks don't re-parse the IP
    on every call — the prefix conversions are cheap individually
    but add up on a rebuild over tens of thousands of audit rows.
    """

    ip_prefix_16: str
    ip_prefix_24: str
    ts_iso: str


@runtime_checkable
class LoginHistoryProtocol(Protocol):
    """Queryable surface of the login-history index.

    Defined so that downstream consumers (the planned
    ``SessionAggregator``) can depend on the interface rather than
    the concrete class — tests can substitute a recording fake
    that implements these four methods without standing up a real
    audit log + session store pair.
    """

    def observe(self, *, username: str, client_ip: str, ts_iso: str) -> None: ...

    def is_first_seen_ip(self, username: str, client_ip: str, *,
                         lookback_days: int = 90) -> bool: ...

    def concurrent_session_count(self, username: str) -> int: ...

    def anomaly_impossible_travel(self, username: str, *,
                                  window_minutes: int = 15) -> tuple[bool, str]: ...


class LoginHistoryIndex:
    """Derived index for "new location", "concurrent sessions", and
    "impossible travel" queries.

    The index is owned by the controller process; it rebuilds from
    the audit log at startup and stays warm via ``observe`` calls
    made from the login path. Rebuilding is O(n) in audit entries
    and the audit log is capped at 10 MiB by rotation, so a cold
    rebuild is consistently fast even at the tail of a log cycle.
    """

    def __init__(self, audit_log: AuditLog, session_store: SessionStore) -> None:
        self._audit = audit_log
        self._sessions = session_store
        # RLock, not Lock: ``observe`` updates both ``_seen_ips`` and
        # ``_recent_logins`` under a single critical section, and the
        # query methods each grab the same lock. Re-entrance keeps
        # the public methods from having to split into locked
        # internal helpers.
        self._lock = threading.RLock()
        self._seen_ips: dict[str, dict[str, str]] = {}
        self._recent_logins: dict[str, deque[LoginEvent]] = {}

    # ---- rebuild / observe ------------------------------------------------

    def rebuild(self) -> None:
        """Re-scan the audit log and rebuild the in-memory index.

        Called on controller startup and on demand (operator "rebuild
        index" button, tests). The method is safe to call at any
        time — it takes the lock once, wipes the caches, then walks
        the audit log in timestamp order. Entries that aren't a
        successful login, or that lack the fields we need, are
        skipped silently (the audit log is append-only and we don't
        want one malformed historical row to abort the scan).
        """
        with self._lock:
            self._seen_ips.clear()
            self._recent_logins.clear()
            for entry in self._audit.iter_entries():
                if entry.action not in AUTH_EVENTS:
                    continue
                if entry.action != LOGIN_SUCCESS:
                    continue
                username = entry.target or entry.actor
                if not username:
                    continue
                ip = entry.ip or ""
                if not ip:
                    continue
                self._record_locked(username=username, client_ip=ip,
                                    ts_iso=entry.timestamp)

    def observe(self, *, username: str, client_ip: str, ts_iso: str) -> None:
        """Record that ``username`` logged in from ``client_ip`` at ``ts_iso``.

        Must be called from the login path *after* a successful
        credential check, so the "new location" badge on the next
        query reflects this login. Malformed IPs and empty usernames
        are ignored — the audit log row will still carry the raw
        value for forensic purposes, but the index has nothing
        useful to derive from it.
        """
        if not username or not client_ip or not ts_iso:
            return
        with self._lock:
            self._record_locked(username=username, client_ip=client_ip,
                                ts_iso=ts_iso)

    def _record_locked(self, *, username: str, client_ip: str,
                       ts_iso: str) -> None:
        """Shared implementation for observe + rebuild. Caller owns the lock."""
        prefix_24 = ip_prefix_for(client_ip,
                                  v4_bits=_FIRST_SEEN_V4_BITS,
                                  v6_bits=_FIRST_SEEN_V6_BITS)
        prefix_16 = ip_prefix_for(client_ip,
                                  v4_bits=_TRAVEL_V4_BITS,
                                  v6_bits=_TRAVEL_V6_BITS)
        if not prefix_24:
            # Malformed IP — ``ip_prefix_for`` returns "" for either
            # prefix width in that case. Nothing to do.
            return
        # Update the "seen" cache: on a repeat visit we overwrite the
        # stored timestamp with the newer value so the lookback-day
        # window slides with real activity rather than the first
        # ever observation.
        seen = self._seen_ips.setdefault(username, {})
        prior = seen.get(prefix_24, "")
        if ts_iso > prior:
            seen[prefix_24] = ts_iso
        # Append to the bounded recent-login ring. ``deque(maxlen=N)``
        # discards from the left on overflow, which is exactly the
        # "only keep the tail" behaviour we want.
        ring = self._recent_logins.get(username)
        if ring is None:
            ring = deque(maxlen=_RECENT_LOGIN_CAP)
            self._recent_logins[username] = ring
        ring.append(LoginEvent(
            ip_prefix_16=prefix_16,
            ip_prefix_24=prefix_24,
            ts_iso=ts_iso,
        ))

    # ---- queries ----------------------------------------------------------

    def is_first_seen_ip(self, username: str, client_ip: str, *,
                         lookback_days: int = 90) -> bool:
        """Whether this ``(username, /24)`` pair is new in the window.

        Returns ``True`` if the user has never signed in from this
        /24 prefix within the last ``lookback_days``. A lookback of
        90 days is the default because it's long enough that an
        admin's recurring home IP keeps showing "known" across
        extended travel, but short enough that stale first-seen
        records (an office the user no longer visits) age out and
        correctly re-flag the next sign-in from the same place.
        """
        if not username or not client_ip:
            return False
        prefix_24 = ip_prefix_for(client_ip,
                                  v4_bits=_FIRST_SEEN_V4_BITS,
                                  v6_bits=_FIRST_SEEN_V6_BITS)
        if not prefix_24:
            return False
        with self._lock:
            seen = self._seen_ips.get(username)
            if not seen:
                return True
            last_ts = seen.get(prefix_24, "")
        if not last_ts:
            return True
        last_dt = parse_iso(last_ts)
        if last_dt is None:
            # Corrupted timestamp on a persisted row — treat the
            # prefix as "unknown" so the admin is notified rather
            # than silently trusted.
            return True
        # ``parse_iso`` returns an aware datetime; use its own
        # ``now`` for the comparison (via ``utcnow_iso`` would round-
        # trip through strings unnecessarily). We compute the cutoff
        # as ``last_dt + window`` and compare to current UTC — a
        # record older than that is treated as expired.
        cutoff = last_dt + timedelta(days=int(lookback_days))
        now = datetime.now(timezone.utc)
        return now > cutoff

    def concurrent_session_count(self, username: str) -> int:
        """Live session count for ``username`` right now.

        Delegates to ``session_store.list_for`` which already
        filters out expired / idle-timed-out sessions, so this
        method does no extra filtering of its own — the session
        store is the single source of truth for "still valid".
        """
        if not username:
            return 0
        return len(self._sessions.list_for(username))

    def anomaly_impossible_travel(self, username: str, *,
                                  window_minutes: int = 15) -> tuple[bool, str]:
        """Detect two logins in different /16s within the window.

        Returns ``(True, detail)`` when the most recent login and a
        prior login within ``window_minutes`` come from different
        /16 prefixes; ``(False, "")`` otherwise. ``detail`` is a
        human-readable summary suitable for an audit-log entry.

        The /16 threshold is a deliberate compromise between false
        positives (mobile carriers hand out /24s across a metro) and
        false negatives (a stolen cookie replayed from the same
        cloud provider as the user's VPN endpoint lands in the same
        /16 as the victim). The primary use case — "user logs in
        from home, then from another continent's NAT minutes later"
        — is comfortably outside any plausible /16 overlap.
        """
        if not username or window_minutes <= 0:
            return False, ""
        with self._lock:
            ring = self._recent_logins.get(username)
            if ring is None or len(ring) < 2:
                return False, ""
            events = list(ring)
        # The most recent login is the right anchor: impossible
        # travel is always "did this login happen too fast after the
        # last one?", not "is there any pair in history". Walking
        # back from the tail also lets us stop early once we leave
        # the time window.
        latest = events[-1]
        latest_dt = parse_iso(latest.ts_iso)
        if latest_dt is None:
            return False, ""
        window = timedelta(minutes=int(window_minutes))
        for prior in reversed(events[:-1]):
            prior_dt = parse_iso(prior.ts_iso)
            if prior_dt is None:
                continue
            delta = latest_dt - prior_dt
            if delta > window:
                # Older than the window — and the list is in append
                # order so everything before it is older still.
                break
            if (prior.ip_prefix_16 and latest.ip_prefix_16
                    and prior.ip_prefix_16 != latest.ip_prefix_16):
                detail = (
                    f"{prior.ip_prefix_16} -> {latest.ip_prefix_16}"
                    f" in {int(delta.total_seconds())}s"
                )
                return True, detail
        return False, ""
