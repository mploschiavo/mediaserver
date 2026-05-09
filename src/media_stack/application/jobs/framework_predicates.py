"""Framework-provided ``when:`` predicates for trigger-driven Jobs
(ADR-0009 / Phase 6).

A ``when:`` clause on a trigger or on ``retry_on_failure`` references
a registered predicate by name. The framework ships one such
predicate today — ``any_service_failed``, used by the bootstrap
contract's ``retry_on_failure`` to gate the heal-retry on the
controller's ``failed_services`` map being non-empty. Plugins
register their own predicates the same way.

Names here describe what the predicate inspects, not which Job
references it — there is no Job-specific behaviour in this module.
"""

from __future__ import annotations

from typing import Any, ClassVar

from media_stack.application.jobs.trigger_schema import (
    TriggerPredicateRegistry,
)


class FrameworkPredicates:
    """Holds the framework's built-in ``when:`` predicates.

    Plugins extend the predicate registry through
    ``TriggerPredicateRegistry.register(...)`` directly; this class
    is the framework's own contribution to the same registry.

    Wiring: controller boot constructs the framework, calls
    ``install(state=...)`` to bind the controller-state reference,
    then calls ``register_all()`` to publish the predicates. After
    that, the trigger engine's ``validate_when_predicates_now()``
    can verify every contract reference resolves.
    """

    _state: ClassVar[Any | None] = None

    @classmethod
    def install(cls, *, state: Any) -> None:
        """Pin the ``ControllerState`` reference predicates consult."""
        cls._state = state

    @classmethod
    def register_all(cls) -> None:
        """Register every framework-provided predicate. Idempotent
        on identical re-registration; conflicting re-registration
        raises (handled by ``TriggerPredicateRegistry.register``)."""
        TriggerPredicateRegistry.register(
            "any_service_failed", cls._any_service_failed,
        )

    @classmethod
    def _any_service_failed(cls, _ctx: Any) -> bool:
        """True when the controller's failed-services map is
        non-empty.

        Used by ``retry_on_failure.when`` to short-circuit the
        heal-retry timer when there is nothing to heal — the timer
        still fires after the configured delay, but the retrigger
        skips so the controller doesn't loop on a clean failure.
        """
        if cls._state is None:
            return False
        getter = getattr(cls._state, "get_failed_services", None)
        if getter is None:
            return False
        try:
            return bool(getter())
        except Exception:
            return False


__all__ = ["FrameworkPredicates"]
