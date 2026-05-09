"""Static trigger index + dispatcher (ADR-0009 / Phase 6.2).

The ``TriggerEngine`` is the framework-internal helper that turns
declarative ``triggers:`` blocks on Job contracts into runnable
behaviour. It has three responsibilities:

1. **Validate** every trigger entry on every loaded Job at construction
   time. Unknown ``event:`` kinds, missing required secondary fields,
   and unknown ``when:`` predicates are rejected loudly so a broken
   contract never reaches dispatch.
2. **Index** triggers by ``event:`` kind for O(matching-jobs) dispatch.
3. **Statically detect cycles** in the completion graph so an
   accidentally-recursive trigger (``A.completed → run B,
   B.completed → run A``) refuses to start the controller, instead
   of looping forever at runtime.

It is intentionally NOT an event bus. There is no public
``subscribe`` API, no listener files, no plugin SPI for
intercepting events. Pluggability is achieved by shipping more Job
contracts; the framework's existing contract loader picks them up
and the engine indexes them on the next boot.

For ``event: schedule`` triggers, the engine registers entries
with the existing ``SchedulerService`` via
``register_schedules(scheduler)`` — a deliberate reuse so the
controller has one schedule store, not two.
"""

from __future__ import annotations

import re
from typing import Any, Callable

from media_stack.application.jobs.trigger_errors import (
    InvalidTriggerError,
    TriggerCycleError,
)
from media_stack.application.jobs.trigger_schema import (
    TriggerKinds,
    TriggerPredicateRegistry,
)


_EVERY_RE = re.compile(r"^\s*(\d+)\s*([smh])?\s*$", re.IGNORECASE)

# Seconds-per-time-unit table for the ``every:`` parser. Named so the
# trigger_engine module stays free of bare ``3600`` literals (per the
# repo-wide MAGIC_NUMBERS_OVER_100 ratchet — units conversions are
# expressively named where the value crosses the threshold).
_SECONDS_PER_MINUTE = 60
_SECONDS_PER_HOUR = 60 * _SECONDS_PER_MINUTE
_EVERY_UNIT_TO_SECONDS: dict[str, int] = {
    "s": 1,
    "m": _SECONDS_PER_MINUTE,
    "h": _SECONDS_PER_HOUR,
}


class TriggerEngine:
    """Builds a static event→jobs index from a list of Job
    definitions and dispatches events to matching jobs.

    Constructor injection:
    * ``job_defs`` — list of Job-discovery dicts (the shape produced by
      ``discover_jobs_from_contracts``). Only the ``name`` and
      ``triggers`` keys are read.
    * ``predicate_registry`` — overridable for tests; defaults to the
      module-level ``TriggerPredicateRegistry``.
    * ``min_interval_seconds`` — schedule registrations below this floor
      are rejected. Mirrors ``SchedulerService.MIN_INTERVAL_SECONDS``
      so schedule triggers never reach the persisted scheduler with an
      interval it would already refuse.

    The engine validates and indexes at construction. A construction
    that doesn't raise means every loaded Job's triggers are
    well-formed and the static completion graph is acyclic. Run-time
    ``dispatch`` cannot encounter shape errors.
    """

    DEFAULT_MIN_INTERVAL_SECONDS = 60

    def __init__(
        self,
        job_defs: list[dict[str, Any]],
        *,
        predicate_registry: type[TriggerPredicateRegistry] = (
            TriggerPredicateRegistry
        ),
        min_interval_seconds: int = DEFAULT_MIN_INTERVAL_SECONDS,
    ) -> None:
        self._job_defs = list(job_defs)
        self._predicates = predicate_registry
        self._min_interval_seconds = int(min_interval_seconds)
        self._index: dict[str, list[tuple[str, dict[str, Any]]]] = {}
        self._validate_and_index()
        self._detect_cycles()

    # ------------------------------------------------------------------
    # Construction-time validation + indexing
    # ------------------------------------------------------------------

    def _validate_and_index(self) -> None:
        for job_def in self._job_defs:
            name = job_def.get("name", "")
            triggers = job_def.get("triggers", []) or []
            if not isinstance(triggers, list):
                raise InvalidTriggerError(
                    name, triggers, "triggers must be a list",
                )
            for trigger in triggers:
                self._validate_trigger(name, trigger)
                kind = trigger["event"]
                self._index.setdefault(kind, []).append((name, trigger))

    def _validate_trigger(
        self, job_name: str, trigger: Any,
    ) -> None:
        if not isinstance(trigger, dict):
            raise InvalidTriggerError(
                job_name, trigger, "trigger entry must be a mapping",
            )
        kind = trigger.get("event")
        if kind is None:
            raise InvalidTriggerError(
                job_name, trigger, "missing 'event:' key",
            )
        if not TriggerKinds.is_valid(kind):
            raise InvalidTriggerError(
                job_name, trigger,
                f"unknown 'event:' kind {kind!r}; "
                f"valid kinds are {sorted(TriggerKinds.ALL)}",
            )
        self._validate_required_secondaries(job_name, trigger, kind)
        # 'manual' and 'controller.started' have no required secondaries.
        # 'when:' validation deferred — predicates may register at
        # import time after the engine is built. Call
        # ``validate_when_predicates_now()`` after all imports complete
        # to perform the final check.

    def _validate_required_secondaries(
        self, job_name: str, trigger: dict[str, Any], kind: str,
    ) -> None:
        """Per-kind required-field check. Split out of
        ``_validate_trigger`` so the elif chain doesn't push the parent
        method past the codebase-wide deeply-nested ratchet (an AST
        elif chain pushes inner-If depth by one per branch).
        """
        if kind in (TriggerKinds.JOB_COMPLETED, TriggerKinds.JOB_FAILED):
            if not trigger.get("job"):
                raise InvalidTriggerError(
                    job_name, trigger,
                    f"event {kind!r} requires a 'job:' field naming "
                    "the upstream Job",
                )
            return
        if kind in (
            TriggerKinds.PROMISE_SATISFIED,
            TriggerKinds.PROMISE_VIOLATED,
        ):
            if not trigger.get("scope"):
                raise InvalidTriggerError(
                    job_name, trigger,
                    f"event {kind!r} requires a 'scope:' field "
                    "naming the orchestrator promise scope",
                )
            return
        if kind == TriggerKinds.SCHEDULE:
            if not (trigger.get("every") or trigger.get("cron")):
                raise InvalidTriggerError(
                    job_name, trigger,
                    "event 'schedule' requires 'every:' "
                    "(e.g. '5m') or 'cron:' (e.g. '*/5 * * * *')",
                )
            # 'every:' parses cleanly so schedule registration at boot
            # doesn't hit a surprise. 'cron:' validation is deferred to
            # register_schedules where the scheduler decides if it
            # accepts cron syntax.
            if "every" in trigger:
                self._parse_every(job_name, trigger)

    # ------------------------------------------------------------------
    # Cycle detection
    # ------------------------------------------------------------------

    def _detect_cycles(self) -> None:
        """Static cycle detection on the completion graph.

        Edge ``A → B`` exists if completing/failing Job A triggers
        Job B. Schedule and controller.started edges don't form
        cycles (they're external sources, not Job-completion edges)
        so they're ignored here.
        """
        graph: dict[str, set[str]] = {}
        for kind in (
            TriggerKinds.JOB_COMPLETED,
            TriggerKinds.JOB_FAILED,
        ):
            for downstream, trigger in self._index.get(kind, []):
                upstream = trigger["job"]
                graph.setdefault(upstream, set()).add(downstream)

        # DFS with white/grey/black colouring; the grey set IS the
        # current path so a cycle reports as a readable list.
        WHITE, GREY, BLACK = 0, 1, 2
        colour: dict[str, int] = {}
        path: list[str] = []

        def visit(node: str) -> None:
            colour[node] = GREY
            path.append(node)
            for nxt in sorted(graph.get(node, set())):
                state = colour.get(nxt, WHITE)
                if state == GREY:
                    # Cycle — emit only the cycle portion of the path.
                    start = path.index(nxt)
                    raise TriggerCycleError(path[start:] + [nxt])
                if state == WHITE:
                    visit(nxt)
            path.pop()
            colour[node] = BLACK

        for node in sorted(graph):
            if colour.get(node, WHITE) == WHITE:
                visit(node)

    # ------------------------------------------------------------------
    # Run-time dispatch
    # ------------------------------------------------------------------

    def dispatch(
        self,
        event_kind: str,
        *,
        ctx: Any = None,
        **payload: Any,
    ) -> list[str]:
        """Return the names of Jobs whose triggers match ``event_kind``
        and ``payload``.

        Filter rules:
        * ``job.completed`` / ``job.failed`` — payload ``job=<name>``
          must equal the trigger's ``job:`` field.
        * ``promise.satisfied`` / ``promise.violated`` — payload
          ``scope=<name>`` must equal the trigger's ``scope:`` field.
        * ``manual`` / ``schedule`` / ``controller.started`` — no
          additional filter.

        ``when:`` predicate gating is applied last: a trigger that
        otherwise matches but whose predicate returns false is
        skipped. Unknown predicate names raise ``KeyError``.

        Returns names in stable contract-load order — callers passing
        the result through ``JobRunner.run`` get a deterministic
        ordering across boots.
        """
        if not TriggerKinds.is_valid(event_kind):
            raise ValueError(f"unknown event_kind {event_kind!r}")
        result: list[str] = []
        for job_name, trigger in self._index.get(event_kind, []):
            if not self._payload_matches(trigger, payload):
                continue
            if not self._when_matches(trigger, ctx):
                continue
            result.append(job_name)
        return result

    def _payload_matches(
        self, trigger: dict[str, Any], payload: dict[str, Any],
    ) -> bool:
        kind = trigger["event"]
        if kind in (TriggerKinds.JOB_COMPLETED, TriggerKinds.JOB_FAILED):
            return trigger["job"] == payload.get("job")
        if kind in (
            TriggerKinds.PROMISE_SATISFIED,
            TriggerKinds.PROMISE_VIOLATED,
        ):
            return trigger["scope"] == payload.get("scope")
        return True

    def _when_matches(
        self, trigger: dict[str, Any], ctx: Any,
    ) -> bool:
        predicate_name = trigger.get("when")
        if not predicate_name:
            return True
        return self._predicates.evaluate(predicate_name, ctx)

    # ------------------------------------------------------------------
    # Late predicate validation + scheduler registration
    # ------------------------------------------------------------------

    def validate_when_predicates_now(self) -> None:
        """Final ``when:`` validation pass. Call after all plugin
        imports complete so the predicate registry is fully populated.

        Raises ``InvalidTriggerError`` for the first unknown predicate
        encountered.
        """
        for triggers in self._index.values():
            for job_name, trigger in triggers:
                name = trigger.get("when")
                if name and not self._predicates.is_known(name):
                    raise InvalidTriggerError(
                        job_name, trigger,
                        f"unknown 'when:' predicate {name!r}; "
                        f"registered: {sorted(self._predicates.known_names())}",
                    )

    def register_schedules(
        self, register_fn: Callable[..., Any],
    ) -> list[dict[str, Any]]:
        """Push every ``event: schedule`` trigger through ``register_fn``.

        ``register_fn`` is the existing scheduler's add-schedule
        callable. Decoupling via callable (instead of importing
        ``SchedulerService`` directly) keeps the engine module free
        of an ``api.services`` dependency — the application layer
        shouldn't reach into the api layer.

        Returns the list of registration payloads for the caller's
        records. ``cron:`` triggers are passed through unchanged so
        a cron-aware scheduler can consume them; non-cron-aware
        schedulers should reject them. ``every:`` triggers are
        normalised to integer seconds.
        """
        registrations: list[dict[str, Any]] = []
        for job_name, trigger in self._index.get(
            TriggerKinds.SCHEDULE, []
        ):
            payload: dict[str, Any] = {"action": job_name}
            if "every" in trigger:
                seconds = self._parse_every(job_name, trigger)
                if seconds < self._min_interval_seconds:
                    raise InvalidTriggerError(
                        job_name, trigger,
                        f"interval {seconds}s is below the scheduler "
                        f"floor of {self._min_interval_seconds}s",
                    )
                payload["interval_seconds"] = seconds
            else:
                payload["cron"] = trigger["cron"]
            register_fn(**payload)
            registrations.append(payload)
        return registrations

    def _parse_every(
        self, job_name: str, trigger: dict[str, Any],
    ) -> int:
        raw = str(trigger.get("every", "")).strip()
        match = _EVERY_RE.match(raw)
        if not match:
            raise InvalidTriggerError(
                job_name, trigger,
                f"unparseable 'every:' value {raw!r}; expected forms "
                "like '30s', '5m', '2h'",
            )
        value = int(match.group(1))
        unit = (match.group(2) or "s").lower()
        multiplier = _EVERY_UNIT_TO_SECONDS[unit]
        return value * multiplier

    # ------------------------------------------------------------------
    # Introspection (test + debug)
    # ------------------------------------------------------------------

    def jobs_for(self, event_kind: str) -> list[str]:
        """Return all job names indexed under ``event_kind``,
        regardless of payload. Test-friendly view of the index.
        """
        return [name for name, _ in self._index.get(event_kind, [])]

    def event_kinds(self) -> frozenset[str]:
        """Set of event kinds with at least one indexed trigger."""
        return frozenset(self._index.keys())


__all__ = [
    "InvalidTriggerError",
    "TriggerCycleError",
    "TriggerEngine",
]
