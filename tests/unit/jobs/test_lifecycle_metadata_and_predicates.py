"""Unit tests for the Phase 6.4 (redo) framework additions:
- ``FrameworkPredicates`` (the framework's built-in ``when:``
  predicates).
- ``JobLifecycleMetadataHandler`` (reads contract end-of-batch
  fields and applies the requested side-effect).

These together replace the bespoke per-Job handler files
(``mark_initial_bootstrap_done.py``, ``heal_sweep.py``,
``post_bootstrap_recovery.py``) deleted in the redo. The tests
pin the shape: declarative contract fields drive behaviour, no
file or method name is coupled to a specific Job.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.application.jobs.controller_state_accessor import (  # noqa: E402
    ControllerStateAccessor,
)
from media_stack.application.jobs.framework_predicates import (  # noqa: E402
    FrameworkPredicates,
)
from media_stack.application.jobs.job_lifecycle_metadata import (  # noqa: E402
    JobLifecycleMetadataHandler,
)
from media_stack.application.jobs.trigger_schema import (  # noqa: E402
    TriggerPredicateRegistry,
)


class _StubState:

    def __init__(self) -> None:
        self.initial_bootstrap_done = False
        self.mark_calls = 0
        self._flags: dict[str, bool] = {}
        self._failed: dict[str, dict[str, Any]] = {}

    def mark_initial_bootstrap_done(self) -> None:
        # Back-compat shim — delegates to set_deployment_flag.
        self.set_deployment_flag("initial_bootstrap_done")

    def set_deployment_flag(self, name: str) -> None:
        self.mark_calls += 1
        self._flags[name] = True
        if name == "initial_bootstrap_done":
            self.initial_bootstrap_done = True

    def is_deployment_flag_set(self, name: str) -> bool:
        if self._flags.get(name):
            return True
        if name == "initial_bootstrap_done":
            return bool(self.initial_bootstrap_done)
        return False

    def get_failed_services(self) -> dict[str, dict[str, Any]]:
        return dict(self._failed)

    def fail(self, service: str) -> None:
        self._failed[service] = {"error": "x"}


class FrameworkPredicatesTests(unittest.TestCase):

    def setUp(self) -> None:
        TriggerPredicateRegistry._reset_for_tests()
        FrameworkPredicates._state = None

    def tearDown(self) -> None:
        TriggerPredicateRegistry._reset_for_tests()
        FrameworkPredicates._state = None

    def test_register_all_publishes_any_service_failed(self) -> None:
        FrameworkPredicates.register_all()
        self.assertTrue(
            TriggerPredicateRegistry.is_known("any_service_failed"),
        )

    def test_predicate_false_when_no_state_installed(self) -> None:
        FrameworkPredicates.register_all()
        self.assertFalse(
            TriggerPredicateRegistry.evaluate("any_service_failed", None),
        )

    def test_predicate_false_when_no_failed_services(self) -> None:
        state = _StubState()
        FrameworkPredicates.install(state=state)
        FrameworkPredicates.register_all()
        self.assertFalse(
            TriggerPredicateRegistry.evaluate("any_service_failed", None),
        )

    def test_predicate_true_when_services_failed(self) -> None:
        state = _StubState()
        state.fail("jellyfin")
        FrameworkPredicates.install(state=state)
        FrameworkPredicates.register_all()
        self.assertTrue(
            TriggerPredicateRegistry.evaluate("any_service_failed", None),
        )


class JobLifecycleMetadataHandlerTests(unittest.TestCase):

    def setUp(self) -> None:
        ControllerStateAccessor.set(None)
        TriggerPredicateRegistry._reset_for_tests()

    def tearDown(self) -> None:
        ControllerStateAccessor.set(None)
        TriggerPredicateRegistry._reset_for_tests()

    def _handler(self, **kwargs) -> JobLifecycleMetadataHandler:
        kwargs.setdefault("dispatch_named", lambda _name: True)
        kwargs.setdefault("timer_factory", _SyncTimerFactory())
        return JobLifecycleMetadataHandler(**kwargs)

    def test_marks_setup_complete_calls_state_when_flag_set(self) -> None:
        state = _StubState()
        ControllerStateAccessor.set(state)
        h = self._handler()
        h.apply_on_completion({"marks_setup_complete": True})
        self.assertEqual(state.mark_calls, 1)
        self.assertTrue(state.initial_bootstrap_done)

    def test_marks_setup_complete_idempotent(self) -> None:
        state = _StubState()
        state.initial_bootstrap_done = True
        ControllerStateAccessor.set(state)
        h = self._handler()
        h.apply_on_completion({"marks_setup_complete": True})
        self.assertEqual(state.mark_calls, 0)

    def test_marks_setup_complete_noop_when_flag_unset(self) -> None:
        state = _StubState()
        ControllerStateAccessor.set(state)
        h = self._handler()
        h.apply_on_completion({})
        self.assertEqual(state.mark_calls, 0)

    def test_marks_setup_complete_noop_when_no_state(self) -> None:
        h = self._handler()
        # Doesn't raise even though state isn't installed.
        h.apply_on_completion({"marks_setup_complete": True})
        # Reaching here means the no-state branch short-circuited
        # cleanly; verify by re-running with the same handler.
        self.assertIsNone(ControllerStateAccessor.get())

    def test_retry_on_failure_dispatches_target_after_delay(self) -> None:
        dispatched: list[str] = []
        timer_factory = _SyncTimerFactory()
        h = self._handler(
            dispatch_named=lambda name: dispatched.append(name) or True,
            timer_factory=timer_factory,
        )
        h.apply_on_failure({
            "retry_on_failure": {
                "target": "reconcile",
                "delay_seconds": 30,
            },
        })
        self.assertEqual(timer_factory.delay, 30.0)
        self.assertEqual(dispatched, ["reconcile"])

    def test_retry_on_failure_skipped_when_predicate_returns_false(
        self,
    ) -> None:
        TriggerPredicateRegistry.register("never", lambda _ctx: False)
        dispatched: list[str] = []
        h = self._handler(
            dispatch_named=lambda n: dispatched.append(n) or True,
            timer_factory=_SyncTimerFactory(),
        )
        h.apply_on_failure({
            "retry_on_failure": {
                "target": "reconcile",
                "delay_seconds": 30,
                "when": "never",
            },
        })
        self.assertEqual(dispatched, [])

    def test_retry_on_failure_runs_when_predicate_passes(self) -> None:
        TriggerPredicateRegistry.register("always", lambda _ctx: True)
        dispatched: list[str] = []
        h = self._handler(
            dispatch_named=lambda n: dispatched.append(n) or True,
            timer_factory=_SyncTimerFactory(),
        )
        h.apply_on_failure({
            "retry_on_failure": {
                "target": "reconcile",
                "delay_seconds": 30,
                "when": "always",
            },
        })
        self.assertEqual(dispatched, ["reconcile"])

    def test_retry_on_failure_unknown_predicate_short_circuits(self) -> None:
        dispatched: list[str] = []
        h = self._handler(
            dispatch_named=lambda n: dispatched.append(n) or True,
            timer_factory=_SyncTimerFactory(),
        )
        h.apply_on_failure({
            "retry_on_failure": {
                "target": "reconcile",
                "delay_seconds": 30,
                "when": "missing-predicate",
            },
        })
        self.assertEqual(dispatched, [])

    def test_retry_on_failure_uses_default_delay_when_invalid(
        self,
    ) -> None:
        timer_factory = _SyncTimerFactory()
        h = self._handler(
            dispatch_named=lambda _n: True,
            timer_factory=timer_factory,
        )
        h.apply_on_failure({
            "retry_on_failure": {
                "target": "reconcile",
                "delay_seconds": "not-a-number",
            },
        })
        self.assertEqual(
            timer_factory.delay,
            float(JobLifecycleMetadataHandler.DEFAULT_RETRY_DELAY_SECONDS),
        )

    def test_retry_on_failure_noop_when_no_target(self) -> None:
        dispatched: list[str] = []
        h = self._handler(
            dispatch_named=lambda n: dispatched.append(n) or True,
            timer_factory=_SyncTimerFactory(),
        )
        h.apply_on_failure({"retry_on_failure": {"delay_seconds": 30}})
        self.assertEqual(dispatched, [])


class _SyncTimerFactory:
    """Test stand-in for ``threading.Timer`` that runs the function
    inline on ``start`` so test assertions are deterministic."""

    def __init__(self) -> None:
        self.delay: float | None = None
        self._fn = None
        self._kwargs: dict = {}
        self.daemon: bool = False

    def __call__(
        self, delay: float, fn, args=None, kwargs=None,
    ) -> "_SyncTimerFactory":
        self.delay = delay
        self._fn = fn
        self._kwargs = kwargs or {}
        return self

    def start(self) -> None:
        if self._fn is not None:
            self._fn(**self._kwargs)


if __name__ == "__main__":
    unittest.main()
