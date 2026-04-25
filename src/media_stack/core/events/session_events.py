"""Typed domain events for the session-visibility feature.

Each class is a frozen, keyword-only dataclass subclassing ``Event``
and advertising a stable dotted ``event_type`` through the class-level
``EVENT_TYPE`` sentinel. ``Event.__post_init__`` backfills the instance
field from that sentinel so call sites stay concise:

    bus.publish(LoginSucceeded(
        username="alice",
        provider="jellyfin",
        client_ip="10.0.0.5",
        user_agent="Firefox",
        device_class="browser",
        first_seen_ip=False,
        concurrent_count=2,
    ))

Why a ``ClassVar`` sentinel rather than passing ``event_type`` per
instance or using a factory classmethod? Two reasons:
  1. It keeps the public constructor signature honest — every field a
     caller supplies is meaningful business data; the string tag is
     an identity of the class itself, not of an instance.
  2. It keeps ``to_dict()`` flat — the field still serialises, so
     downstream JSON consumers (audit log, notification dispatcher)
     see ``event_type`` inline without special-casing.

Reason strings for ``SessionRevoked`` / ``LoginFailed`` / ``BanApplied``
are deliberately plain ``str`` rather than ``Enum``: the wire contract
(audit log, notifications) is a string, and an Enum would force every
producer to import the type just to emit an event. The permitted
values are documented inline on each field and enforced at the
authorisation layer, not here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from media_stack.core.events.bus import Event


@dataclass(frozen=True, kw_only=True)
class SessionCreated(Event):
    """A new authenticated session was minted.

    ``device_class`` / ``client_ip`` / ``user_agent`` default to empty
    because some providers (e.g. legacy internal callers) do not supply
    a full request envelope; we still want to record the session
    creation rather than drop it for missing metadata.
    """

    EVENT_TYPE: ClassVar[str] = "session.created"

    username: str
    session_id: str
    provider: str
    device_class: str = ""
    client_ip: str = ""
    user_agent: str = ""


@dataclass(frozen=True, kw_only=True)
class SessionRevoked(Event):
    """A session was terminated.

    ``reason`` is one of:
      ``"user"``            — user signed out voluntarily
      ``"admin_revoke"``    — operator kicked the session
      ``"idle"``            — idle-timeout swept it
      ``"absolute"``        — hit the absolute max-lifetime ceiling
      ``"banned"``          — user/IP was banned, cascading revoke
      ``"password_changed"`` — password rotation invalidated tokens
      ``"replaced"``        — same principal reauthenticated, old
                              session superseded
    """

    EVENT_TYPE: ClassVar[str] = "session.revoked"

    username: str
    session_id: str
    provider: str
    reason: str


@dataclass(frozen=True, kw_only=True)
class LoginSucceeded(Event):
    """Authentication succeeded.

    ``first_seen_ip`` flags whether this (username, client_ip) pair has
    never been observed before — the notification dispatcher uses this
    to decide whether to send a "new device" email without having to
    query history itself.

    ``concurrent_count`` is the session count for this user *after*
    this login; metrics exports it as a gauge so operators can alert
    on account sharing.
    """

    EVENT_TYPE: ClassVar[str] = "login.succeeded"

    username: str
    provider: str
    client_ip: str
    user_agent: str
    device_class: str
    first_seen_ip: bool
    concurrent_count: int


@dataclass(frozen=True, kw_only=True)
class LoginFailed(Event):
    """Authentication attempt rejected.

    ``reason`` is one of:
      ``"bad_password"``  — credential mismatch
      ``"unknown_user"``  — username not in directory
      ``"rate_limited"``  — throttled before credential check
    Distinct values let the metrics consumer split counters and let the
    notification dispatcher warn the real user on ``bad_password`` but
    stay silent on ``unknown_user`` (to avoid enumeration spam).
    """

    EVENT_TYPE: ClassVar[str] = "login.failed"

    username: str
    provider: str
    client_ip: str
    user_agent: str
    reason: str


@dataclass(frozen=True, kw_only=True)
class LoginBlocked(Event):
    """A login was rejected before credential check due to a standing ban.

    Distinct from ``LoginFailed`` because banned attempts must never be
    counted toward lockout thresholds and are surfaced on a different
    alerting path. ``ban_kind`` is ``"user"`` or ``"ip"``; ``ban_reason``
    is the free-text reason recorded when the ban was applied.
    """

    EVENT_TYPE: ClassVar[str] = "login.blocked"

    username: str
    client_ip: str
    ban_kind: str
    ban_reason: str


@dataclass(frozen=True, kw_only=True)
class BanApplied(Event):
    """A user or IP was added to the ban list.

    ``expires_at`` is an ISO-8601 string; the empty string means
    permanent. We keep it as a string (not ``datetime``) so audit
    serialisation is lossless — the bus never needs to guess a
    timezone and consumers parse if they care.
    """

    EVENT_TYPE: ClassVar[str] = "ban.applied"

    kind: str
    target: str
    actor: str
    reason: str
    expires_at: str


@dataclass(frozen=True, kw_only=True)
class BanRemoved(Event):
    """A user or IP ban was lifted.

    No ``reason`` field: in practice lifts are either automatic
    (expiry) or operator-driven, and the operator identity
    (``actor``) is the audit-meaningful piece. Adding a free-text
    reason proved to be noise in prior iterations and is deferred
    until there is a concrete consumer.
    """

    EVENT_TYPE: ClassVar[str] = "ban.removed"

    kind: str
    target: str
    actor: str


@dataclass(frozen=True, kw_only=True)
class EmergencyRevokeInvoked(Event):
    """The incident-response emergency-revoke switch was triggered.

    Distinct from a one-off ``SessionRevoked`` fan-out because this
    event carries the operator-supplied reason string, the aggregate
    counts (sessions killed, admin users flagged for forced
    rotation), and a ``secrets_rotated`` flag. Downstream consumers
    (notification dispatcher, on-call pager) treat this as a
    different severity from a routine revoke.
    """

    EVENT_TYPE: ClassVar[str] = "security.emergency_revoke"

    actor: str
    reason: str
    sessions_revoked: int
    forced_rotations: int
    secrets_rotated: bool


@dataclass(frozen=True, kw_only=True)
class PasswordChanged(Event):
    """A password was changed (by the user or by an admin).

    ``self_change`` distinguishes user-initiated rotations from
    admin-forced resets. Downstream consumers treat the two differently:
    a self-change revokes only sibling sessions on the same account; an
    admin reset additionally posts a notification to the user.
    """

    EVENT_TYPE: ClassVar[str] = "password.changed"

    username: str
    actor: str
    self_change: bool
    provider: str
