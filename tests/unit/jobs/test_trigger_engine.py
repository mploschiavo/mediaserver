"""Tests for ``TriggerEngine`` (ADR-0009 / Phase 6.2).

Pins:
1. Construction validates every trigger entry — unknown ``event:``
   kinds, missing required secondary fields, and unparseable
   ``every:`` values raise loudly with the offending job name.
2. Construction detects static cycles in the completion graph
   (``job.completed`` / ``job.failed`` edges).
3. Dispatch matches by ``(event, payload)`` and gates on ``when:``
   predicates.
4. ``register_schedules`` pushes every ``event: schedule`` trigger
   through the supplied register callable and rejects sub-floor
   intervals.
5. ``validate_when_predicates_now`` flags unknown predicate names
   only after the predicate registry is finalised.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.application.jobs.trigger_engine import (  # noqa: E402
    InvalidTriggerError,
    TriggerCycleError,
    TriggerEngine,
)
from media_stack.application.jobs.trigger_schema import (  # noqa: E402
    TriggerPredicateRegistry,
)


def _job(name: str, *triggers: dict) -> dict:
    return {"name": name, "triggers": list(triggers)}


class ConstructionValidationTests(unittest.TestCase):
    """6.2 — every shape error blocks construction."""

    def test_empty_input_constructs_cleanly(self) -> None:
        engine = TriggerEngine([])
        self.assertEqual(engine.event_kinds(), frozenset())

    def test_jobs_with_no_triggers_construct_cleanly(self) -> None:
        engine = TriggerEngine([{"name": "a", "triggers": []}])
        self.assertEqual(engine.event_kinds(), frozenset())

    def test_unknown_event_kind_rejected(self) -> None:
        with self.assertRaises(InvalidTriggerError) as ctx:
            TriggerEngine([_job("a", {"event": "Job.Completed"})])
        self.assertIn("unknown 'event:' kind", str(ctx.exception))
        self.assertEqual(ctx.exception.job_name, "a")

    def test_missing_event_key_rejected(self) -> None:
        with self.assertRaises(InvalidTriggerError) as ctx:
            TriggerEngine([_job("a", {"job": "x"})])
        self.assertIn("missing 'event:' key", str(ctx.exception))

    def test_non_dict_trigger_rejected(self) -> None:
        with self.assertRaises(InvalidTriggerError):
            TriggerEngine(
                [{"name": "a", "triggers": ["not-a-dict"]}],
            )

    def test_non_list_triggers_rejected(self) -> None:
        with self.assertRaises(InvalidTriggerError):
            TriggerEngine(
                [{"name": "a", "triggers": "string-not-list"}],
            )

    def test_job_completed_missing_job_field_rejected(self) -> None:
        with self.assertRaises(InvalidTriggerError) as ctx:
            TriggerEngine([_job("a", {"event": "job.completed"})])
        self.assertIn("requires a 'job:' field", str(ctx.exception))

    def test_promise_satisfied_missing_scope_rejected(self) -> None:
        with self.assertRaises(InvalidTriggerError) as ctx:
            TriggerEngine([_job("a", {"event": "promise.satisfied"})])
        self.assertIn("requires a 'scope:' field", str(ctx.exception))

    def test_schedule_missing_every_and_cron_rejected(self) -> None:
        with self.assertRaises(InvalidTriggerError) as ctx:
            TriggerEngine([_job("a", {"event": "schedule"})])
        self.assertIn("'every:'", str(ctx.exception))

    def test_unparseable_every_value_rejected(self) -> None:
        with self.assertRaises(InvalidTriggerError) as ctx:
            TriggerEngine(
                [_job("a", {"event": "schedule", "every": "five mins"})],
            )
        self.assertIn("unparseable", str(ctx.exception))

    def test_manual_and_controller_started_have_no_required_fields(
        self,
    ) -> None:
        engine = TriggerEngine(
            [
                _job("a", {"event": "manual"}),
                _job("b", {"event": "controller.started"}),
            ],
        )
        self.assertEqual(
            engine.event_kinds(),
            frozenset({"manual", "controller.started"}),
        )

    def test_index_groups_jobs_by_event_kind(self) -> None:
        engine = TriggerEngine([
            _job("a", {"event": "job.completed", "job": "x"}),
            _job("b", {"event": "job.completed", "job": "y"}),
            _job("c", {"event": "controller.started"}),
        ])
        self.assertEqual(
            sorted(engine.jobs_for("job.completed")),
            ["a", "b"],
        )
        self.assertEqual(
            engine.jobs_for("controller.started"),
            ["c"],
        )


class CycleDetectionTests(unittest.TestCase):
    """6.2 — static cycle detection in the completion graph."""

    def test_acyclic_graph_constructs(self) -> None:
        engine = TriggerEngine([
            _job("b", {"event": "job.completed", "job": "a"}),
            _job("c", {"event": "job.completed", "job": "b"}),
        ])
        self.assertEqual(
            sorted(engine.jobs_for("job.completed")), ["b", "c"],
        )

    def test_two_node_cycle_rejected(self) -> None:
        # a.completed -> run b ; b.completed -> run a
        with self.assertRaises(TriggerCycleError) as ctx:
            TriggerEngine([
                _job("b", {"event": "job.completed", "job": "a"}),
                _job("a", {"event": "job.completed", "job": "b"}),
            ])
        self.assertIn("a", ctx.exception.cycle)
        self.assertIn("b", ctx.exception.cycle)

    def test_self_cycle_rejected(self) -> None:
        with self.assertRaises(TriggerCycleError):
            TriggerEngine([
                _job("a", {"event": "job.completed", "job": "a"}),
            ])

    def test_three_node_cycle_rejected(self) -> None:
        with self.assertRaises(TriggerCycleError):
            TriggerEngine([
                _job("b", {"event": "job.completed", "job": "a"}),
                _job("c", {"event": "job.completed", "job": "b"}),
                _job("a", {"event": "job.completed", "job": "c"}),
            ])

    def test_schedule_and_controller_started_dont_form_cycles(self) -> None:
        # Both jobs trigger on external events — no completion edge.
        engine = TriggerEngine([
            _job("a", {"event": "schedule", "every": "5m"}),
            _job("b", {"event": "controller.started"}),
        ])
        self.assertEqual(
            engine.event_kinds(),
            frozenset({"schedule", "controller.started"}),
        )


class DispatchTests(unittest.TestCase):
    """6.2 — dispatch matches by (event, payload) + when: gating."""

    def setUp(self) -> None:
        TriggerPredicateRegistry._reset_for_tests()

    def tearDown(self) -> None:
        TriggerPredicateRegistry._reset_for_tests()

    def test_job_completed_matches_by_job_payload(self) -> None:
        engine = TriggerEngine([
            _job("recovery", {"event": "job.completed", "job": "bootstrap"}),
            _job("other", {"event": "job.completed", "job": "reconcile"}),
        ])
        self.assertEqual(
            engine.dispatch("job.completed", job="bootstrap"),
            ["recovery"],
        )
        self.assertEqual(
            engine.dispatch("job.completed", job="reconcile"),
            ["other"],
        )

    def test_promise_satisfied_matches_by_scope(self) -> None:
        engine = TriggerEngine([
            _job(
                "marker",
                {"event": "promise.satisfied", "scope": "initial-bootstrap"},
            ),
        ])
        self.assertEqual(
            engine.dispatch(
                "promise.satisfied", scope="initial-bootstrap",
            ),
            ["marker"],
        )
        self.assertEqual(
            engine.dispatch("promise.satisfied", scope="something-else"),
            [],
        )

    def test_unmatched_payload_returns_empty(self) -> None:
        engine = TriggerEngine([
            _job("a", {"event": "job.completed", "job": "bootstrap"}),
        ])
        self.assertEqual(
            engine.dispatch("job.completed", job="other"), [],
        )

    def test_controller_started_has_no_filter(self) -> None:
        engine = TriggerEngine([
            _job("auto", {"event": "controller.started"}),
        ])
        self.assertEqual(
            engine.dispatch("controller.started"), ["auto"],
        )

    def test_when_predicate_gates_dispatch(self) -> None:
        flag = {"open": False}
        TriggerPredicateRegistry.register(
            "gate", lambda ctx: flag["open"],
        )
        engine = TriggerEngine([
            _job(
                "a",
                {
                    "event": "controller.started",
                    "when": "gate",
                },
            ),
        ])
        self.assertEqual(engine.dispatch("controller.started"), [])
        flag["open"] = True
        self.assertEqual(
            engine.dispatch("controller.started"), ["a"],
        )

    def test_dispatch_with_unknown_event_kind_raises(self) -> None:
        engine = TriggerEngine([])
        with self.assertRaises(ValueError):
            engine.dispatch("not.a.real.event")

    def test_unknown_when_predicate_at_dispatch_raises_keyerror(
        self,
    ) -> None:
        engine = TriggerEngine([
            _job(
                "a",
                {"event": "controller.started", "when": "missing"},
            ),
        ])
        with self.assertRaises(KeyError):
            engine.dispatch("controller.started")


class LatePredicateValidationTests(unittest.TestCase):
    """6.2 — validate_when_predicates_now flags unknown predicates
    after the registry is finalised."""

    def setUp(self) -> None:
        TriggerPredicateRegistry._reset_for_tests()

    def tearDown(self) -> None:
        TriggerPredicateRegistry._reset_for_tests()

    def test_validate_with_all_known_predicates_passes(self) -> None:
        TriggerPredicateRegistry.register("ok", lambda ctx: True)
        engine = TriggerEngine([
            _job(
                "a",
                {"event": "controller.started", "when": "ok"},
            ),
        ])
        engine.validate_when_predicates_now()
        # Reaching here means no InvalidTriggerError was raised.
        self.assertTrue(
            TriggerPredicateRegistry.is_known("ok"),
        )

    def test_validate_with_unknown_predicate_raises(self) -> None:
        engine = TriggerEngine([
            _job(
                "a",
                {"event": "controller.started", "when": "absent"},
            ),
        ])
        with self.assertRaises(InvalidTriggerError) as ctx:
            engine.validate_when_predicates_now()
        self.assertIn("unknown 'when:' predicate", str(ctx.exception))


class ScheduleRegistrationTests(unittest.TestCase):
    """6.2 — register_schedules pushes through the supplied callable."""

    def test_every_value_normalised_to_seconds(self) -> None:
        engine = TriggerEngine([
            _job(
                "shadow",
                {"event": "schedule", "every": "5m"},
            ),
        ])
        calls: list[dict] = []
        engine.register_schedules(lambda **kw: calls.append(kw))
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["action"], "shadow")
        self.assertEqual(calls[0]["interval_seconds"], 300)

    def test_seconds_units_parse(self) -> None:
        engine = TriggerEngine(
            [_job("a", {"event": "schedule", "every": "120s"})],
        )
        calls: list[dict] = []
        engine.register_schedules(lambda **kw: calls.append(kw))
        self.assertEqual(calls[0]["interval_seconds"], 120)

    def test_hour_units_parse(self) -> None:
        engine = TriggerEngine(
            [_job("a", {"event": "schedule", "every": "2h"})],
        )
        calls: list[dict] = []
        engine.register_schedules(lambda **kw: calls.append(kw))
        self.assertEqual(calls[0]["interval_seconds"], 7200)

    def test_cron_field_passed_through(self) -> None:
        engine = TriggerEngine(
            [_job("a", {"event": "schedule", "cron": "*/5 * * * *"})],
        )
        calls: list[dict] = []
        engine.register_schedules(lambda **kw: calls.append(kw))
        self.assertEqual(calls[0]["cron"], "*/5 * * * *")
        self.assertNotIn("interval_seconds", calls[0])

    def test_sub_floor_interval_rejected_at_registration(self) -> None:
        engine = TriggerEngine(
            [_job("a", {"event": "schedule", "every": "30s"})],
        )
        with self.assertRaises(InvalidTriggerError) as ctx:
            engine.register_schedules(lambda **kw: None)
        self.assertIn("below the scheduler floor", str(ctx.exception))

    def test_no_schedule_triggers_means_no_calls(self) -> None:
        engine = TriggerEngine(
            [_job("a", {"event": "controller.started"})],
        )
        calls: list[dict] = []
        engine.register_schedules(lambda **kw: calls.append(kw))
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
