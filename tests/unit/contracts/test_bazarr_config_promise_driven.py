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
  * LegacyJobRetired — ``ensure-bazarr-language-profile`` is GONE
    from core.yaml as of ADR-0005 Phase 5b.5; the orchestrator's
    lifecycle dispatch is the only path.
  * LegacyHandlerStillImportable — the underlying handler imports
    cleanly so the orchestrator's ``ensure_config_wiring`` wide-
    handler delegate keeps reaching the same form-encoded POST +
    file write code path.
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

    def test_each_promise_ensurer_is_shared_job(self) -> None:
        """ADR-0010 Phase 7 — all five promises point at the same
        Job (``bazarr:ensure-config-wiring``). The Job's handler
        binds to ``BazarrLifecycle.ensure_config_wiring``; splitting
        into per-promise Jobs would mean five redundant POSTs that
        each clobber the shared settings document."""
        from media_stack.domain.services.promises import JobEnsurer
        expected_job = "bazarr:ensure-config-wiring"
        for pid, _expected_probe in _EXPECTED_PROBE_METHODS:
            promise = self.by_id[pid]
            self.assertIsInstance(
                promise.ensurer, JobEnsurer,
                f"{pid}: ensurer regressed from Job dispatch "
                f"(got {type(promise.ensurer).__name__})",
            )
            self.assertEqual(
                promise.ensurer.job_name, expected_job,
                f"{pid}: ensurer.job_name expected {expected_job!r} — "
                "all five Bazarr promises share a single Job because "
                "the underlying handler does profile + toggles + "
                "providers + arr-integration + plugin-XML in one "
                "round-trip.",
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


class LegacyJobRetired(unittest.TestCase):
    """``ensure-bazarr-language-profile`` is GONE from core.yaml as
    of ADR-0005 Phase 5b.5. The orchestrator's lifecycle dispatch
    via the five Bazarr promises is the only path; auto-heal and
    the operator dashboard route through the orchestrator too."""

    def setUp(self) -> None:
        self.contracts = _ContractFixture()

    def test_legacy_registration_is_gone(self) -> None:
        self.assertNotIn(
            "ensure-bazarr-language-profile",
            self.contracts.core_jobs(),
            "ensure-bazarr-language-profile reappeared in core.yaml "
            "— ADR-0005 Phase 5b.5 retired the registration shell. "
            "Reverting means restoring the entry (with phase: post + "
            "priority: 91) AND flipping every Bazarr promise back "
            "to http_json/file_text + string ensured_by.",
        )


class LegacyHandlerStillImportable(unittest.TestCase):
    """The underlying ``ensure_bazarr_language_profile`` handler
    stays importable because the orchestrator's
    ``BazarrLifecycle.ensure_config_wiring`` wide-handler-delegates
    back to it via injected callables (the legacy handler does
    profile + toggles + providers + arr-integration + plugin-XML in
    one round-trip)."""

    def test_handler_imports(self) -> None:
        import importlib
        mod = importlib.import_module(
            "media_stack.services.apps.core.job_adapters",
        )
        self.assertTrue(
            hasattr(mod, "ensure_bazarr_language_profile"),
            "ensure_bazarr_language_profile dropped from job_adapters "
            "— breaks the orchestrator's lifecycle method (which "
            "wide-handler-delegates back to the legacy handler).",
        )


if __name__ == "__main__":
    unittest.main()
