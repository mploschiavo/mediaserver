"""Schema tests for ADR-0009 Phase 6.1 (``triggers:``) and ADR-0010
Phase 7.1 (``satisfies:``) Job-contract additions.

Pins:

1. A ``plugin.jobs.<name>`` block with a ``triggers:`` list parses
   and the field appears verbatim in the discovered dict.
2. A ``satisfies: [promise-id]`` field parses and the field
   appears verbatim.
3. Both default to empty lists when absent (current Jobs keep
   working without modification).
4. ``TriggerKinds.is_valid`` accepts the closed set and rejects
   unknown values.
5. ``TriggerPredicateRegistry`` registers + evaluates predicates,
   reports unknown names, and refuses conflicting re-registration.
"""

from __future__ import annotations

import sys
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

import media_stack.application.jobs.framework as _jf  # noqa: E402
from media_stack.application.jobs.trigger_schema import (  # noqa: E402
    TriggerKinds,
    TriggerPredicateRegistry,
)


class _ContractDirHarness:
    """Context manager that points the framework at a tmp contracts dir."""

    def __init__(self, yaml_text: str) -> None:
        self._yaml_text = yaml_text
        self._tmp: Path | None = None

    def __enter__(self) -> Path:
        import os
        import tempfile

        self._tmp = Path(tempfile.mkdtemp(prefix="job-schema-test-"))
        (self._tmp / "fake_service.yaml").write_text(
            self._yaml_text, encoding="utf-8",
        )
        os.environ["SERVICES_REGISTRY_DIR"] = str(self._tmp)
        _jf._DISCOVERED_JOBS_CACHE = None
        return self._tmp

    def __exit__(self, *exc_info) -> None:
        import os
        import shutil

        os.environ.pop("SERVICES_REGISTRY_DIR", None)
        if self._tmp is not None:
            shutil.rmtree(self._tmp, ignore_errors=True)
        _jf._DISCOVERED_JOBS_CACHE = None


class JobContractSchemaTests(unittest.TestCase):
    """6.1 + 7.1 — new fields parse and surface in the discovered dict."""

    def test_triggers_block_round_trips_through_discovery(self) -> None:
        yaml_text = textwrap.dedent(
            """
            service:
              id: fakesvc
            plugin:
              jobs:
                test-with-triggers:
                  handler: pkg.mod:fn
                  phase: post
                  triggers:
                    - event: job.completed
                      job: bootstrap-deployment
                    - event: schedule
                      every: 5m
            """
        ).strip()
        with _ContractDirHarness(yaml_text):
            jobs = _jf.discover_jobs_from_contracts()
        by_name = {j["name"]: j for j in jobs}
        self.assertIn("test-with-triggers", by_name)
        triggers = by_name["test-with-triggers"]["triggers"]
        self.assertEqual(len(triggers), 2)
        self.assertEqual(triggers[0]["event"], "job.completed")
        self.assertEqual(triggers[0]["job"], "bootstrap-deployment")
        self.assertEqual(triggers[1]["event"], "schedule")
        self.assertEqual(triggers[1]["every"], "5m")

    def test_satisfies_field_round_trips_through_discovery(self) -> None:
        yaml_text = textwrap.dedent(
            """
            service:
              id: fakesvc
            plugin:
              jobs:
                test-with-satisfies:
                  handler: pkg.mod:fn
                  phase: default
                  satisfies:
                    - jellyfin-libraries-present
                    - jellyfin-livetv-configured
            """
        ).strip()
        with _ContractDirHarness(yaml_text):
            jobs = _jf.discover_jobs_from_contracts()
        by_name = {j["name"]: j for j in jobs}
        self.assertIn("test-with-satisfies", by_name)
        self.assertEqual(
            by_name["test-with-satisfies"]["satisfies"],
            ["jellyfin-libraries-present", "jellyfin-livetv-configured"],
        )

    def test_absent_triggers_and_satisfies_default_to_empty_lists(self) -> None:
        yaml_text = textwrap.dedent(
            """
            service:
              id: fakesvc
            plugin:
              jobs:
                legacy-job:
                  handler: pkg.mod:fn
                  phase: default
            """
        ).strip()
        with _ContractDirHarness(yaml_text):
            jobs = _jf.discover_jobs_from_contracts()
        by_name = {j["name"]: j for j in jobs}
        self.assertEqual(by_name["legacy-job"]["triggers"], [])
        self.assertEqual(by_name["legacy-job"]["satisfies"], [])


class TriggerKindsTests(unittest.TestCase):
    """6.1 — closed set of valid ``on:`` values."""

    def test_all_constants_appear_in_all_set(self) -> None:
        for attr in (
            "MANUAL", "SCHEDULE",
            "JOB_COMPLETED", "JOB_FAILED",
            "PROMISE_SATISFIED", "PROMISE_VIOLATED",
            "CONTROLLER_STARTED",
        ):
            value = getattr(TriggerKinds, attr)
            self.assertIn(
                value, TriggerKinds.ALL,
                f"{attr}={value!r} missing from TriggerKinds.ALL",
            )

    def test_is_valid_accepts_closed_set(self) -> None:
        for kind in TriggerKinds.ALL:
            self.assertTrue(TriggerKinds.is_valid(kind))

    def test_is_valid_rejects_unknown_kinds(self) -> None:
        for bogus in ("on.bootstrap", "completed", "Job.Completed", "", "*"):
            self.assertFalse(
                TriggerKinds.is_valid(bogus),
                f"{bogus!r} should not be a valid trigger kind",
            )


class TriggerPredicateRegistryTests(unittest.TestCase):
    """6.1 — when: predicate registry. Closed-but-extensible."""

    def setUp(self) -> None:
        TriggerPredicateRegistry._reset_for_tests()

    def tearDown(self) -> None:
        TriggerPredicateRegistry._reset_for_tests()

    def test_register_and_evaluate(self) -> None:
        TriggerPredicateRegistry.register(
            "always_true", lambda ctx: True,
        )
        self.assertTrue(TriggerPredicateRegistry.is_known("always_true"))
        self.assertTrue(
            TriggerPredicateRegistry.evaluate("always_true", object()),
        )

    def test_unknown_predicate_raises_keyerror_on_evaluate(self) -> None:
        with self.assertRaises(KeyError):
            TriggerPredicateRegistry.evaluate("nope", object())

    def test_is_known_false_for_unregistered(self) -> None:
        self.assertFalse(TriggerPredicateRegistry.is_known("anything"))

    def test_idempotent_reregistration_with_same_callable(self) -> None:
        fn = lambda ctx: False  # noqa: E731
        TriggerPredicateRegistry.register("p", fn)
        TriggerPredicateRegistry.register("p", fn)
        self.assertTrue(TriggerPredicateRegistry.is_known("p"))

    def test_conflicting_reregistration_raises_valueerror(self) -> None:
        TriggerPredicateRegistry.register("p", lambda ctx: True)
        with self.assertRaises(ValueError):
            TriggerPredicateRegistry.register("p", lambda ctx: False)

    def test_known_names_returns_frozenset_snapshot(self) -> None:
        TriggerPredicateRegistry.register("a", lambda ctx: True)
        TriggerPredicateRegistry.register("b", lambda ctx: True)
        names = TriggerPredicateRegistry.known_names()
        self.assertIsInstance(names, frozenset)
        self.assertEqual(names, frozenset({"a", "b"}))


if __name__ == "__main__":
    unittest.main()
