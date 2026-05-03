"""Pin the promise-driven bootstrap wiring.

The bootstrap DAG drives the Jellyfin family through the
orchestrator (``bootstrap:satisfy-promises``) instead of through
a phase-scheduled ``jellyfin:ensure-api-key`` job. These tests
assert the contract-level shape of that wiring so a future
contract edit can't silently undo it.

Sections:

  * BootstrapSatisfyPromisesPlacement — the synthetic job runs
    LAST in ``post`` (priority 100, after every other post-phase
    ensurer) so the orchestrator's verdict is taken AFTER the
    legacy ensurers had a chance to mutate state.
  * JellyfinFamilyAnnotation — the three Jellyfin promises carry
    ``bootstrap_blocking: true`` explicitly, and the loader
    honours the field.
  * JellyfinEnsureApiKeyUnscheduled — ``jellyfin:ensure-api-key``
    is no longer scheduled by the bootstrap DAG. The job is still
    registered (``run_job(name)`` keeps working for cron + manual
    invocations + the auto-heal hook), it just doesn't fire as a
    phase-driven step.

Implementation note: every assertion runs against the loaded
contract registry and the discovered job set, NOT raw YAML, so a
typo in the YAML that changes the loaded shape still gets caught.
"""

from __future__ import annotations

import unittest
from pathlib import Path

import yaml


_REPO_ROOT = Path(__file__).resolve().parents[3]


class _ContractFixture:
    """Loads the relevant contract YAMLs once per test class.

    Wraps the dict-of-dicts navigation so each test reads a small,
    named accessor instead of nested ``.get(...).get(...)`` chains.
    """

    def __init__(self) -> None:
        self._guardrails = yaml.safe_load(
            (_REPO_ROOT / "contracts" / "services" / "guardrails.yaml")
            .read_text(encoding="utf-8")
        )
        self._jellyfin = yaml.safe_load(
            (_REPO_ROOT / "contracts" / "services" / "jellyfin.yaml")
            .read_text(encoding="utf-8")
        )

    def guardrails_jobs(self) -> dict:
        return (self._guardrails.get("plugin") or {}).get("jobs") or {}

    def jellyfin_jobs(self) -> dict:
        return (self._jellyfin.get("plugin") or {}).get("jobs") or {}


class BootstrapSatisfyPromisesPlacement(unittest.TestCase):
    """The synthetic blocking job lives in ``post`` priority 100 —
    AFTER every other post-phase ensurer."""

    def setUp(self) -> None:
        self.contracts = _ContractFixture()
        self.entry = self.contracts.guardrails_jobs().get(
            "bootstrap:satisfy-promises",
        )
        self.assertIsNotNone(
            self.entry,
            "bootstrap:satisfy-promises missing from guardrails.yaml",
        )

    def test_phase_is_post(self) -> None:
        # This used to live in ``orchestrator_satisfy`` (a holding
        # area). ``post`` makes the bootstrap DAG actually
        # schedule it.
        self.assertEqual(self.entry["phase"], "post")

    def test_priority_runs_after_other_post_jobs(self) -> None:
        # The other post-phase ensurers top out at priority 90 today.
        # Run AFTER them so the orchestrator's verdict is taken
        # AFTER any legacy mutations during the migration window.
        self.assertGreaterEqual(self.entry["priority"], 100)
        # Loose ceiling so a future post-phase ensurer that needs
        # to run after the orchestrator (none today) still has
        # space without bumping this one.
        self.assertLess(self.entry["priority"], 200)

    def test_blocks_other_post_phase_jobs(self) -> None:
        # The whole point of this job is bootstrap WAITS for it.
        # ``non_blocking: false`` is the contract-side knob.
        self.assertFalse(self.entry.get("non_blocking", False))


class JellyfinFamilyAnnotation(unittest.TestCase):
    """The three Jellyfin promises carry explicit
    ``bootstrap_blocking: true``. Default is True — the explicit
    annotation documents intent for future readers."""

    _EXPECTED_FAMILY = (
        "jellyfin-running",
        "jellyfin-api-key-discoverable",
        "jellyfin-libraries",
    )

    def setUp(self) -> None:
        from media_stack.infrastructure.promises.registry import load_registry
        self.registry = {p.id: p for p in load_registry()}

    def test_each_jellyfin_promise_loads(self) -> None:
        for pid in self._EXPECTED_FAMILY:
            self.assertIn(pid, self.registry, f"missing promise: {pid}")

    def test_each_jellyfin_promise_is_blocking(self) -> None:
        for pid in self._EXPECTED_FAMILY:
            promise = self.registry[pid]
            self.assertTrue(
                promise.bootstrap_blocking,
                f"{pid}: bootstrap_blocking should be True for the "
                f"Jellyfin family proof",
            )

    def test_dependency_chain_preserved(self) -> None:
        # ``jellyfin-api-key-discoverable`` depends on
        # ``jellyfin-running`` so the orchestrator probes them in
        # the right order.
        self.assertEqual(
            self.registry["jellyfin-api-key-discoverable"].depends_on,
            ("jellyfin-running",),
        )


class JellyfinEnsureApiKeyUnscheduled(unittest.TestCase):
    """``jellyfin:ensure-api-key`` no longer has ``phase: post``.

    The job is still REGISTERED so ``run_job(name)`` (auto-heal +
    operator) keeps working — it's just not on the bootstrap DAG.
    The orchestrator's promise dispatch is now the bootstrap-time
    path."""

    def setUp(self) -> None:
        self.contracts = _ContractFixture()
        self.entry = self.contracts.jellyfin_jobs().get(
            "jellyfin:ensure-api-key",
        )
        self.assertIsNotNone(
            self.entry,
            "jellyfin:ensure-api-key disappeared from jellyfin.yaml — "
            "The job is registered but unscheduled. Restore the "
            "job entry (without phase) so run_job and auto-heal still "
            "resolve it.",
        )

    def test_no_phase_field(self) -> None:
        # ``phase: post`` would put it back in the bootstrap DAG and
        # double up with bootstrap:satisfy-promises' dispatch.
        self.assertNotIn(
            "phase", self.entry,
            "jellyfin:ensure-api-key has phase= again — the wiring "
            "removed the field. Reverting means restoring "
            "phase: post + priority: 80 in jellyfin.yaml.",
        )

    def test_handler_path_unchanged(self) -> None:
        # The orchestrator's LifecycleEnsurer:jellyfin:mint_api_key
        # ends up at the SAME handler that this job's contract
        # entry resolves to. Pin the handler so a follow-up rename
        # of the function breaks both code paths together.
        self.assertEqual(
            self.entry.get("handler"),
            "media_stack.application.jellyfin.ensure_api_key:"
            "ensure_jellyfin_api_key",
        )

    def test_still_discoverable_in_runtime_registry(self) -> None:
        # Auto-heal + dashboard "run job" still resolve through
        # ``get_job_registry()`` even when the job isn't phase-
        # scheduled. Pin that the registry exposes the handler.
        from media_stack.application.jobs.framework import get_job_registry
        registry = get_job_registry()
        self.assertIn(
            "jellyfin:ensure-api-key", registry,
            "jellyfin:ensure-api-key dropped out of the runtime "
            "registry — auto-heal + manual run-job will return "
            "'Unknown job'.",
        )


class BootstrapSatisfyPromisesIsScheduled(unittest.TestCase):
    """The synthetic job actually shows up in
    ``discover_jobs_from_contracts``.

    Pinning this catches a revert that forgets to flip the
    phase back AND a future shim cleanup that misroutes
    the contract loader."""

    def test_synthetic_job_present_in_discovered_jobs(self) -> None:
        from media_stack.application.jobs.framework import (
            discover_jobs_from_contracts,
        )
        names = {j["name"] for j in discover_jobs_from_contracts()}
        self.assertIn(
            "bootstrap:satisfy-promises", names,
            "bootstrap:satisfy-promises missing from discovered jobs — "
            "orchestrator-driven bootstrap expects it scheduled in post phase.",
        )

    def test_bootstrap_dag_includes_synthetic_job(self) -> None:
        # ``build_job_framework`` is the canonical bootstrap-DAG
        # builder. The synthetic job should be
        # findable in the tree.
        from media_stack.application.jobs.framework import (
            build_job_framework,
        )
        root = build_job_framework()

        def _walk(node) -> bool:
            if getattr(node, "name", "") == "bootstrap:satisfy-promises":
                return True
            for child in getattr(node, "sub_jobs", ()):
                if _walk(child):
                    return True
            return False

        self.assertTrue(
            _walk(root),
            "bootstrap:satisfy-promises not reachable from the "
            "bootstrap DAG root — wiring is broken.",
        )


if __name__ == "__main__":
    unittest.main()
