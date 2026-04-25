"""Thread-safe in-process event bus for session-visibility events.

The session-visibility feature emits a handful of domain events
(logins, session lifecycle, bans, password changes). Five independent
consumers care about them (audit log, Prometheus metrics, notification
dispatcher, SessionAggregator cache, and future consumers). Wiring each
emitter directly to each consumer produces an N*M coupling mesh that is
painful to refactor and hard to test. Routing through a typed bus keeps
every emitter ignorant of consumers and lets tests subscribe a spy
without monkey-patching.

Design notes:
  * Dispatch is synchronous and in-process. We deliberately do *not*
    introduce a queue/threadpool: downstream handlers are expected to be
    fast (counter increments, cache invalidations, structured log
    writes). Expensive work (email send, webhook POST) belongs behind a
    handler that itself enqueues to a worker — keeping that concern out
    of the bus keeps the bus simple.
  * An ``RLock`` (not ``Lock``) guards subscriber state because handlers
    may call ``publish`` re-entrantly — e.g. a session.revoked handler
    that, in turn, emits ban.user.applied. A plain Lock would deadlock
    on the second acquire from the same thread.
  * Handlers that raise are caught and logged at DEBUG (not ERROR): a
    misbehaving subscriber must not silently appear healthy, but it also
    must not prevent delivery to the other four subscribers. DEBUG keeps
    noisy test failures out of production logs while preserving the
    signal for anyone who opts in. Callers that need stricter semantics
    should wrap their handler.
"""

from __future__ import annotations

import itertools
import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, ClassVar

logger = logging.getLogger(__name__)


def _default_ts() -> str:
    """Return an ISO-8601 UTC timestamp (Zulu form) for event stamping.

    Prefers the project's ``core.time_utils.utcnow_iso`` if that module
    exists so event timestamps match the rest of the stack (tests may
    stub it for deterministic clocks). Falls back to stdlib so this
    module has no hard dependency on an optional helper.
    """
    try:  # pragma: no cover - optional integration point
        from media_stack.core import time_utils  # type: ignore[attr-defined]

        fn = getattr(time_utils, "utcnow_iso", None)
        if callable(fn):
            return str(fn())
    except Exception:
        pass
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True, kw_only=True)
class Event:
    """Base class for all domain events on the bus.

    Frozen so handlers cannot mutate an event they received and
    accidentally corrupt what a later handler sees — events are a
    shared-read value, not shared-write state. ``kw_only`` keeps
    subclass construction unambiguous when new fields are appended.

    ``event_type`` is auto-populated from the subclass's ``EVENT_TYPE``
    class variable via ``__post_init__``. Subclasses set ``EVENT_TYPE``
    once; callers never pass ``event_type`` at construction time. The
    field is still a dataclass field (not a plain ``ClassVar``) so it
    round-trips through ``to_dict()`` for serialisation.
    """

    EVENT_TYPE: ClassVar[str] = ""

    event_type: str = ""
    ts: str = field(default_factory=_default_ts)
    request_id: str = ""

    def __post_init__(self) -> None:
        """Backfill ``event_type`` from the subclass's ``EVENT_TYPE``.

        Frozen dataclasses forbid direct attribute assignment, so we
        route through ``object.__setattr__`` — the documented escape
        hatch the stdlib itself uses for post-init invariants on frozen
        instances. Only fills when the caller did not override.
        """
        if not self.event_type and self.EVENT_TYPE:
            object.__setattr__(self, "event_type", self.EVENT_TYPE)

    def to_dict(self) -> dict[str, Any]:
        """Return a plain-dict projection suitable for JSON logging.

        We walk ``__dataclass_fields__`` rather than using
        ``dataclasses.asdict`` because the latter deep-copies nested
        containers — events only carry primitives, so a shallow copy is
        both cheaper and preserves identity for debugging.
        """
        out: dict[str, Any] = {}
        for name in self.__dataclass_fields__:  # type: ignore[attr-defined]
            out[name] = getattr(self, name)
        return out


@dataclass(frozen=True)
class SubscriberHandle:
    """Opaque token returned by ``subscribe``/``subscribe_all``.

    The private ``_id`` is an integer allocated by the bus's monotonic
    counter. Callers treat this as opaque; equality is by id so a
    handle round-trips through sets/dicts cleanly for bookkeeping in
    tests.
    """

    _id: int


class EventBus:
    """Synchronous, thread-safe publish/subscribe event bus.

    Not a global singleton by construction — callers wire the instance
    they want (production uses a module-level default; tests build a
    fresh bus per case to avoid cross-test bleed). Subscription order
    is preserved because dispatch is documented to be deterministic:
    audit log must see an event *before* the notification dispatcher
    attempts to send mail, which is the whole reason we expose an
    ordered list rather than a set.
    """

    def __init__(self) -> None:
        # RLock — see module docstring. Publishing from inside a
        # handler is a supported pattern (revoke -> ban cascade) and a
        # plain Lock would self-deadlock on re-entry.
        self._lock = threading.RLock()
        self._by_type: dict[str, list[tuple[int, Callable[[Event], None]]]] = {}
        self._all: list[tuple[int, Callable[[Event], None]]] = []
        self._ids = itertools.count(1)

    # ------------------------------------------------------------------
    # subscription API
    # ------------------------------------------------------------------

    def subscribe(
        self, event_type: str, handler: Callable[[Event], None]
    ) -> SubscriberHandle:
        """Register ``handler`` for events whose ``event_type`` matches.

        Returns a handle the caller stores so they can later
        ``unsubscribe``. We return a handle instead of the callable
        itself because the same function object can be subscribed more
        than once (e.g. a shared logger registered for two event types)
        and the caller needs to distinguish the registrations.
        """
        with self._lock:
            sid = next(self._ids)
            self._by_type.setdefault(event_type, []).append((sid, handler))
            return SubscriberHandle(_id=sid)

    def subscribe_all(self, handler: Callable[[Event], None]) -> SubscriberHandle:
        """Register ``handler`` for every event regardless of type.

        Used by the audit log and by debug tooling that captures a full
        trace. Kept separate from ``subscribe`` so a per-type subscriber
        list can stay small and not be walked on every publish.
        """
        with self._lock:
            sid = next(self._ids)
            self._all.append((sid, handler))
            return SubscriberHandle(_id=sid)

    def unsubscribe(self, handle: SubscriberHandle) -> None:
        """Remove a subscription. Idempotent.

        Double-unsubscribe is a no-op rather than an error: consumers
        often register in ``__init__`` and tear down in a ``close()`` or
        shutdown hook that may be invoked twice (e.g. explicit close +
        atexit). Making this tolerant avoids every caller needing its
        own 'already unsubscribed?' flag.
        """
        target = handle._id
        with self._lock:
            for _type, subs in list(self._by_type.items()):
                self._by_type[_type] = [(s, h) for (s, h) in subs if s != target]
                if not self._by_type[_type]:
                    del self._by_type[_type]
            self._all = [(s, h) for (s, h) in self._all if s != target]

    # ------------------------------------------------------------------
    # dispatch
    # ------------------------------------------------------------------

    def publish(self, event: Event) -> None:
        """Deliver ``event`` to every subscriber, preserving order.

        We snapshot the subscriber lists under the lock and release it
        before invoking handlers. This matters for two reasons:
          1. Handler execution can be slow relative to the mutation
             window; holding the lock across handler code would serialise
             unrelated publishers on different event types.
          2. Handlers are permitted to ``subscribe``/``unsubscribe`` as
             a side effect (e.g. one-shot subscriptions). Mutating the
             very list we are iterating would either skip entries or
             raise; iterating a snapshot side-steps the whole class of
             bug and makes those semantics well-defined: changes take
             effect from the *next* publish, not this one.

        All-subscribers fire *after* per-type subscribers so the audit
        log records the canonical sequence — typed subscribers are the
        "business logic" consumers, catch-all subscribers are observers.
        """
        with self._lock:
            typed = list(self._by_type.get(event.event_type, ()))
            all_subs = list(self._all)

        for _sid, handler in typed:
            self._safe_invoke(handler, event)
        for _sid, handler in all_subs:
            self._safe_invoke(handler, event)

    @staticmethod
    def _safe_invoke(handler: Callable[[Event], None], event: Event) -> None:
        """Invoke a handler, swallowing + logging any exception.

        A raising handler must never abort dispatch to its siblings:
        that would let the notification dispatcher's flaky SMTP kill
        audit-log delivery, which is exactly the coupling the bus
        exists to prevent. DEBUG level (not ERROR) because the bus has
        no context on whether a given exception is expected — handler
        owners are responsible for their own error surfacing.
        """
        try:
            handler(event)
        except Exception:  # noqa: BLE001 - intentional broad catch
            logger.debug(
                "event handler raised during dispatch of %s",
                event.event_type,
                exc_info=True,
            )

    # ------------------------------------------------------------------
    # introspection / lifecycle
    # ------------------------------------------------------------------

    def clear(self) -> None:
        """Drop all subscribers.

        Exists for tests that reuse a module-level bus instance across
        cases. Production code should never need this — wire once at
        startup and rely on explicit ``unsubscribe``.
        """
        with self._lock:
            self._by_type.clear()
            self._all.clear()

    def subscriber_count(self, event_type: str | None = None) -> int:
        """Count subscribers.

        ``event_type=None`` reports the total across every per-type
        bucket plus catch-alls; a specific type reports that bucket
        plus catch-alls (because a catch-all *would* fire for that
        type). Used by tests to assert subscribe/unsubscribe bookkeeping
        without reaching into private attrs.
        """
        with self._lock:
            if event_type is None:
                typed = sum(len(v) for v in self._by_type.values())
                return typed + len(self._all)
            return len(self._by_type.get(event_type, ())) + len(self._all)
