"""ADR-0005 Phase 5b.5 invariant — every promise's ``ensured_by`` is
either lifecycle-typed (a dict with ``type: lifecycle``) OR points
at a top-level operational job (the small allowlist below). The
legacy ``ensure-*`` registration shells were retired in 5b.5; their
re-introduction (or any new ``ensured_by: <legacy-name>`` reference)
fails this ratchet.

Why this lives separately from
``test_promises_registry.py::test_no_orphan_ensure_jobs``:

  * The orphan-check there asks "is every ``ensure-*`` job referenced
    by SOME promise?" — but its allowlist
    (``_ORCHESTRATOR_LIFECYCLE_DISPATCHED``) means a regression that
    re-adds ``ensured_by: ensure-foo`` to a promise still passes.
  * This ratchet inverts the question: "is every promise's
    ``ensured_by`` either typed-lifecycle or a known top-level job?"
    A new string ``ensured_by`` referencing a stub that was
    re-registered now fails here even if the orphan check still
    passes.

Sections:
  * EnsuredByVocabularyAllowlist — the canonical set of legal
    string-typed ``ensured_by`` values. Lifecycle-dispatched
    promises don't appear here (they use the dict form).
  * NoStringEnsureEnsuredBy — the actual ratchet: walk every YAML
    under ``contracts/services/``, parse each promise, and assert
    its ``ensured_by`` is either a dict with ``type: lifecycle`` or
    a string in the allowlist.
"""

from __future__ import annotations

import unittest
from pathlib import Path

import yaml


_REPO_ROOT = Path(__file__).resolve().parents[3]
_SERVICES_DIR = _REPO_ROOT / "contracts" / "services"


# Top-level operational jobs that are still legitimately referenced
# from a promise's string ``ensured_by``. These are NOT ensurer
# stubs — each one carries a real ``phase`` + ``priority`` and is
# scheduled by the bootstrap loader. They predate the lifecycle-
# dispatch model and remain a valid reference.
#
# Adding a name here means: "this is a top-level operational job,
# scheduled by the bootstrap loader, that a promise legitimately
# probes against." It is NOT a back-door for re-introducing the
# legacy ``ensure-*`` registration shells.
_ALLOWED_TOP_LEVEL_JOBS = frozenset({
    "envoy-config",
    "discover-indexers",
    "tag-indexers-for-apps",
    "unpackerr-post",
    "push-indexers",
})


# Infrastructure-layer ``ensured_by`` values — valid only on
# k8s-tagged promises (or on the cross-platform
# ``seed-runtime-overrides`` bootstrap job). Pre-existing vocabulary
# from ``test_promises_registry.py``; mirrored here so this ratchet
# doesn't false-positive on k8s-only manifests.
_INFRA_ENSURED_BY = frozenset({
    "kubectl-apply", "operator", "seed-runtime-overrides",
})


# Dict-typed ensurer schema vocabulary. ``lifecycle`` was the legacy
# typed dispatch (ADR-0005 Phase 3); ``job`` is the ADR-0010 Phase 7
# replacement (orchestrator routes via ``run_job(<name>)``); ``deploy``
# is the externally-ensured marker (service-running promises that
# the deploy tooling brings up — the orchestrator just probes).
# This ratchet's regression class is string-typed
# ``ensured_by: ensure-*`` re-introductions, not the typed forms.
_ALLOWED_DICT_TYPES = frozenset({"lifecycle", "job", "deploy"})


class EnsuredByVocabularyAllowlist(unittest.TestCase):
    """Sanity: the allowlist contains only known top-level operational
    job names. Adding a name that doesn't actually exist as a job
    entry in any contract YAML is a typo waiting to happen — pin it
    here so the typo surfaces at ratchet time, not at runtime.
    """

    def test_each_allowlisted_job_exists_as_top_level_entry(self) -> None:
        all_jobs: dict[str, dict] = {}
        for yml in sorted(_SERVICES_DIR.glob("*.yaml")):
            if yml.name.startswith("_"):
                continue
            doc = yaml.safe_load(yml.read_text(encoding="utf-8")) or {}
            plugin = (doc.get("plugin") or {})
            jobs = plugin.get("jobs") or {}
            if not isinstance(jobs, dict):
                continue
            for job_name, job_def in jobs.items():
                if isinstance(job_def, dict):
                    all_jobs[job_name] = job_def
        missing = sorted(_ALLOWED_TOP_LEVEL_JOBS - set(all_jobs))
        self.assertFalse(
            missing,
            "Top-level-jobs allowlist references jobs that don't "
            "exist in any contract YAML — likely a typo or a job "
            "that was renamed without updating this ratchet:\n"
            + "\n".join(f"  - {name}" for name in missing),
        )


class NoStringEnsureEnsuredBy(unittest.TestCase):
    """Walk every promise across ``contracts/services/*.yaml`` and
    assert ``ensured_by`` is one of:

      * a dict with ``type: lifecycle`` (typed lifecycle dispatch,
        per ADR-0003 Phase 4a + ADR-0005)
      * a string in ``_ALLOWED_TOP_LEVEL_JOBS`` (top-level
        operational job)
      * a string in ``_INFRA_ENSURED_BY`` (k8s infra-layer or the
        cross-platform seed bootstrap)

    Anything else fails — most commonly a regression that re-adds
    a string ``ensured_by: ensure-*`` reference after the Phase 5b.5
    cutover retired the registration shells. The fix is to flip the
    promise to ``{type: lifecycle, service: X, method: Y}`` (and add
    the lifecycle method on ``XLifecycle`` if it doesn't exist).
    """

    def _walk_promises(self):
        """Yield ``(yaml_path, promise_dict)`` for every promise
        across every service YAML. ``contracts/services/_*.yaml`` is
        a template / fragment file, not a service contract — skip."""
        for yml in sorted(_SERVICES_DIR.glob("*.yaml")):
            if yml.name.startswith("_"):
                continue
            doc = yaml.safe_load(yml.read_text(encoding="utf-8")) or {}
            plugin = (doc.get("plugin") or {})
            promises = plugin.get("promises") or []
            if not isinstance(promises, list):
                continue
            for promise in promises:
                if isinstance(promise, dict):
                    yield yml, promise

    def test_every_promise_uses_lifecycle_or_allowlisted_top_level(
        self,
    ) -> None:
        bad: list[str] = []
        canonical_fix = (
            "flip to ``{type: lifecycle, service: <X>, method: <Y>}`` "
            "(and add the lifecycle method on ``XLifecycle`` if it "
            "doesn't exist). String references to legacy ``ensure-*`` "
            "stubs are retired (ADR-0005 Phase 5b.5)."
        )
        for yml, promise in self._walk_promises():
            pid = promise.get("id") or "<no-id>"
            ensured = promise.get("ensured_by")

            if isinstance(ensured, dict):
                kind = ensured.get("type")
                if kind not in _ALLOWED_DICT_TYPES:
                    bad.append(
                        f"  {yml.name}::{pid}: ensured_by is a dict "
                        f"of type {kind!r} — only "
                        f"{sorted(_ALLOWED_DICT_TYPES)} are permitted "
                        f"at this layer. {canonical_fix}"
                    )
                continue

            if isinstance(ensured, str):
                if ensured in _ALLOWED_TOP_LEVEL_JOBS:
                    continue
                if ensured in _INFRA_ENSURED_BY:
                    continue
                bad.append(
                    f"  {yml.name}::{pid}: ensured_by={ensured!r} is "
                    "neither a typed lifecycle dispatch nor an "
                    "allowlisted top-level operational job "
                    f"{sorted(_ALLOWED_TOP_LEVEL_JOBS)}. "
                    f"{canonical_fix}"
                )
                continue

            bad.append(
                f"  {yml.name}::{pid}: ensured_by has unsupported "
                f"shape {type(ensured).__name__} (value={ensured!r}). "
                f"{canonical_fix}"
            )

        self.assertFalse(
            bad,
            "Promises with non-allowlisted ``ensured_by`` shapes:\n"
            + "\n".join(bad),
        )


if __name__ == "__main__":
    unittest.main()
