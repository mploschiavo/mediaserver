"""Storage-domain EventBus publisher (ADR-0008 Phase 4).

The lockdown + cleanup services publish three storage-domain events
on the shared ``EventBus``:

* ``storage.lockdown_engaged`` — after a successful engage transition.
* ``storage.lockdown_released`` — after a successful release transition.
* ``storage.cleanup_invoked`` — after each cleanup pass settles.

Per-call lookup of the bus mirrors the pattern in
``application.jobs.run_history``: the bus is fetched at publish time
so a test that swaps the default bus picks up the new instance
without restarting the process. Failure isolation is total — a
missing/raising bus must NEVER block the underlying lockdown or
cleanup action.

Lifted out of ``download_lockdown_service.py`` and
``disk_guardrails_service.py`` so both files stay below the 400-line
hygiene ratchet without sacrificing the publisher contract.
"""

from __future__ import annotations

from media_stack.core.events import (
    get_default_bus,
    StorageCleanupInvoked,
    StorageLockdownEngaged,
    StorageLockdownReleased,
)
from media_stack.core.logging_utils import log_swallowed


class StorageEventPublisher:
    """Adapter that publishes the three Phase 4 storage events.

    Class-shaped so the no-loose-functions ratchet stays clean and so
    tests can inject a custom bus / event-class trio without
    monkey-patching ``media_stack.core.events``. Production callers
    construct a single instance per process; tests wire fakes.
    """

    def publish_lockdown_engaged(self, event: StorageLockdownEngaged) -> None:
        """Publish a fully-constructed lockdown-engaged event. The
        caller builds the typed dataclass; the publisher only owns
        bus-lookup + failure isolation."""
        try:
            get_default_bus().publish(event)
        except (AttributeError, OSError, RuntimeError, ValueError) as exc:
            log_swallowed(exc, context="lockdown-event-publish")

    def publish_lockdown_released(self, event: StorageLockdownReleased) -> None:
        try:
            get_default_bus().publish(event)
        except (AttributeError, OSError, RuntimeError, ValueError) as exc:
            log_swallowed(exc, context="lockdown-event-publish")

    def publish_cleanup_invoked(self, event: StorageCleanupInvoked) -> None:
        try:
            get_default_bus().publish(event)
        except (AttributeError, OSError, RuntimeError, ValueError) as exc:
            log_swallowed(exc, context="cleanup-event-publish")


# Module-level singleton — both services reach for the same instance
# so tests can swap one global to silence all three publishers.
STORAGE_EVENT_PUBLISHER = StorageEventPublisher()


__all__ = ["StorageEventPublisher", "STORAGE_EVENT_PUBLISHER"]
