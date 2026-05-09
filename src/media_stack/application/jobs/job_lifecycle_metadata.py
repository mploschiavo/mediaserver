"""Declarative end-of-batch side-effects for Job contracts
(ADR-0009 Phase 6.4 redo).

Per-Job side-effects live as fields on the Job's contract entry,
not in bespoke handler files. Two fields are recognised:

* ``marks_setup_complete: true`` — on successful completion, the
  framework records the deployment-setup-complete signal on the
  installed ``ControllerState``. Multiple Jobs may set this; the
  call is idempotent at the state level.

* ``retry_on_failure: { delay_seconds, target, when }`` — on
  failure (with the optional ``when`` predicate passing), the
  framework spawns a daemon timer that dispatches the ``target``
  Job after ``delay_seconds`` via the trigger dispatcher. The
  retrigger lands in run history identically to a manual run.

Both are read at end-of-batch by ``JobLifecycleMetadataHandler`` —
no per-Job handler file, no string-named handler in the contract.
Plugins extend by adding the same fields to their own Job entries.

The ``marks_setup_complete`` path currently routes to
``ControllerState.mark_initial_bootstrap_done()`` because that is
what ``api/state.py`` exposes today. A future refactor that
genericises ``ControllerState`` to a named-flag dictionary would
let plugins declare ``marks_setup_complete: <flag-name>`` (string)
and route through ``state.set_deployment_flag(name)`` — out of
scope for this phase.
"""

from __future__ import annotations

import threading
from typing import Any, Callable

from media_stack.core.logging_utils import log_swallowed
from media_stack.application.jobs.controller_state_accessor import (
    ControllerStateAccessor,
)
from media_stack.application.jobs.trigger_dispatcher import (
    TriggerDispatcherSingleton,
)
from media_stack.application.jobs.trigger_schema import (
    TriggerPredicateRegistry,
)


class JobLifecycleMetadataHandler:
    """Reads contract-declared end-of-batch metadata from a Job's
    discovery dict and applies the requested side-effect.

    Construction takes the side-effect dependencies (state accessor,
    trigger fire callable, timer factory) so tests can substitute
    in-memory stubs. Production code uses
    ``JobLifecycleMetadataHandler.default()`` to get an instance
    bound to the live singletons.
    """

    DEFAULT_RETRY_DELAY_SECONDS: int = 2 * 60

    def __init__(
        self,
        *,
        state_accessor: type = ControllerStateAccessor,
        dispatch_named: Callable[[str], bool] | None = None,
        timer_factory: Callable[..., threading.Timer] | None = None,
    ) -> None:
        self._state_accessor = state_accessor
        self._dispatch_named = (
            dispatch_named or TriggerDispatcherSingleton.dispatch_named
        )
        self._timer_factory = timer_factory or threading.Timer

    @classmethod
    def default(cls) -> "JobLifecycleMetadataHandler":
        """Production-bound instance — wires to the live singletons."""
        return cls()

    def apply_on_completion(self, job_def: dict[str, Any]) -> None:
        """Apply the success-path side-effect declared on ``job_def``.

        Called once from ``JobRunner.run`` end-of-batch when ``errors
        == 0``. The declarative success effect is
        ``marks_setup_complete: <flag-name>`` (a string); the
        framework calls ``state.set_deployment_flag(<flag-name>)``
        and ``state.is_deployment_flag_set(<flag-name>)`` so plugins
        can declare their own flags without code changes.

        Boolean ``marks_setup_complete: true`` is accepted as a
        back-compat shorthand for the canonical
        ``initial_bootstrap_done`` flag — useful for the bootstrap
        Job that predates the dict-backed mechanism. New contracts
        should use the explicit flag-name form.
        """
        flag_name = self._resolve_setup_flag_name(job_def)
        if not flag_name:
            return
        state = self._state_accessor.get()
        if state is None:
            return
        if state.is_deployment_flag_set(flag_name):
            return
        try:
            state.set_deployment_flag(flag_name)
        except Exception as exc:  # noqa: BLE001
            log_swallowed(exc)

    @classmethod
    def _resolve_setup_flag_name(
        cls, job_def: dict[str, Any],
    ) -> str | None:
        raw = job_def.get("marks_setup_complete")
        if isinstance(raw, str) and raw.strip():
            return raw.strip()
        if raw is True:
            # Back-compat shorthand: bare ``true`` means the historical
            # bootstrap-completion flag.
            return "initial_bootstrap_done"
        return None

    def apply_on_failure(self, job_def: dict[str, Any]) -> None:
        """Apply the failure-path side-effect declared on ``job_def``.

        Currently the only declarative failure effect is
        ``retry_on_failure: { target, delay_seconds, when }`` — a
        delayed retrigger of another Job, gated by an optional
        predicate. The retrigger goes through the existing
        ``TriggerDispatcherSingleton`` so the retry shows up in run
        history identically to a manual or scheduled invocation.
        """
        retry = job_def.get("retry_on_failure")
        if not isinstance(retry, dict):
            return
        target = retry.get("target")
        if not target:
            return
        if not self._predicate_passes(retry.get("when")):
            return
        delay = self._resolve_delay(retry)
        timer = self._timer_factory(
            delay,
            self._fire_retrigger,
            kwargs={"target": target},
        )
        timer.daemon = True
        timer.start()

    def _resolve_delay(self, retry: dict[str, Any]) -> float:
        raw = retry.get("delay_seconds", self.DEFAULT_RETRY_DELAY_SECONDS)
        try:
            return float(raw)
        except (TypeError, ValueError):
            return float(self.DEFAULT_RETRY_DELAY_SECONDS)

    def _predicate_passes(self, predicate_name: str | None) -> bool:
        if not predicate_name:
            return True
        if not TriggerPredicateRegistry.is_known(predicate_name):
            return False
        try:
            state = self._state_accessor.get()
            return TriggerPredicateRegistry.evaluate(
                predicate_name, state,
            )
        except Exception as exc:  # noqa: BLE001
            log_swallowed(exc)
            return False

    def _fire_retrigger(self, *, target: str) -> None:
        try:
            self._dispatch_named(target)
        except Exception as exc:  # noqa: BLE001
            log_swallowed(exc)


__all__ = ["JobLifecycleMetadataHandler"]
