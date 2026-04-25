"""Out-of-band notification dispatch for session-visibility security events.

This package owns the *delivery surface* for security events that must
leave the box — new-location logins, IP bans, brute-force threshold
trips, password changes. Those events already land in the audit log;
the dispatcher's job is the separate, best-effort side-channel fan-out
to operator-facing transports (webhook, email, Slack/Discord later).

Why it is a dispatcher and not a queue:

* The audit log is the source of truth. If a notification fails, the
  event is *not* lost — it is still on disk. Operators want prompt
  delivery, not guaranteed-exactly-once delivery, so we optimise for
  latency and simplicity over durability.
* A full queue (Redis/SQS/…) would drag in operational dependencies
  the rest of the controller deliberately avoids. The existing
  webhook broadcast loop in ``media_stack.api.webhooks`` is
  best-effort synchronous too; we stay consistent with that.

Public surface:

* :class:`NotificationDispatcher` — thread-safe channel registry
  with per-event routing and dedupe-key suppression.
* :class:`Channel` — runtime-checkable protocol every backend
  implements. Keeps the dispatcher ignorant of transport details.
* :class:`Notification` — immutable payload record.
* :class:`NotificationResult` / :class:`DeliveryStatus` — per-channel
  outcome, surfaced to the caller so failed sends can be routed to
  an "undeliverable" sink for later inspection.
* :class:`WebhookChannel` / :class:`EmailChannel` — two reference
  backends, intentionally minimal; Slack/Discord slot in without
  touching the dispatcher.
"""

from __future__ import annotations

from .dispatcher import (
    Channel,
    DeliveryStatus,
    Notification,
    NotificationDispatcher,
    NotificationResult,
)
from .email_channel import EmailChannel
from .webhook_channel import WebhookChannel

__all__ = [
    "Channel",
    "DeliveryStatus",
    "EmailChannel",
    "Notification",
    "NotificationDispatcher",
    "NotificationResult",
    "WebhookChannel",
]
