"""Pin the promise-driven wiring of the Maintainerr rules-linked-to-arr
cutover (ADR-0005 Phase 3 — wide-handler delegation).

The single ``maintainerr-rules-linked-to-arr`` promise flipped from
string-typed ``ensured_by: configure-collections`` (a misnomer — that
string named the Jellyfin auto-collections job, unrelated to
Maintainerr's ``radarrSettingsId``/``sonarrSettingsId`` linkage) to
``{type: lifecycle, service: maintainerr, method: ensure_rules_linked_to_arr}``.
Its probe flipped from ``http_json`` to lifecycle-typed too. The
Jellyfin ``configure-collections`` job in ``jellyfin.yaml`` lost its
``phase: media_server`` line so the bootstrap DAG no longer schedules
it directly — the orchestrator dispatches the real handler
(``ensure_maintainerr_integrations``) via the promise registry instead.

This ratchet pins the contract-level shape so a future contract edit
can't silently undo it.

Sections:
  * PromiseUsesLifecycleDispatch — probe + ensurer are LifecycleProbe
    + LifecycleEnsurer with the correct service + method names.
  * PromiseIsBlocking — explicit ``bootstrap_blocking: true``
    survived the move (proof-of-pattern convention).
  * LegacyJobUnscheduled — ``configure-collections`` is still
    REGISTERED in jellyfin.yaml (handler + label + requires) but has
    NO ``phase`` field, so ``discover_jobs_from_contracts`` skips it
    from the bootstrap DAG.
  * LegacyJobStillResolvable — the registered handler resolves
    cleanly so ``run_job(name)`` and the auto-heal cycle keep working
    even though the bootstrap loader never picks it up. Note the
    handler points at the JELLYFIN auto-collections op
    (``ensure_jellyfin_auto_collections_config``), NOT the
    Maintainerr integrations op the lifecycle ensurer wide-handler-
    delegates to — they're different code paths sharing only the
    historical name collision the cutover untangles.
"""

from __future__ import annotations

import unittest
from pathlib import Path

import yaml


_REPO_ROOT = Path(__file__).resolve().parents[3]


class _ContractFixture:
    """Loads the relevant contract YAMLs once per test class."""

    def __init__(self) -> None:
        self._jellyfin = yaml.safe_load(
            (_REPO_ROOT / "contracts" / "services" / "jellyfin.yaml")
            .read_text(encoding="utf-8")
        )

    def jellyfin_jobs(self) -> dict:
        return (self._jellyfin.get("plugin") or {}).get("jobs") or {}


class _LoadedRegistry:
    """One-shot loader fixture cache."""

    _cache = None

    @classmethod
    def get(cls):
        if cls._cache is None:
            from media_stack.infrastructure.promises.registry import (
                PromiseRegistryLoader,
            )
            cls._cache = PromiseRegistryLoader().aggregate()
        return cls._cache


_PROMISE_ID = "maintainerr-rules-linked-to-arr"
_EXPECTED_SERVICE = "maintainerr"
_EXPECTED_PROBE_METHOD = "probe_rules_linked_to_arr"
_EXPECTED_ENSURER_METHOD = "ensure_rules_linked_to_arr"
_LEGACY_JOB_NAME = "configure-collections"
_LEGACY_JOB_HANDLER = (
    "media_stack.services.apps.jellyfin.runtime_ops:"
    "ensure_jellyfin_auto_collections_config"
)


class PromiseUsesLifecycleDispatch(unittest.TestCase):
    """The promise's probe + ensurer are LifecycleProbe +
    LifecycleEnsurer pointing at MaintainerrLifecycle methods.
    Reverting to the legacy ``http_json`` probe + string
    ``ensured_by: configure-collections`` flips every assertion here."""

    def setUp(self) -> None:
        self.by_id = _LoadedRegistry.get().by_id()

    def test_promise_probe_is_lifecycle(self) -> None:
        from media_stack.domain.services.promises import LifecycleProbe
        promise = self.by_id.get(_PROMISE_ID)
        self.assertIsNotNone(
            promise, f"{_PROMISE_ID!r} dropped out of registry",
        )
        self.assertIsInstance(
            promise.probe, LifecycleProbe,
            f"{_PROMISE_ID}: probe regressed from lifecycle dispatch "
            f"(got {type(promise.probe).__name__})",
        )
        self.assertEqual(
            promise.probe.service, _EXPECTED_SERVICE,
            f"{_PROMISE_ID}: probe.service expected "
            f"{_EXPECTED_SERVICE!r}",
        )
        self.assertEqual(
            promise.probe.method, _EXPECTED_PROBE_METHOD,
            f"{_PROMISE_ID}: probe.method expected "
            f"{_EXPECTED_PROBE_METHOD!r}",
        )

    def test_promise_ensurer_is_job(self) -> None:
        """ADR-0010 Phase 7 — promise→Job migration. The ensurer is
        a JobEnsurer pointing at ``maintainerr:ensure-rules-linked-to-arr``
        whose handler binds to ``MaintainerrLifecycle.ensure_rules_linked_to_arr``
        via the shared ``LifecycleHandlerAdapter``."""
        from media_stack.domain.services.promises import JobEnsurer
        promise = self.by_id[_PROMISE_ID]
        self.assertIsInstance(
            promise.ensurer, JobEnsurer,
            f"{_PROMISE_ID}: ensurer regressed from Job dispatch "
            f"(got {type(promise.ensurer).__name__})",
        )
        self.assertEqual(
            promise.ensurer.job_name,
            "maintainerr:ensure-rules-linked-to-arr",
            f"{_PROMISE_ID}: ensurer.job_name should target the "
            f"contract Job entry that wraps the lifecycle method.",
        )


class PromiseIsBlocking(unittest.TestCase):
    """Explicit ``bootstrap_blocking: true`` annotation survives the
    cutover. The Jellyfin Phase 2 family proof set the
    explicit-on-cutover-proofs convention; this Phase 3 cutover
    follows it."""

    def setUp(self) -> None:
        self.by_id = _LoadedRegistry.get().by_id()

    def test_promise_is_blocking(self) -> None:
        promise = self.by_id[_PROMISE_ID]
        self.assertTrue(
            promise.bootstrap_blocking,
            f"{_PROMISE_ID}: bootstrap_blocking flipped to False — "
            f"the cutover proof requires explicit-True so "
            f"orchestrator-driven bootstrap waits for it.",
        )


class LegacyJobUnscheduled(unittest.TestCase):
    """``configure-collections`` no longer has ``phase: media_server``
    in jellyfin.yaml. The job is still REGISTERED so ``run_job(name)``
    (auto-heal + operator + Jellyfin auto-collections plugin reconcile)
    keeps resolving it; the bootstrap loader skips it because ``phase``
    is absent."""

    def setUp(self) -> None:
        self.contracts = _ContractFixture()
        self.entry = self.contracts.jellyfin_jobs().get(_LEGACY_JOB_NAME)
        self.assertIsNotNone(
            self.entry,
            f"{_LEGACY_JOB_NAME} disappeared from jellyfin.yaml — "
            "the cutover keeps it registered, just unscheduled. "
            "Restore the entry (without phase) so run_job + auto-heal "
            "still resolve it.",
        )

    def test_no_phase_field(self) -> None:
        # ``phase: media_server`` would put the job back in the
        # bootstrap DAG and double up with the orchestrator's
        # lifecycle dispatch via the maintainerr-rules-linked-to-arr
        # promise.
        self.assertNotIn(
            "phase", self.entry,
            f"{_LEGACY_JOB_NAME} has phase= again — the cutover "
            "removed it. Reverting means restoring "
            "phase: media_server + priority: 60 in jellyfin.yaml AND "
            "flipping the maintainerr-rules-linked-to-arr promise "
            "back to http_json + string ``ensured_by: configure-collections``.",
        )

    def test_no_priority_field(self) -> None:
        self.assertNotIn(
            "priority", self.entry,
            f"{_LEGACY_JOB_NAME} has priority= again — the cutover "
            "removed it along with phase. Reverting means restoring "
            "both fields.",
        )

    def test_handler_path_unchanged(self) -> None:
        # The orchestrator's
        # LifecycleEnsurer:maintainerr:ensure_rules_linked_to_arr is
        # implemented in MaintainerrLifecycle and wide-handler-
        # delegates to ``ensure_maintainerr_integrations``. The
        # Jellyfin job's handler MUST stay so run_job + auto-heal +
        # the Jellyfin auto-collections plugin reconcile keep working
        # — note that's a DIFFERENT handler than the one the lifecycle
        # ensurer reaches (the legacy ``configure-collections`` →
        # ``ensure_jellyfin_auto_collections_config`` collision is the
        # exact misnomer the cutover documents and untangles).
        self.assertEqual(
            self.entry.get("handler"),
            _LEGACY_JOB_HANDLER,
        )


class LegacyJobStillResolvable(unittest.TestCase):
    """The job entry's handler imports cleanly. Auto-heal and
    operator-dashboard ``run_job`` still resolve through
    ``get_job_registry()`` even when the bootstrap loader skips the
    job."""

    def test_handler_imports(self) -> None:
        import importlib
        mod = importlib.import_module(
            "media_stack.services.apps.jellyfin.runtime_ops",
        )
        self.assertTrue(
            hasattr(mod, "ensure_jellyfin_auto_collections_config"),
            "ensure_jellyfin_auto_collections_config dropped from "
            "jellyfin.runtime_ops — breaks the legacy run_job path "
            "for the Jellyfin auto-collections plugin reconcile. "
            "(Note this is a DIFFERENT handler than the one the "
            "lifecycle ensurer reaches; see this file's docstring.)",
        )

    def test_lifecycle_ensurer_target_imports(self) -> None:
        # The lifecycle ensurer wide-handler-delegates to
        # ``ensure_maintainerr_integrations`` — that target must also
        # stay importable so the cutover's ensurer path doesn't break
        # at run time.
        import importlib
        mod = importlib.import_module(
            "media_stack.services.apps.maintainerr.runtime_ops",
        )
        self.assertTrue(
            hasattr(mod, "ensure_maintainerr_integrations"),
            "ensure_maintainerr_integrations dropped from "
            "maintainerr.runtime_ops — breaks "
            "MaintainerrLifecycle.ensure_rules_linked_to_arr (the "
            "wide-handler delegation target).",
        )


if __name__ == "__main__":
    unittest.main()
