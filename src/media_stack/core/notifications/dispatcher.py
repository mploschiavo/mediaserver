"""Channel registry + dispatch logic for security notifications.

The dispatcher is deliberately small: it holds a name-keyed registry
of :class:`Channel` implementations, iterates them on
:meth:`NotificationDispatcher.dispatch`, and records one
:class:`NotificationResult` per channel attempt. It does *not* queue,
batch, or retry at the dispatcher layer — retry semantics live inside
each channel (the webhook channel uses the project's existing
``HttpClient`` which already retries transport errors) and the caller
decides what to do with ``RETRYABLE`` / ``UNDELIVERABLE`` outcomes.

Design choices worth calling out:

* **Per-channel event-type filtering.** Channels opt *in* to the
  events they care about via an optional ``event_types`` set. The
  dispatcher asks the channel (``channel.accepts(event_type)``) rather
  than reading a config map so each transport owns its own matching
  rule — a future Slack channel can use regex routing without the
  dispatcher growing special cases. Channels that return ``False``
  from ``accepts`` are skipped silently; their result is simply not
  in the returned list.
* **Exceptions in ``send`` become results, never bubble.** One
  misbehaving channel must not stop the fan-out. Any exception
  thrown by ``send`` is caught and turned into ``UNDELIVERABLE`` so
  the caller can route it to the dead-letter sink without wrapping
  every dispatch in try/except.
* **Dedupe is a wall-clock TTL keyed on ``dedupe_key``.** A burst of
  identical events (e.g. ten failed logins firing the same
  brute-force alert) should collapse to one notification. We keep a
  tiny in-memory ``{key: last_sent_monotonic}`` table and suppress
  repeats within the window. A ``dedupe_key`` of ``""`` opts out —
  every call goes through. We use ``time.monotonic`` so NTP steps
  cannot reopen the window early.
* **Thread-safe via a re-entrant lock.** ``dispatch`` is commonly
  called from whichever thread observed the security event (request
  handler, ban-enforcer, audit-tail); the tests exercise 10 threads
  fanning out concurrently. An ``RLock`` lets the dedupe check
  recurse into registry iteration without deadlocking.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol, runtime_checkable

__all__ = [
    "Channel",
    "DeliveryStatus",
    "Notification",
    "NotificationDispatcher",
    "NotificationResult",
]


class DeliveryStatus(str, Enum):
    """Outcome of a single channel's attempt to deliver a notification.

    ``str`` mix-in keeps the value JSON-friendly — audit-log rows can
    embed ``result.status`` directly without a custom encoder. The
    four states map one-to-one onto operator intent:

    * ``OK``            — delivered, nothing to do.
    * ``RETRYABLE``     — transient failure (timeout, 5xx,
      connection reset). The caller *may* retry; the dispatcher does
      not loop because retries would block unrelated channels.
    * ``DROPPED``       — deliberately suppressed (dedupe hit or
      channel declined via ``accepts``). Not an error.
    * ``UNDELIVERABLE`` — permanent failure (bad config, 4xx other
      than 429, unhandled exception). Belongs on the dead-letter
      sink; retrying won't help without operator action.
    """

    OK = "ok"
    RETRYABLE = "retryable"
    DROPPED = "dropped"
    UNDELIVERABLE = "undeliverable"


@dataclass(frozen=True)
class Notification:
    """A single security event ready to be fanned out to channels.

    Frozen so the same instance can be handed to N channels without
    any of them mutating the payload the others see. ``structured``
    defaults to an empty dict via ``default_factory`` because a
    mutable default on a frozen dataclass would still share the one
    instance across all notifications.

    Fields:
        event_type: Short machine-readable identifier, e.g.
            ``"auth.new_location"``. Channels match on this.
        title: Operator-facing one-line summary.
        body: Longer human-readable description.
        severity: One of ``"info"``, ``"warn"``, ``"critical"``. Kept
            as a plain string rather than an enum so downstream
            consumers can carry forward severities we don't emit yet
            (e.g. ``"debug"``).
        structured: JSON-serialisable bag of machine-readable fields
            (ip, user_id, session_id, …). Channels that speak JSON
            surface this verbatim.
        dedupe_key: Optional cross-channel suppression key. Empty
            string (the default) disables dedupe for this event.
    """

    event_type: str
    title: str
    body: str
    severity: str
    structured: dict[str, Any] = field(default_factory=dict)
    dedupe_key: str = ""


@dataclass(frozen=True)
class NotificationResult:
    """Per-channel outcome of a single :meth:`NotificationDispatcher.dispatch`.

    Returned in a list (one entry per attempted channel) so the
    caller can route failures to an undeliverable sink while logging
    successes. ``attempt`` is an optional hint for channels that
    internally retry; the dispatcher itself always reports ``1``.
    """

    channel_name: str
    status: DeliveryStatus
    detail: str = ""
    attempt: int = 1


@runtime_checkable
class Channel(Protocol):
    """Transport-agnostic notification backend.

    ``runtime_checkable`` so tests and config loaders can do
    ``isinstance(obj, Channel)`` when validating a dynamic plugin
    list. Implementations are expected to be *stateless with respect
    to the dispatcher* — the dispatcher never inspects internals, it
    only calls the two protocol methods.
    """

    name: str

    def send(self, notification: Notification) -> NotificationResult:
        """Deliver ``notification`` and return a single result."""
        ...

    def accepts(self, event_type: str) -> bool:
        """Return True iff this channel wants to handle ``event_type``."""
        ...


class NotificationDispatcher:
    """Thread-safe channel registry + fan-out driver.

    Instances are typically process-wide singletons; call sites pull
    the shared dispatcher from wherever the session-visibility
    wiring exposes it and call :meth:`dispatch` on every security
    event.
    """

    def __init__(self, *, dedupe_window_seconds: int = 60) -> None:
        """Create an empty dispatcher.

        Args:
            dedupe_window_seconds: Default TTL for ``dedupe_key``
                suppression. 60 seconds matches the brute-force
                rate-limit window used elsewhere in the stack — a
                storm of login failures reports once per minute, not
                once per attempt. Set to 0 to disable dedupe
                entirely at construction time.
        """
        self._lock = threading.RLock()
        self._channels: dict[str, Channel] = {}
        self._dedupe_window_seconds = max(0, int(dedupe_window_seconds))
        # ``{dedupe_key: monotonic_timestamp_of_last_send}``.
        # Pruned lazily on dispatch so one-shot keys don't accumulate.
        self._dedupe_seen: dict[str, float] = {}

    # ------------------------------------------------------------------
    # Registry management
    # ------------------------------------------------------------------

    def register(self, channel: Channel) -> None:
        """Register or replace ``channel`` keyed by ``channel.name``.

        Idempotent on name: registering a second channel with the
        same name evicts the first. This lets config-reload paths
        rebuild the dispatcher state without a separate
        "remove-before-add" dance.
        """
        with self._lock:
            self._channels[channel.name] = channel

    def unregister(self, name: str) -> None:
        """Remove the channel named ``name``. No-op if absent."""
        with self._lock:
            self._channels.pop(name, None)

    def channels(self) -> list[str]:
        """Return the currently-registered channel names (snapshot)."""
        with self._lock:
            return list(self._channels.keys())

    def deduplicate(self, window_seconds: int) -> None:
        """Set the dedupe window for subsequent dispatches.

        Exposed as a method (not a constructor-only knob) so an
        operator toggle can widen/narrow the window at runtime
        without recreating the dispatcher and losing the channel
        registry. Negative values clamp to 0 (dedupe disabled).
        """
        with self._lock:
            self._dedupe_window_seconds = max(0, int(window_seconds))
            # Reset the history so a shorter window does not
            # retroactively suppress keys that would now be allowed.
            self._dedupe_seen.clear()

    # ------------------------------------------------------------------
    # Dispatch
    # ------------------------------------------------------------------

    def dispatch(self, notification: Notification) -> list[NotificationResult]:
        """Fan ``notification`` out to every accepting channel.

        Returns a list with one :class:`NotificationResult` per
        channel that was *attempted* — channels whose ``accepts``
        returned ``False`` are silently skipped and do not appear in
        the list. If the notification is dedupe-suppressed, the
        return value is a single-entry list flagging the drop so the
        caller has a visible trail that something was intentionally
        suppressed (rather than lost).
        """
        # Dedupe check + snapshot of channels happen under the lock
        # so a concurrent ``register`` cannot produce a half-visible
        # registry mid-iteration. The actual ``send`` calls run
        # outside the lock — they can be slow (HTTP timeouts) and
        # must not serialise the whole dispatcher.
        with self._lock:
            if self._is_dedupe_hit(notification):
                return [
                    NotificationResult(
                        channel_name="__dispatcher__",
                        status=DeliveryStatus.DROPPED,
                        detail=f"dedupe_key={notification.dedupe_key!r} within window",
                    )
                ]
            self._record_dedupe(notification)
            snapshot = list(self._channels.values())

        results: list[NotificationResult] = []
        for channel in snapshot:
            try:
                if not channel.accepts(notification.event_type):
                    continue
            except Exception as exc:
                # A broken ``accepts`` is still the channel's fault;
                # surface it rather than skipping silently, otherwise
                # a misconfigured filter hides the event entirely.
                results.append(
                    NotificationResult(
                        channel_name=getattr(channel, "name", "<unknown>"),
                        status=DeliveryStatus.UNDELIVERABLE,
                        detail=f"accepts() raised: {exc}",
                    )
                )
                continue

            try:
                result = channel.send(notification)
            except Exception as exc:
                results.append(
                    NotificationResult(
                        channel_name=getattr(channel, "name", "<unknown>"),
                        status=DeliveryStatus.UNDELIVERABLE,
                        detail=str(exc),
                    )
                )
                continue

            # Defensive: a misbehaving channel could return ``None``.
            # Rather than crash the fan-out, synthesise a result.
            if not isinstance(result, NotificationResult):
                results.append(
                    NotificationResult(
                        channel_name=getattr(channel, "name", "<unknown>"),
                        status=DeliveryStatus.UNDELIVERABLE,
                        detail=f"channel returned {type(result).__name__}, not NotificationResult",
                    )
                )
                continue

            results.append(result)

        return results

    # ------------------------------------------------------------------
    # Dedupe internals
    # ------------------------------------------------------------------

    def _is_dedupe_hit(self, notification: Notification) -> bool:
        """Return True iff ``notification`` should be dropped.

        Caller must hold ``self._lock``. Prunes expired entries as
        it walks, so the map never grows unbounded in the common
        case where most keys are one-shot.
        """
        if self._dedupe_window_seconds <= 0:
            return False
        key = notification.dedupe_key
        if not key:
            return False
        now = time.monotonic()
        cutoff = now - self._dedupe_window_seconds
        # Opportunistic prune — cheap because the map is tiny in
        # practice (seconds of window × rate of distinct keys).
        expired = [k for k, ts in self._dedupe_seen.items() if ts < cutoff]
        for k in expired:
            self._dedupe_seen.pop(k, None)
        last = self._dedupe_seen.get(key)
        return last is not None and last >= cutoff

    def _record_dedupe(self, notification: Notification) -> None:
        """Stamp ``dedupe_key`` with the current monotonic time.

        Caller must hold ``self._lock``. A blank key is skipped so
        opting-out events do not pollute the map.
        """
        if self._dedupe_window_seconds <= 0:
            return
        key = notification.dedupe_key
        if not key:
            return
        self._dedupe_seen[key] = time.monotonic()
