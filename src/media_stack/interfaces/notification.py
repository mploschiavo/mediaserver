"""Notification-sink port.

Generalises the existing ``core/notifications/`` plumbing. A
notification sink is anything that can accept a structured message
and deliver it: stdout, a webhook, ntfy, Discord, an email server,
a UI toast bus, …

Phase 16-A scaffolding: shape only. Phase 16-E migrates the live
sinks behind this port.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class Notification:
    """Structured notification payload.

    ``severity`` mirrors the rollup vocabulary the controller
    already uses (``info`` / ``warning`` / ``error``). ``data`` is
    sink-specific extra context — keep it JSON-serialisable.
    """

    title: str
    body: str
    severity: str = "info"
    data: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class NotificationSink(Protocol):
    """Port for any delivery target."""

    name: str

    def send(self, msg: Notification) -> None:
        """Deliver ``msg``. MUST NOT raise on transport failure —
        log + drop. The caller is not equipped to recover from a
        sink outage, and a raise here would cascade."""
