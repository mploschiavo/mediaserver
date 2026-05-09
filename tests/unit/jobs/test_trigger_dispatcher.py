"""Tests for ``TriggerDispatchService`` + the module-level singleton
accessor (ADR-0009 / Phase 6.3).

Pins:
1. ``fire`` calls the engine, spawns a thread per matched job, and
   returns the dispatched names.
2. Predicate ``KeyError`` from the engine is logged but does not
   propagate — the publishing site keeps running.
3. ``run_fn`` exceptions are caught inside the daemon thread —
   one bad triggered job doesn't kill the dispatcher.
4. ``set_trigger_dispatcher`` / ``get_trigger_dispatcher`` round
   trip; ``fire_event`` is a no-op when no dispatcher is set.
"""

from __future__ import annotations

import sys
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.application.jobs.trigger_dispatcher import (  # noqa: E402
    TriggerDispatchService,
    TriggerDispatcherSingleton,
)
from media_stack.application.jobs.trigger_engine import (  # noqa: E402
    TriggerEngine,
)
from media_stack.application.jobs.trigger_schema import (  # noqa: E402
    TriggerPredicateRegistry,
)


def _job(name: str, *triggers: dict) -> dict:
    return {"name": name, "triggers": list(triggers)}


class _SyncThread:
    """Stand-in for ``threading.Thread`` that runs the target inline.

    Lets tests inspect run results without race conditions. Mirrors
    ``threading.Thread``'s constructor signature for the kwargs the
    dispatcher uses (``target``, ``daemon``, ``name``).
    """

    def __init__(self, *, target, daemon=True, name=""):  # noqa: ARG002
        self._target = target
        self.name = name

    def start(self) -> None:
        self._target()


class TriggerDispatchServiceTests(unittest.TestCase):

    def setUp(self) -> None:
        TriggerPredicateRegistry._reset_for_tests()

    def tearDown(self) -> None:
        TriggerPredicateRegistry._reset_for_tests()
        TriggerDispatcherSingleton.set(None)

    def test_fire_runs_each_matched_job(self) -> None:
        engine = TriggerEngine([
            _job("a", {"event": "controller.started"}),
            _job("b", {"event": "controller.started"}),
        ])
        ran: list[str] = []
        svc = TriggerDispatchService(
            engine,
            run_fn=lambda name: ran.append(name),
            thread_factory=_SyncThread,
        )
        result = svc.fire("controller.started")
        self.assertEqual(sorted(result), ["a", "b"])
        self.assertEqual(sorted(ran), ["a", "b"])

    def test_fire_returns_only_matched_names(self) -> None:
        engine = TriggerEngine([
            _job("recovery", {"event": "job.completed", "job": "bootstrap"}),
            _job("other", {"event": "job.completed", "job": "reconcile"}),
        ])
        ran: list[str] = []
        svc = TriggerDispatchService(
            engine,
            run_fn=lambda name: ran.append(name),
            thread_factory=_SyncThread,
        )
        result = svc.fire("job.completed", job="bootstrap")
        self.assertEqual(result, ["recovery"])
        self.assertEqual(ran, ["recovery"])

    def test_run_fn_exception_is_caught_inside_thread(self) -> None:
        engine = TriggerEngine([
            _job("a", {"event": "controller.started"}),
        ])
        def failing(_name: str) -> None:
            raise RuntimeError("triggered job blew up")
        svc = TriggerDispatchService(
            engine, run_fn=failing, thread_factory=_SyncThread,
        )
        # Should NOT raise — exception is logged, dispatcher continues.
        result = svc.fire("controller.started")
        self.assertEqual(result, ["a"])

    def test_unknown_when_predicate_logged_not_raised(self) -> None:
        engine = TriggerEngine([
            _job(
                "a",
                {"event": "controller.started", "when": "missing"},
            ),
        ])
        ran: list[str] = []
        svc = TriggerDispatchService(
            engine,
            run_fn=lambda name: ran.append(name),
            thread_factory=_SyncThread,
        )
        # KeyError from the engine becomes "no jobs matched" — must
        # not propagate to the publishing site.
        result = svc.fire("controller.started")
        self.assertEqual(result, [])
        self.assertEqual(ran, [])

    def test_thread_factory_default_is_real_threading_Thread(
        self,
    ) -> None:
        # Real threads — wait briefly for the spawned daemon to land.
        engine = TriggerEngine([
            _job("a", {"event": "controller.started"}),
        ])
        completed = threading.Event()
        svc = TriggerDispatchService(
            engine, run_fn=lambda name: completed.set(),
        )
        svc.fire("controller.started")
        self.assertTrue(completed.wait(timeout=2.0),
                        "daemon thread did not run within 2 seconds")

    def test_engine_property_exposes_underlying_engine(self) -> None:
        engine = TriggerEngine([])
        svc = TriggerDispatchService(engine, run_fn=lambda n: None)
        self.assertIs(svc.engine, engine)


class SingletonAccessorTests(unittest.TestCase):

    def setUp(self) -> None:
        TriggerDispatcherSingleton.set(None)

    def tearDown(self) -> None:
        TriggerDispatcherSingleton.set(None)

    def test_get_returns_none_before_set(self) -> None:
        self.assertIsNone(TriggerDispatcherSingleton.get())

    def test_set_then_get_round_trips(self) -> None:
        engine = TriggerEngine([])
        svc = TriggerDispatchService(engine, run_fn=lambda n: None)
        TriggerDispatcherSingleton.set(svc)
        self.assertIs(TriggerDispatcherSingleton.get(), svc)

    def test_set_none_clears(self) -> None:
        engine = TriggerEngine([])
        svc = TriggerDispatchService(engine, run_fn=lambda n: None)
        TriggerDispatcherSingleton.set(svc)
        TriggerDispatcherSingleton.set(None)
        self.assertIsNone(TriggerDispatcherSingleton.get())

    def test_fire_event_is_noop_when_no_dispatcher(self) -> None:
        # No exception, returns empty list.
        self.assertEqual(TriggerDispatcherSingleton.fire("controller.started"), [])

    def test_fire_event_routes_to_installed_dispatcher(self) -> None:
        engine = TriggerEngine([
            _job("a", {"event": "controller.started"}),
        ])
        ran: list[str] = []
        svc = TriggerDispatchService(
            engine,
            run_fn=lambda name: ran.append(name),
            thread_factory=_SyncThread,
        )
        TriggerDispatcherSingleton.set(svc)
        result = TriggerDispatcherSingleton.fire("controller.started")
        self.assertEqual(result, ["a"])
        self.assertEqual(ran, ["a"])


if __name__ == "__main__":
    unittest.main()
