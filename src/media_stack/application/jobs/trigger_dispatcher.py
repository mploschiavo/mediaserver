"""Glue between ``TriggerEngine.dispatch`` and ``JobRunner.run``
(ADR-0009 / Phase 6.3).

When a lifecycle hook fires (job batch completes, promise scope
satisfied, etc.), the dispatcher asks the engine which jobs match,
then runs each in its own daemon thread so the publishing site
isn't blocked by downstream work. Daemon threads match the
existing ``non_blocking`` Job pattern in
``application/jobs/framework.py`` — no new threading abstraction.

The controller's startup wires a single ``TriggerDispatchService``
into the singleton via ``TriggerDispatcherSingleton.set``;
publishers (``JobRunner.run``, controller boot) read it via
``TriggerDispatcherSingleton.fire``. Tests that don't want
triggers to fire just leave the singleton unset, which is the
default — ``fire(...)`` becomes a no-op.

The dispatcher does NOT default to importing
``framework.run_job``; the caller passes ``run_fn`` at construction.
This keeps the dispatcher module free of any inbound dependency on
``framework``, which the framework-side hook would otherwise
import — closing the would-be import cycle at the static analyser
level.
"""

from __future__ import annotations

import threading
from typing import Any, Callable

from media_stack.core.logging_utils import log_swallowed
from media_stack.services import runtime_platform
from media_stack.application.jobs.trigger_engine import TriggerEngine


class TriggerDispatchService:
    """Routes lifecycle events from publishers to ``JobRunner.run``.

    Constructor injection:
    * ``engine`` — a ready-to-dispatch ``TriggerEngine`` (already
      validated + indexed at construction).
    * ``run_fn`` — required callable ``(job_name: str) -> Any``
      invoked on a daemon thread for each matched job.
    * ``thread_factory`` — overridable for tests so threading
      behaviour can be replaced with a synchronous callable.

    ``fire`` is the only public publishing method. It catches and
    logs predicate-evaluation errors so a faulty ``when:`` predicate
    doesn't prevent the publishing site from continuing — the
    downstream Jobs that the predicate gated simply don't run.
    """

    def __init__(
        self,
        engine: TriggerEngine,
        *,
        run_fn: Callable[[str], Any],
        thread_factory: Callable[..., threading.Thread] | None = None,
    ) -> None:
        self._engine = engine
        self._run_fn = run_fn
        self._thread_factory = thread_factory or threading.Thread

    def fire(
        self,
        event_kind: str,
        *,
        ctx: Any = None,
        **payload: Any,
    ) -> list[str]:
        """Look up matching jobs for ``event_kind`` + ``payload`` and
        spawn each in its own daemon thread.

        Returns the list of job names actually launched (for logs +
        tests). Engine-side validation errors propagate; a faulty
        predicate is logged and treated as "no jobs matched" for
        the offending trigger so the publisher stays running.
        """
        try:
            names = self._engine.dispatch(event_kind, ctx=ctx, **payload)
        except KeyError as exc:
            self._log_predicate_error(event_kind, payload, exc)
            return []
        for name in names:
            self._spawn(name)
        return names

    def dispatch_named(self, name: str) -> None:
        """Dispatch a single Job by name via the configured ``run_fn``.

        Used by callers that already know which Job to run and want
        the dispatcher's ``run_fn`` (e.g. the action queue adapter)
        rather than constructing a synthetic event. The retrigger
        path in ``JobLifecycleMetadataHandler`` uses this to fire
        the ``retry_on_failure.target`` Job after the timer expires.
        """
        self._spawn(name)

    def _spawn(self, name: str) -> None:
        thread = self._thread_factory(
            target=lambda: self._run_safely(name),
            daemon=True,
            name=f"triggered-{name}",
        )
        thread.start()

    def _run_safely(self, name: str) -> None:
        try:
            self._run_fn(name)
        except Exception as exc:  # noqa: BLE001
            self._log_run_error(name, exc)

    @classmethod
    def _log_predicate_error(
        cls, event_kind: str, payload: dict, exc: KeyError,
    ) -> None:
        try:
            runtime_platform.log(
                f"[WARN] trigger {event_kind} payload={payload}: "
                f"unknown 'when:' predicate {exc!s} — no jobs matched"
            )
        except Exception as logger_exc:  # noqa: BLE001
            log_swallowed(logger_exc)

    @classmethod
    def _log_run_error(cls, name: str, exc: BaseException) -> None:
        try:
            runtime_platform.log(
                f"[ERR] triggered job {name!r} raised: {exc}"
            )
        except Exception as logger_exc:  # noqa: BLE001
            log_swallowed(logger_exc)

    @property
    def engine(self) -> TriggerEngine:
        """Read-only view of the underlying engine. Useful for the
        controller's boot sequence to call
        ``engine.register_schedules`` after the dispatcher is
        constructed."""
        return self._engine


class TriggerDispatcherSingleton:
    """Module-singleton accessor for the installed dispatcher.

    Mirrors the ``request_cancel`` / ``clear_cancel`` /
    ``_is_cancel_requested`` pattern in ``framework.py`` — module-
    state for cross-module signal wiring, set once at controller
    boot. A class wraps the storage so the loose-functions ratchet
    stays satisfied; instance state lives on the class.

    Tests reset between cases by calling ``set(None)`` in tearDown.
    """

    _installed: TriggerDispatchService | None = None
    _lock: threading.Lock = threading.Lock()

    @classmethod
    def set(cls, dispatcher: TriggerDispatchService | None) -> None:
        """Install (or clear, with ``None``) the dispatcher.

        Called once by controller startup after every plugin import
        has finished, so ``TriggerEngine`` sees the final set of
        contracts and registered ``when:`` predicates. Tests that
        rebuild the framework call this with a fresh dispatcher
        each setUp.
        """
        with cls._lock:
            cls._installed = dispatcher

    @classmethod
    def get(cls) -> TriggerDispatchService | None:
        """Return the installed dispatcher, or ``None`` before
        startup has wired one. Publishing sites are responsible for
        the None-check; "no dispatcher yet" is the legitimate
        boot-time state, not an error."""
        return cls._installed

    @classmethod
    def fire(
        cls,
        event_kind: str,
        *,
        ctx: Any = None,
        **payload: Any,
    ) -> list[str]:
        """Convenience publisher: look up the singleton and call
        ``fire``. Returns ``[]`` if no dispatcher is installed.

        Exists so publishing sites stay one-line:

            ``TriggerDispatcherSingleton.fire("job.completed", job=root.name)``

        instead of the four-line guard ``if cls.get() is not None: ...``.
        """
        dispatcher = cls._installed
        if dispatcher is None:
            return []
        return dispatcher.fire(event_kind, ctx=ctx, **payload)

    @classmethod
    def dispatch_named(cls, name: str) -> bool:
        """Dispatch a single Job by name. Returns whether a
        dispatcher was installed to handle it (``False`` is the
        legitimate boot-time state, not an error)."""
        dispatcher = cls._installed
        if dispatcher is None:
            return False
        dispatcher.dispatch_named(name)
        return True


__all__ = [
    "TriggerDispatchService",
    "TriggerDispatcherSingleton",
]
