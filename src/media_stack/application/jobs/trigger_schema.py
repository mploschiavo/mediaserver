"""Closed schema for declarative Job triggers (ADR-0009 / Phase 6.1).

Two registries live here:

``TriggerKinds`` — the closed set of valid ``event:`` values on a
trigger entry. Plugins cannot extend this set; adding a new trigger
kind is a framework change because each kind corresponds to a
concrete event source the ``TriggerEngine`` knows how to deliver
(``JobRunner`` lifecycle hooks, ``Orchestrator.satisfy_scope``, the
existing scheduler, controller boot). Keeping the surface fixed is
deliberate — it is the only thing that prevents contracts from
becoming a programming language.

The trigger key is ``event:`` (not ``on:``) because PyYAML parses
the bare key ``on:`` as the boolean ``True`` under YAML 1.1's
deprecated ``y/yes/n/no/on/off`` alias set; renaming avoids silent
breakage and the brittleness of having to quote every contract.

``TriggerPredicateRegistry`` — the closed-but-extensible registry
of state predicates referenced by ``when:`` clauses on triggers.
Plugins register predicates by calling ``register(name, fn)`` at
import time. The framework validates ``when:`` references at boot
and rejects unknown predicate names.
"""

from __future__ import annotations

from typing import Any, Callable, ClassVar


class TriggerKinds:
    """Closed set of trigger ``event:`` values.

    Each constant maps to a single event source delivered by the
    framework. Order of definition matches the ADR-0009 phase order
    (manual first, then automation primitives).
    """

    MANUAL: ClassVar[str] = "manual"
    SCHEDULE: ClassVar[str] = "schedule"
    JOB_COMPLETED: ClassVar[str] = "job.completed"
    JOB_FAILED: ClassVar[str] = "job.failed"
    PROMISE_SATISFIED: ClassVar[str] = "promise.satisfied"
    PROMISE_VIOLATED: ClassVar[str] = "promise.violated"
    CONTROLLER_STARTED: ClassVar[str] = "controller.started"

    ALL: ClassVar[frozenset[str]] = frozenset({
        "manual",
        "schedule",
        "job.completed",
        "job.failed",
        "promise.satisfied",
        "promise.violated",
        "controller.started",
    })

    @classmethod
    def is_valid(cls, kind: str) -> bool:
        """Return True if ``kind`` is a known trigger ``event:`` value."""
        return kind in cls.ALL


class TriggerPredicateRegistry:
    """Closed-but-extensible registry of ``when:`` predicates.

    A predicate is a callable ``fn(ctx) -> bool`` that consults
    runtime state (deployment-state file, orchestrator scope,
    guardrail state, etc.) and returns whether the gate is open.

    Plugins register predicates at import time. The framework
    validates ``when:`` references on every loaded trigger at boot
    and refuses to start if a contract references an unknown
    predicate.

    A small set of framework-provided predicates is registered by
    ``register_default_predicates`` and lives in this module's
    sister ``trigger_predicate_defaults`` (created in Phase 6.2).
    Until that lands the registry is empty; ``triggers`` blocks
    that omit ``when:`` work without it.
    """

    _predicates: ClassVar[dict[str, Callable[[Any], bool]]] = {}

    @classmethod
    def register(cls, name: str, fn: Callable[[Any], bool]) -> None:
        """Register a predicate. Idempotent on identical re-registration.

        Raises ``ValueError`` if a different callable is registered
        under the same name — a hard error rather than a silent
        last-write-wins so plugin collisions surface at import time.
        """
        existing = cls._predicates.get(name)
        if existing is not None and existing is not fn:
            raise ValueError(
                f"trigger predicate {name!r} already registered with "
                f"a different callable; refusing to overwrite"
            )
        cls._predicates[name] = fn

    @classmethod
    def is_known(cls, name: str) -> bool:
        """Return True if ``name`` has been registered."""
        return name in cls._predicates

    @classmethod
    def evaluate(cls, name: str, ctx: Any) -> bool:
        """Run the predicate. Raises ``KeyError`` if unknown.

        Predicate exceptions propagate — a faulty predicate is a
        framework bug; silently treating it as "gate closed" or
        "gate open" would mask the issue.
        """
        return cls._predicates[name](ctx)

    @classmethod
    def known_names(cls) -> frozenset[str]:
        """Snapshot of registered predicate names. Test-friendly."""
        return frozenset(cls._predicates.keys())

    @classmethod
    def _reset_for_tests(cls) -> None:
        """Clear the registry. Test-fixtures only."""
        cls._predicates.clear()


__all__ = ["TriggerKinds", "TriggerPredicateRegistry"]
