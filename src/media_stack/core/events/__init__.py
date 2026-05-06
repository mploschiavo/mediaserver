"""Structured event bus + domain event types for session-visibility.

Re-exports the ``EventBus`` wiring and every domain ``Event`` subclass so
call sites can do ``from media_stack.core.events import LoginSucceeded``
without caring which submodule defines it. Keeping a single import
surface here also means future event classes can move between
``session_events`` and (eventual) ``content_events`` modules without
rippling through every emitter.

Process-wide default bus
------------------------
Most consumers want a single shared bus per controller process — the
SSE forwarder subscribes once at startup and the publishers
(``record_run_start`` / ``record_run_complete``, future health probe,
guardrail evaluator) need to reach the *same* instance. Services that
already accept an injected ``event_bus`` keep their parameter; modules
without a constructor (free functions in ``run_history``) call
``get_default_bus()``. Tests that need isolation construct their own
``EventBus()`` and pass it in directly.
"""

from __future__ import annotations

import threading

from media_stack.core.events.bus import Event, EventBus, SubscriberHandle
from media_stack.core.events.job_events import JobCompleted, JobStarted
from media_stack.core.events.media_integrity_events import (
    MediaIntegrityConfigEnforced,
    MediaIntegrityConfigEnforceFailed,
    MediaIntegrityDuplicateResolved,
    MediaIntegrityDuplicateReviewNeeded,
    MediaIntegrityReconcileFailed,
)
from media_stack.core.events.session_events import (
    BanApplied,
    BanRemoved,
    EmergencyRevokeInvoked,
    LoginBlocked,
    LoginFailed,
    LoginSucceeded,
    PasswordChanged,
    SessionCreated,
    SessionRevoked,
)
from media_stack.core.events.storage_events import (
    StorageCleanupInvoked,
    StorageLockdownEngaged,
    StorageLockdownReleased,
)

_default_bus: EventBus | None = None
_default_bus_lock = threading.Lock()


def get_default_bus() -> EventBus:
    """Return the process-wide default ``EventBus`` instance.

    Lazily constructed on first access — keeps import-time side
    effects to zero and lets tests that don't touch the bus skip the
    allocation entirely. Thread-safe via double-checked locking.
    """
    global _default_bus
    if _default_bus is None:
        with _default_bus_lock:
            if _default_bus is None:
                _default_bus = EventBus()
    return _default_bus


def reset_default_bus() -> None:
    """Drop the cached default bus — for test isolation only.

    Production code never calls this; the bus lives for the lifetime
    of the controller process. Tests that mutate publisher/subscriber
    state across cases call this in a fixture teardown so the next
    test sees a fresh bus.
    """
    global _default_bus
    with _default_bus_lock:
        _default_bus = None


__all__ = [
    "BanApplied",
    "BanRemoved",
    "EmergencyRevokeInvoked",
    "Event",
    "EventBus",
    "JobCompleted",
    "JobStarted",
    "LoginBlocked",
    "LoginFailed",
    "LoginSucceeded",
    "MediaIntegrityConfigEnforceFailed",
    "MediaIntegrityConfigEnforced",
    "MediaIntegrityDuplicateResolved",
    "MediaIntegrityDuplicateReviewNeeded",
    "PasswordChanged",
    "SessionCreated",
    "SessionRevoked",
    "StorageCleanupInvoked",
    "StorageLockdownEngaged",
    "StorageLockdownReleased",
    "SubscriberHandle",
    "get_default_bus",
    "reset_default_bus",
]
