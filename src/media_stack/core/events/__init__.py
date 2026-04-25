"""Structured event bus + domain event types for session-visibility.

Re-exports the ``EventBus`` wiring and every domain ``Event`` subclass so
call sites can do ``from media_stack.core.events import LoginSucceeded``
without caring which submodule defines it. Keeping a single import
surface here also means future event classes can move between
``session_events`` and (eventual) ``content_events`` modules without
rippling through every emitter.
"""

from __future__ import annotations

from media_stack.core.events.bus import Event, EventBus, SubscriberHandle
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

__all__ = [
    "BanApplied",
    "BanRemoved",
    "EmergencyRevokeInvoked",
    "Event",
    "EventBus",
    "LoginBlocked",
    "LoginFailed",
    "LoginSucceeded",
    "MediaIntegrityConfigEnforceFailed",
    "MediaIntegrityConfigEnforced",
    "MediaIntegrityDuplicateResolved",
    "MediaIntegrityDuplicateReviewNeeded",
    "MediaIntegrityReconcileFailed",
    "PasswordChanged",
    "SessionCreated",
    "SessionRevoked",
    "SubscriberHandle",
]
