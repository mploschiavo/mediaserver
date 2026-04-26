"""Admin-facing security report service.

Four small, high-signal reports derived from existing primitives:

* ``failed_login_clusters`` — group recent ``login_failure`` audit
  entries by /24 so credential-stuffing shows up as one row.
* ``new_location_alerts`` — recent successful logins on a
  ``(user, /24)`` pair not seen in the prior lookback period.
* ``concurrent_session_spikes`` — users holding ≥ a threshold of
  live sessions (shared-credential / takeover signal).
* ``login_history_for_user`` — raw ``AUTH_EVENTS`` trail for a
  single user, newest-first.

Every entry point is ``@requires_admin``. ``new_location_alerts``
delegates the "is this known?" decision to
``LoginHistoryIndex.is_first_seen_ip`` so the live badge + the
report always agree. ``concurrent_session_spikes`` works off the
aggregated session list so a user with one cookie and three
Jellyfin apps counts as four.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from media_stack.core.auth.authz import Actor, requires_admin
from media_stack.core.auth.session_store import ip_prefix_for
from media_stack.core.auth.users.audit_actions import (
    AUTH_EVENTS,
    LOGIN_FAILURE,
    LOGIN_SUCCESS,
)
from media_stack.core.auth.users.audit_log import AuditLog
from media_stack.core.auth.users.login_history_index import LoginHistoryProtocol
from media_stack.core.time_utils import parse_iso, utcnow_iso
from media_stack.application.security.session_aggregator import (
    SessionAggregator,
    SessionDTO,
)

_log = logging.getLogger("media_stack.security.reports")


# ---- Value objects --------------------------------------------------------


@dataclass(frozen=True)
class FailedLoginCluster:
    """One row in the ``failed_login_clusters`` report.

    ``ip_prefix`` is a CIDR string (``"203.0.113.0/24"``). Empty /
    un-parseable IPs coalesce into a single ``""`` bucket so they
    remain visible without polluting real clusters.
    """

    ip_prefix: str
    usernames: tuple[str, ...]
    attempt_count: int
    first_seen: str
    last_seen: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "ip_prefix": self.ip_prefix,
            "usernames": list(self.usernames),
            "attempt_count": self.attempt_count,
            "first_seen": self.first_seen,
            "last_seen": self.last_seen,
        }


@dataclass(frozen=True)
class NewLocationAlert:
    """One row in the ``new_location_alerts`` report."""

    username: str
    ip_prefix: str
    observed_at: str
    provider: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "username": self.username,
            "ip_prefix": self.ip_prefix,
            "observed_at": self.observed_at,
            "provider": self.provider,
        }


@dataclass(frozen=True)
class ConcurrentSessionAlert:
    """One row in the ``concurrent_session_spikes`` report."""

    username: str
    count: int
    threshold: int
    providers: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "username": self.username,
            "count": self.count,
            "threshold": self.threshold,
            "providers": list(self.providers),
        }


# ---- Service --------------------------------------------------------------


class SecurityReportService:
    """Aggregate security reports for the dashboard Security tab.

    ``audit_log`` is source of truth for historical auth events;
    ``session_aggregator`` supplies live cross-provider sessions
    for the spike report; ``login_history`` provides the
    ``is_first_seen_ip`` check used by ``new_location_alerts``.
    """

    def __init__(
        self,
        *,
        audit_log: AuditLog,
        session_aggregator: SessionAggregator,
        login_history: LoginHistoryProtocol,
    ) -> None:
        if audit_log is None:
            raise ValueError("audit_log is required")
        if session_aggregator is None:
            raise ValueError("session_aggregator is required")
        if login_history is None:
            raise ValueError("login_history is required")
        self._audit = audit_log
        self._aggregator = session_aggregator
        self._login_history = login_history

    @requires_admin
    def failed_login_clusters(
        self, *, actor: Actor,
        since_hours: int = 24,
        min_attempts: int = 5,
    ) -> list[FailedLoginCluster]:
        """Group recent ``login_failure`` audit entries by /24.

        Only clusters with ``attempt_count >= min_attempts`` are
        returned. Sorted by ``attempt_count`` desc, ties broken by
        ``last_seen`` desc.
        """
        since_iso = _cutoff_iso(hours=since_hours)
        entries = self._recent_entries(
            actions=(LOGIN_FAILURE,), since=since_iso,
        )
        buckets: dict[str, _ClusterAccum] = {}
        for e in entries:
            ip_raw = str(e.get("ip", "") or "")
            prefix = ip_prefix_for(ip_raw) if ip_raw else ""
            acc = buckets.get(prefix)
            if acc is None:
                acc = _ClusterAccum(prefix)
                buckets[prefix] = acc
            acc.add(e)
        out: list[FailedLoginCluster] = []
        for acc in buckets.values():
            if acc.attempt_count < int(min_attempts):
                continue
            out.append(acc.freeze())
        out.sort(key=lambda c: (-c.attempt_count, _neg_iso(c.last_seen)))
        return out

    @requires_admin
    def new_location_alerts(
        self, *, actor: Actor,
        lookback_days: int = 90,
        since_hours: int = 24,
    ) -> list[NewLocationAlert]:
        """Recent successful logins on a ``(user, /24)`` pair not seen
        in the prior ``lookback_days`` period.

        Delegates to ``LoginHistoryIndex.is_first_seen_ip`` so this
        report and the live "new location" badge agree. We walk the
        recent audit tail capped by ``since_hours`` — this is the
        operator's "what changed recently?" report.
        """
        since_iso = _cutoff_iso(hours=since_hours)
        entries = self._recent_entries(
            actions=(LOGIN_SUCCESS,), since=since_iso,
        )
        seen_pairs: set[tuple[str, str]] = set()
        out: list[NewLocationAlert] = []
        for e in entries:
            username = str(e.get("target", "") or e.get("actor", "") or "")
            ip_raw = str(e.get("ip", "") or "")
            ts = str(e.get("timestamp", "") or "")
            if not username or not ip_raw:
                continue
            prefix = ip_prefix_for(ip_raw)
            if not prefix:
                continue
            key = (username, prefix)
            if key in seen_pairs:
                # One alert per pair in the report window — duplicate
                # logins from the same /24 don't each generate a row.
                continue
            try:
                first = bool(
                    self._login_history.is_first_seen_ip(
                        username, ip_raw, lookback_days=int(lookback_days),
                    )
                )
            except Exception as exc:  # noqa: BLE001
                _log.debug(
                    "login_history.is_first_seen_ip(%r,%r) failed: %s",
                    username, ip_raw, exc,
                )
                continue
            if not first:
                continue
            seen_pairs.add(key)
            provider = str(
                (e.get("detail") or {}).get("provider", "") or "controller"
            )
            out.append(NewLocationAlert(
                username=username,
                ip_prefix=prefix,
                observed_at=ts,
                provider=provider,
            ))
        out.sort(key=lambda a: (_neg_iso(a.observed_at), a.username))
        return out

    @requires_admin
    def concurrent_session_spikes(
        self, *, actor: Actor, threshold: int = 5,
    ) -> list[ConcurrentSessionAlert]:
        """Users currently holding ``>= threshold`` live sessions.

        Shared-credential / takeover signal. Counts come from the
        aggregator so every backend contributes (one cookie + four
        Jellyfin apps counts as five).
        """
        threshold = int(threshold)
        if threshold <= 0:
            threshold = 1
        try:
            sessions = self._aggregator.list_all(actor=actor)
        except Exception as exc:  # noqa: BLE001
            _log.debug("session_aggregator.list_all failed: %s", exc)
            return []
        grouped: dict[str, list[SessionDTO]] = {}
        for s in sessions:
            if not s.username:
                continue
            grouped.setdefault(s.username, []).append(s)
        out: list[ConcurrentSessionAlert] = []
        for username, rows in grouped.items():
            if len(rows) < threshold:
                continue
            providers = sorted({r.provider for r in rows})
            out.append(ConcurrentSessionAlert(
                username=username,
                count=len(rows),
                threshold=threshold,
                providers=tuple(providers),
            ))
        out.sort(key=lambda a: (-a.count, a.username))
        return out

    @requires_admin
    def login_history_for_user(
        self, *, username: str, actor: Actor, limit: int = 100,
    ) -> list[dict[str, Any]]:
        """``AUTH_EVENTS`` audit entries for ``username``, newest first.

        Uses ``recent_by_actions`` so the filter set stays in sync
        with the action-constants module.
        """
        if not username:
            return []
        bound = max(1, int(limit))
        # Pull a generous multiple so filtering by user has headroom
        # before trimming to the caller's limit.
        raw = self._audit.recent_by_actions(
            AUTH_EVENTS, limit=bound * 10,
        )
        mine = [
            e for e in raw
            if str(e.get("target", "")) == username
            or str(e.get("actor", "")) == username
        ]
        # ``recent_by_actions`` returns oldest-first (tail-slice); the
        # UI wants newest-first.
        mine.reverse()
        return mine[:bound]

    def _recent_entries(
        self, *, actions: tuple[str, ...], since: str,
    ) -> list[dict[str, Any]]:
        try:
            return self._audit.recent_by_actions(
                actions, since=since, limit=10_000,
            )
        except Exception as exc:  # noqa: BLE001
            _log.debug("audit.recent_by_actions failed: %s", exc)
            return []


# ---- Internals ------------------------------------------------------------


class _ClusterAccum:
    """Mutable accumulator folded into an immutable ``FailedLoginCluster``
    before return. Kept out of the public API."""

    __slots__ = (
        "_ip_prefix", "_usernames", "_count", "_first_seen", "_last_seen",
    )

    def __init__(self, ip_prefix: str) -> None:
        self._ip_prefix = ip_prefix
        self._usernames: dict[str, None] = {}
        self._count = 0
        self._first_seen = ""
        self._last_seen = ""

    @property
    def attempt_count(self) -> int:
        return self._count

    def add(self, entry: dict[str, Any]) -> None:
        self._count += 1
        target = str(entry.get("target", "") or "")
        if target:
            self._usernames.setdefault(target, None)
        ts = str(entry.get("timestamp", "") or "")
        if ts:
            if not self._first_seen or ts < self._first_seen:
                self._first_seen = ts
            if ts > self._last_seen:
                self._last_seen = ts

    def freeze(self) -> FailedLoginCluster:
        return FailedLoginCluster(
            ip_prefix=self._ip_prefix,
            usernames=tuple(self._usernames.keys()),
            attempt_count=self._count,
            first_seen=self._first_seen,
            last_seen=self._last_seen,
        )


def _cutoff_iso(*, hours: int) -> str:
    """Compute an ISO ``since`` cutoff ``hours`` ago.

    ``AuditLog`` compares ``since`` lexically against the entry's
    timestamp. Entries are written with
    ``isoformat(timespec="seconds")`` (``"...+00:00"``); we match
    that shape to avoid ``Z`` vs ``+00:00`` ordering drift.
    """
    h = max(0, int(hours))
    now_dt = parse_iso(utcnow_iso())
    if now_dt is None:
        # Defensive — a truly wedged clock falls through to "since
        # forever" so the caller sees every entry.
        return ""
    cutoff = now_dt - timedelta(hours=h)
    return cutoff.replace(microsecond=0).isoformat(timespec="seconds")


def _neg_iso(s: str) -> tuple:
    """Sort key that orders ``s`` descending under ascending sort.

    Empty strings sort last; present strings have each codepoint
    inverted so ascending = reverse chronological.
    """
    if not s:
        return (1, "")
    inverted = "".join(chr(0x10FFFF - ord(c)) for c in s)
    return (0, inverted)


__all__ = [
    "ConcurrentSessionAlert",
    "FailedLoginCluster",
    "NewLocationAlert",
    "SecurityReportService",
]
