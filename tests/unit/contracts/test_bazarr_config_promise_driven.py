"""Pin the promise-driven wiring of the Bazarr-config family cutover
(ADR-0005 Phase 3 — Bazarr promises).

Five promises (bazarr-language-profile, bazarr-default-profile-toggles,
bazarr-providers, bazarr-arr-integration, bazarr-jellyfin-plugin-config)
all flipped from string-typed ``ensured_by:
ensure-bazarr-language-profile`` to ``{type: lifecycle, service:
bazarr, method: ensure_config_wiring}``. Their probes flipped from
``http_json`` / ``file_text`` to lifecycle-typed too — each pointing
at its own per-promise probe method on ``BazarrLifecycle``. The
legacy ``ensure-bazarr-language-profile`` job in ``core.yaml`` lost
its ``phase: post`` line so the bootstrap loader stops scheduling it
directly — the orchestrator dispatches via the promise registry
instead.

This ratchet pins the contract-level shape so a future contract edit
can't silently undo it.

Sections:
  * EachPromiseUsesLifecycleDispatch — probe + ensurer are
    LifecycleProbe + LifecycleEnsurer with the correct service +
    method names. Per-promise probe methods differ; all five share
    ``ensure_config_wiring`` (rationale: the legacy handler does all
    five things in one form-encoded POST + one file write — five
    separate ensurers would mean five redundant POSTs that each
    clobber the shared settings document).
  * EachPromiseIsBlocking — explicit ``bootstrap_blocking: true``
    survived the move (proof-of-pattern convention).
  * LegacyJobUnscheduled — ``ensure-bazarr-language-profile`` is
    still REGISTERED in core.yaml (handler + label + requires) but
    has NO ``phase`` field, so ``discover_jobs_from_contracts`` skips
    it from the bootstrap DAG.
  * LegacyJobStillResolvable — the registered handler resolves
    cleanly so ``run_job(name)`` and the auto-heal cycle keep working
    even though the bootstrap loader never picks it up.
"""

from __future__ import annotations

import unittest
from pathlib import Path

import yaml


_REPO_ROOT = Path(__file__).resolve().parents[3]


class _ContractFixture:
    """Loads the relevant contract YAMLs once per test class."""

    def __init__(self) -> None:
        self._core = yaml.safe_load(
            (_REPO_ROOT / "contracts" / "services" / "core.yaml")
            .read_text(encoding="utf-8")
        )

    def core_jobs(self) -> dict:
        return (self._core.get("plugin") or {}).get("jobs") or {}


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


# Per-promise probe-method map. All five share ``ensure_config_wiring``.
_EXPECTED_PROBE_METHODS = (
    ("bazarr-language-profile",         "probe_language_profile"),
    ("bazarr-default-profile-toggles",  "probe_default_profile_toggles"),
    ("bazarr-providers",                "probe_providers"),
    ("bazarr-arr-integration",          "probe_arr_integration"),
    ("bazarr-jellyfin-plugin-config",   "probe_jellyfin_plugin_config"),
)
_SHARED_ENSURER_METHOD = "ensure_config_wiring"
_EXPECTED_SERVICE = "bazarr"


class EachPromiseUsesLifecycleDispatch(unittest.TestCase):
    """Each Bazarr-config promise's probe + ensurer are LifecycleProbe
    + LifecycleEnsurer pointing at the right BazarrLifecycle methods.
    Reverting to the legacy ``http_json``/``file_text`` probe + string
    ``ensured_by: ensure-bazarr-language-profile`` flips every
    assertion here."""

    def setUp(self) -> None:
        self.by_id = _LoadedRegistry.get().by_id()

    def test_each_promise_probe_is_lifecycle(self) -> None:
        from media_stack.domain.services.promises import LifecycleProbe
        for pid, expected_method in _EXPECTED_PROBE_METHODS:
            promise = self.by_id.get(pid)
            self.assertIsNotNone(
                promise, f"{pid!r} dropped out of registry",
            )
            self.assertIsInstance(
                promise.probe, LifecycleProbe,
                f"{pid}: probe regressed from lifecycle dispatch "
                f"(got {type(promise.probe).__name__})",
            )
            self.assertEqual(
                promise.probe.service, _EXPECTED_SERVICE,
                f"{pid}: probe.service expected {_EXPECTED_SERVICE!r}",
            )
            self.assertEqual(
                promise.probe.method, expected_method,
                f"{pid}: probe.method expected {expected_method!r}",
            )

    def test_each_promise_ensurer_is_lifecycle_and_shared(self) -> None:
        """All five promises point at the same ensurer method
        (``ensure_config_wiring``). Splitting into per-promise ensurers
        would mean five redundant POSTs that each clobber the shared
        settings document."""
        from media_stack.domain.services.promises import LifecycleEnsurer
        for pid, _expected_probe in _EXPECTED_PROBE_METHODS:
            promise = self.by_id[pid]
            self.assertIsInstance(
                promise.ensurer, LifecycleEnsurer,
                f"{pid}: ensurer regressed from lifecycle dispatch "
                f"(got {type(promise.ensurer).__name__})",
            )
            self.assertEqual(
                promise.ensurer.service, _EXPECTED_SERVICE,
                f"{pid}: ensurer.service expected {_EXPECTED_SERVICE!r}",
            )
            self.assertEqual(
                promise.ensurer.method, _SHARED_ENSURER_METHOD,
                f"{pid}: ensurer.method expected "
                f"{_SHARED_ENSURER_METHOD!r}. All five Bazarr promises "
                "intentionally share one ensurer — the legacy handler "
                "does profile + toggles + providers + arr-integration "
                "+ plugin-XML in one round-trip; per-promise ensurers "
                "would clobber the shared settings document.",
            )


class EachPromiseIsBlocking(unittest.TestCase):
    """Explicit ``bootstrap_blocking: true`` annotation survives the
    cutover. The Jellyfin Phase 2 family proof set the
    explicit-on-cutover-proofs convention; this Phase 3 family
    follows it."""

    def setUp(self) -> None:
        self.by_id = _LoadedRegistry.get().by_id()

    def test_each_promise_is_blocking(self) -> None:
        for pid, _ in _EXPECTED_PROBE_METHODS:
            promise = self.by_id[pid]
            self.assertTrue(
                promise.bootstrap_blocking,
                f"{pid}: bootstrap_blocking flipped to False — "
                f"the cutover proof requires explicit-True so "
                f"orchestrator-driven bootstrap waits for it.",
            )


class LegacyJobUnscheduled(unittest.TestCase):
    """``ensure-bazarr-language-profile`` no longer has ``phase: post``
    in core.yaml. The job is still REGISTERED so ``run_job(name)``
    (auto-heal + operator) keeps resolving it; the bootstrap loader
    skips it because ``phase`` is absent."""

    def setUp(self) -> None:
        self.contracts = _ContractFixture()
        self.entry = self.contracts.core_jobs().get(
            "ensure-bazarr-language-profile",
        )
        self.assertIsNotNone(
            self.entry,
            "ensure-bazarr-language-profile disappeared from core.yaml — "
            "the cutover keeps it registered, just unscheduled. "
            "Restore the entry (without phase) so run_job + auto-heal "
            "still resolve it.",
        )

    def test_no_phase_field(self) -> None:
        # ``phase: post`` would put the job back in the bootstrap DAG
        # and double up with the orchestrator's lifecycle dispatch via
        # the five Bazarr promises.
        self.assertNotIn(
            "phase", self.entry,
            "ensure-bazarr-language-profile has phase= again — the "
            "cutover removed it. Reverting means restoring "
            "phase: post + priority: 91 in core.yaml AND flipping "
            "every Bazarr promise back to http_json/file_text + "
            "string ensured_by.",
        )

    def test_handler_path_unchanged(self) -> None:
        # The orchestrator's
        # LifecycleEnsurer:bazarr:ensure_config_wiring is implemented
        # in BazarrLifecycle, but the legacy job's handler MUST stay
        # so run_job + auto-heal keep working.
        self.assertEqual(
            self.entry.get("handler"),
            "media_stack.services.apps.core.job_adapters:"
            "ensure_bazarr_language_profile",
        )


class LegacyJobStillResolvable(unittest.TestCase):
    """The job entry's handler imports cleanly. Auto-heal and
    operator-dashboard ``run_job`` still resolve through
    ``get_job_registry()`` even when the bootstrap loader skips the
    job."""

    def test_handler_imports(self) -> None:
        import importlib
        mod = importlib.import_module(
            "media_stack.services.apps.core.job_adapters",
        )
        self.assertTrue(
            hasattr(mod, "ensure_bazarr_language_profile"),
            "ensure_bazarr_language_profile dropped from job_adapters "
            "— breaks both legacy run_job AND the orchestrator's "
            "lifecycle method (which the legacy handler is the "
            "reference implementation for).",
        )


if __name__ == "__main__":
    unittest.main()
