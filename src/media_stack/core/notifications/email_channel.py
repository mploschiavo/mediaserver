"""Email channel stub for :class:`NotificationDispatcher`.

**This is a stub.** Real SMTP wiring (TLS config, auth, retries,
attachment support, DKIM) is a follow-up task; building that here
would balloon the dispatcher change-set and force testing through a
live SMTP fixture. Instead, this channel takes an injected
``sender`` callable — defaulting to an in-memory test stub — so the
*dispatch surface* can be exercised end-to-end today and the SMTP
integration is a one-function swap later.

The sender signature is ``(to_list, subject, body) -> None``; an
SMTP-backed implementation becomes a tiny adapter around
:mod:`smtplib` that matches this signature. Anything more complex
(content-type negotiation, templating, bounce handling) lives on the
roadmap, not in this file.

Result mapping:

* sender returns normally → :attr:`DeliveryStatus.OK`.
* sender raises         → :attr:`DeliveryStatus.UNDELIVERABLE` with
  ``detail=str(exc)``. We do not classify email failures as
  retryable because without a real SMTP client we cannot tell a
  transient queue problem from a permanent rejection; leaving that
  to the real implementation keeps this file honest.
"""

from __future__ import annotations

from collections.abc import Callable

from .dispatcher import (
    Channel,
    DeliveryStatus,
    Notification,
    NotificationResult,
)

__all__ = ["EmailChannel", "InMemoryEmailSender"]


Sender = Callable[[list[str], str, str], None]


class InMemoryEmailSender:
    """Default sender that records messages instead of transmitting them.

    Used so tests (and dev environments) can assert that the channel
    produced the right content without standing up an SMTP server.
    A production deployment replaces this with an ``smtplib``-backed
    callable wired through the controller's config loader.
    """

    def __init__(self) -> None:
        #: List of ``(to_list, subject, body)`` tuples, newest last.
        #: Exposed for tests; not intended for runtime consumption.
        self.sent: list[tuple[list[str], str, str]] = []

    def __call__(self, to: list[str], subject: str, body: str) -> None:
        # Store a defensive copy of ``to`` so later mutation of the
        # caller's list can't rewrite the record after the fact.
        self.sent.append((list(to), subject, body))


class EmailChannel(Channel):
    """Deliver notifications by calling an injected email-sender callable.

    The sender is *not* called on a background thread; the dispatcher
    runs channels synchronously on purpose (see dispatcher module
    docstring). A future SMTP implementation that needs to bound
    latency should use its own timeout/connection-pool plumbing —
    this class does not add a timer on top.
    """

    def __init__(
        self,
        name: str,
        to: list[str],
        *,
        event_types: frozenset[str] | None = None,
        sender: Sender | None = None,
    ) -> None:
        """Configure an email channel.

        Args:
            name: Dispatcher key. Must be unique within a dispatcher.
            to: One or more recipient addresses. We store a defensive
                copy so later mutation by the caller does not change
                where mail is sent.
            event_types: If given, only these event types are
                accepted. ``None`` means "accept everything".
            sender: Callable invoked on every send. Defaults to an
                :class:`InMemoryEmailSender` so tests can inspect
                delivered mail without SMTP. Production config
                injects a real SMTP-backed callable here.
        """
        self.name = name
        self._to = list(to)
        self._event_types = event_types
        self._sender: Sender = sender if sender is not None else InMemoryEmailSender()

    # ------------------------------------------------------------------
    # Channel protocol
    # ------------------------------------------------------------------

    def accepts(self, event_type: str) -> bool:
        """Return True when this channel wants ``event_type``."""
        if self._event_types is None:
            return True
        return event_type in self._event_types

    def send(self, notification: Notification) -> NotificationResult:
        """Format and dispatch the notification via the sender callable."""
        subject = self._format_subject(notification)
        body = self._format_body(notification)
        try:
            self._sender(list(self._to), subject, body)
        except Exception as exc:
            return NotificationResult(
                channel_name=self.name,
                status=DeliveryStatus.UNDELIVERABLE,
                detail=str(exc),
            )
        return NotificationResult(
            channel_name=self.name,
            status=DeliveryStatus.OK,
            detail=f"recipients={len(self._to)}",
        )

    # ------------------------------------------------------------------
    # Formatting
    # ------------------------------------------------------------------

    @staticmethod
    def _format_subject(notification: Notification) -> str:
        """Build the subject line from notification metadata.

        Severity is prefixed in brackets so filters / triage rules
        in the operator's mail client can key off it without parsing
        the body.
        """
        sev = notification.severity.upper() if notification.severity else "INFO"
        return f"[{sev}] {notification.title}"

    @staticmethod
    def _format_body(notification: Notification) -> str:
        """Render a plain-text body suitable for SMTP transmission.

        Keeps the structured fields at the bottom so a human reader
        sees the narrative first and the JSON-ish detail second.
        """
        lines = [
            notification.body or "",
            "",
            f"event_type: {notification.event_type}",
            f"severity: {notification.severity}",
        ]
        if notification.structured:
            lines.append("")
            lines.append("Details:")
            for key, value in sorted(notification.structured.items()):
                lines.append(f"  {key}: {value}")
        return "\n".join(lines)
