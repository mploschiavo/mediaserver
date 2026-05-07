"""L33 — meta-ratchet for the post-install promises registry.

The registry (``contracts/promises/promises.yaml``) is the source of truth for
"what works out-of-the-box after a fresh install." This ratchet keeps
the registry consistent with the rest of the codebase:

  - Every promise declares ``platforms:`` (compose, k8s, or both).
  - Every promise's ``ensured_by`` is either a real contract job
    handler OR a recognised infrastructure-layer vocabulary value
    for k8s-only promises (``kubectl-apply`` / ``operator``).
  - Every contract-job ``ensured_by`` resolves to an importable handler.
  - Every ``ensure-*`` job in contracts/services/*.yaml is referenced
    by at least one promise (no orphan adapters).
  - Probe shape is well-formed (supported ``type``, required fields).

This is a CATEGORY-level ratchet — it doesn't verify any single
feature, it verifies that the FRAMEWORK around features is intact.
The fresh-install acceptance script (bin/verify-fresh-install.sh)
verifies the actual runtime promises by hitting each probe.

v1.0.169: folded the separate K8s registry (``promises-k8s.yaml``) +
its own meta-ratchet back into this file. Every promise now carries
``platforms:`` and one ratchet covers both runtimes, with different
``ensured_by`` vocabulary per platform.
"""

from __future__ import annotations

import importlib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]

# Infrastructure-layer ``ensured_by`` values — valid ONLY on promises
# tagged ``platforms: [k8s]``. These describe the layer that makes the
# promise hold rather than a contract job the runner can call:
#
#   kubectl-apply           — applying the k8s manifest is what makes
#                             the promise true (the verifier doesn't
#                             apply; the deploy step does).
#   operator                — a one-time manual step documented in
#                             docs/deployment.md (e.g. patching a PV's
#                             reclaim policy).
#   seed-runtime-overrides  — the bootstrap-time job that seeds
#                             ``.controller/*-overrides.yaml`` from
#                             profile on first run (new in v1.0.169;
#                             applies on both platforms so the
#                             "clean re-deploy = same result" invariant
#                             holds).
INFRA_ENSURED_BY = {"kubectl-apply", "operator", "seed-runtime-overrides"}

# Supported probe types. K8s-specific ones only valid when the promise
# is tagged ``platforms: [k8s]``. The ``lifecycle`` type is the
# ADR-0003 Phase 4a addition; lifecycle-typed entries are validated
# by ``test_promise_dispatch_resolution_ratchet.py`` (which checks
# the resolved class + method actually exist), so this legacy ratchet
# accepts the type literal but skips the legacy ``assert``-expression
# requirement for it.
COMPOSE_PROBE_TYPES = {"http_json", "http_text", "http_status",
                       "file_json", "file_text"}
K8S_PROBE_TYPES = {"k8s_resource", "k8s_exec"}
LIFECYCLE_PROBE_TYPES = {"lifecycle"}
ALL_PROBE_TYPES = COMPOSE_PROBE_TYPES | K8S_PROBE_TYPES | LIFECYCLE_PROBE_TYPES

# Allowed kinds for k8s_resource probes.
ALLOWED_K8S_KINDS = {
    "pvc", "pv", "pod", "deployment", "service", "ingress", "secret",
    "configmap", "statefulset", "daemonset", "job", "cronjob",
}


def _load_promises() -> dict:
    """Return the aggregate promise registry as a YAML-shaped dict.

    ADR-0006 Phase 2 split per-service promises out of the monolithic
    ``contracts/promises/promises.yaml`` into
    ``contracts/services/<svc>.yaml::plugin.promises``. This loader
    used to read only the legacy file — every Jellyfin family ratchet
    started failing once those entries moved. Use the production
    aggregator (which walks both sources + validates cross-file
    invariants) and rebuild the legacy YAML shape from each
    ``Promise.to_dict()`` so the test logic stays unchanged.
    """
    from media_stack.infrastructure.promises.registry import (
        PromiseRegistryLoader,
    )
    result = PromiseRegistryLoader().aggregate()
    return {
        "promises": [_promise_to_yaml_entry(p) for p in result.promises],
    }


def _promise_to_yaml_entry(promise) -> dict:
    """Reassemble a YAML-shaped promise entry from a typed
    ``Promise``. Mirrors what the legacy ``yaml.safe_load`` produced
    on the monolith, including the compact-string form for
    Job/Infra ensurers (``ensured_by: ensure-foo`` rather than the
    typed ``{type: job, job_name: ensure-foo}`` dict) — the legacy
    consistency tests inspect that flat shape."""
    out: dict = {
        "id": promise.id,
        "description": promise.description,
        "platforms": list(promise.platforms),
        "probe": promise.probe.to_dict(),
        "ensured_by": _ensurer_to_legacy_yaml(promise.ensurer),
        "bootstrap_blocking": promise.bootstrap_blocking,
    }
    if promise.depends_on:
        out["depends_on"] = list(promise.depends_on)
    return out


def _ensurer_to_legacy_yaml(ensurer):
    """Collapse Job/Infra ensurers back to bare strings (their pre-
    Phase-4a shape) so the legacy ratchets — which special-case
    string vs dict — see the same surface they did before the
    typed-loader landed. Lifecycle/Deploy stay as dicts; they were
    introduced as dict-typed in Phase 4a and the ratchets already
    skip dict ensurers."""
    payload = ensurer.to_dict()
    kind = payload.get("type")
    if kind == "job":
        return payload.get("job_name") or payload
    if kind == "infra":
        return payload.get("target") or payload
    return payload


def _load_contract_jobs() -> dict[str, dict]:
    """Return ``{job_name: {handler, phase, ...}}`` from every
    contracts/services/*.yaml file.

    Includes both ``plugin.jobs.<name>`` entries AND the singular
    ``plugin.post_setup_handler`` / ``plugin.preflight_handler``
    entries — those run through the same bootstrap pipeline.
    """
    import yaml
    out: dict[str, dict] = {}
    svc_dir = ROOT / "contracts" / "services"
    for yml in sorted(svc_dir.glob("*.yaml")):
        if yml.name.startswith("_"):
            continue
        try:
            doc = yaml.safe_load(yml.read_text(encoding="utf-8")) or {}
        except Exception:
            continue
        plugin = doc.get("plugin") or {}
        for job_name, job_def in (plugin.get("jobs") or {}).items():
            if isinstance(job_def, dict):
                out[job_name] = job_def
        for key in ("post_setup_handler", "preflight_handler"):
            entry = plugin.get(key)
            if isinstance(entry, dict) and entry.get("handler"):
                synthetic = f"{entry.get('name') or yml.stem}-{key.split('_')[0]}"
                out[synthetic] = entry
    return out


def _resolve_handler(handler_path: str):
    """Mirror of cli/commands/job_framework.py:_resolve_handler so this
    test runs without spinning up the JobRunner."""
    if not handler_path:
        return None
    if ":" in handler_path:
        mod_path, func_name = handler_path.rsplit(":", 1)
    elif "." in handler_path:
        mod_path, func_name = handler_path.rsplit(".", 1)
    else:
        return None
    try:
        mod = importlib.import_module(mod_path)
        return getattr(mod, func_name, None)
    except Exception:
        return None


class PromisesRegistryConsistent(unittest.TestCase):

    def setUp(self):
        try:
            import yaml  # noqa: F401
        except ImportError:
            self.skipTest("PyYAML not installed")
        self.registry = _load_promises()
        self.promises = self.registry.get("promises") or []
        self.jobs = _load_contract_jobs()

    def test_registry_exists_and_has_promises(self):
        self.assertTrue(
            self.registry,
            "contracts/promises/promises.yaml is missing or empty. Without it "
            "the fresh-install acceptance script has nothing to "
            "verify and the meta-ratchet can't enforce convention.",
        )
        self.assertGreater(
            len(self.promises), 0,
            "promises.yaml has no ``promises:`` entries.",
        )

    def test_every_promise_has_required_fields(self):
        required = {"id", "description", "ensured_by", "probe", "platforms"}
        bad: list[str] = []
        for p in self.promises:
            if not isinstance(p, dict):
                bad.append(f"  non-mapping promise: {p!r}")
                continue
            missing = required - set(p.keys())
            if missing:
                bad.append(f"  {p.get('id', '<no-id>')}: missing {missing}")
        self.assertFalse(
            bad,
            "Promises missing required fields:\n" + "\n".join(bad),
        )

    def test_platforms_field_is_valid(self):
        """``platforms:`` must be a non-empty list whose values come
        from ``{compose, k8s}``. Default-unspecified is not permitted —
        forcing the tag explicit means nobody has to guess whether a
        promise applies to a given runtime."""
        valid = {"compose", "k8s"}
        bad: list[str] = []
        for p in self.promises:
            platforms = p.get("platforms")
            if not isinstance(platforms, list) or not platforms:
                bad.append(
                    f"  {p.get('id')}: platforms must be a non-empty "
                    f"list of {sorted(valid)} (got {platforms!r})"
                )
                continue
            invalid = set(platforms) - valid
            if invalid:
                bad.append(
                    f"  {p.get('id')}: unknown platform tags {sorted(invalid)} "
                    f"(must be subset of {sorted(valid)})"
                )
        self.assertFalse(
            bad,
            "Platform-tag errors:\n" + "\n".join(bad),
        )

    def test_probe_shape_and_platform_consistency(self):
        """Probe ``type`` must be supported and consistent with the
        promise's ``platforms:`` — k8s-only probe types belong ONLY
        on k8s-tagged promises, never on agnostic or compose-only."""
        bad: list[str] = []
        for p in self.promises:
            pid = p.get("id")
            probe = p.get("probe") or {}
            ptype = probe.get("type")
            if ptype not in ALL_PROBE_TYPES:
                bad.append(
                    f"  {pid}: unknown probe type {ptype!r} "
                    f"(supported: {sorted(ALL_PROBE_TYPES)})"
                )
                continue
            # Lifecycle probes don't have an ``assert:`` expression —
            # the lifecycle method's ProbeResult IS the assertion.
            # Validation of service+method resolution is handled by
            # test_promise_dispatch_resolution_ratchet.py.
            if ptype in LIFECYCLE_PROBE_TYPES:
                continue
            if "assert" not in probe:
                bad.append(f"  {pid}: probe missing ``assert`` expression")
            platforms = set(p.get("platforms") or [])
            if ptype in K8S_PROBE_TYPES and platforms != {"k8s"}:
                bad.append(
                    f"  {pid}: probe type {ptype!r} is k8s-only but "
                    f"platforms={sorted(platforms)} — tag the promise "
                    "``platforms: [k8s]`` or rewrite the probe as "
                    "http_json/http_text/http_status/file_json/file_text "
                    "so it can run on both runtimes."
                )
            if ptype == "k8s_resource":
                kind = probe.get("kind") or ""
                if kind and kind not in ALLOWED_K8S_KINDS:
                    bad.append(
                        f"  {pid}: k8s_resource kind {kind!r} not in "
                        f"allowed set {sorted(ALLOWED_K8S_KINDS)}"
                    )
        self.assertFalse(
            bad,
            "Probe-shape errors:\n" + "\n".join(bad),
        )

    def test_ensured_by_matches_platform(self):
        """``ensured_by`` vocabulary:
          - contract-job name (e.g. ``ensure-bazarr-language-profile``)
              → handler must be importable. Valid on any platform.
          - infra-layer value (``kubectl-apply`` / ``operator`` /
              ``seed-runtime-overrides``) → only valid on k8s-only
              promises OR on the cross-platform seed-runtime-overrides.
        """
        bad: list[str] = []
        for p in self.promises:
            pid = p.get("id")
            ensured = p.get("ensured_by")
            platforms = set(p.get("platforms") or [])
            # ADR-0003 Phase 4a: dict-typed ensurers (lifecycle /
            # deploy / infra / job) are validated by the
            # test_promise_dispatch_resolution_ratchet.py — skip
            # them here so the legacy meta-ratchet's narrower
            # vocabulary doesn't block the new schema.
            if isinstance(ensured, dict):
                continue
            if ensured in INFRA_ENSURED_BY:
                # seed-runtime-overrides is agnostic (bootstrap seeds
                # on both platforms); kubectl-apply/operator are k8s-
                # only because they describe a cluster-layer guarantee.
                if ensured != "seed-runtime-overrides" and platforms != {"k8s"}:
                    bad.append(
                        f"  {pid}: ensured_by={ensured!r} is k8s-only "
                        "vocabulary — promise must be tagged "
                        f"platforms: [k8s] (got {sorted(platforms)})"
                    )
                continue
            # Contract-job path — must resolve.
            if ensured not in self.jobs:
                bad.append(
                    f"  {pid}: ensured_by={ensured!r} is not a known "
                    "contract job and not in the allowed infra "
                    f"vocabulary {sorted(INFRA_ENSURED_BY)}"
                )
                continue
            handler_path = str((self.jobs[ensured] or {}).get("handler") or "")
            if not handler_path:
                bad.append(f"  {pid}: contract job {ensured!r} has no handler")
                continue
            if _resolve_handler(handler_path) is None:
                bad.append(
                    f"  {pid}: handler {handler_path!r} not importable"
                )
        self.assertFalse(
            bad,
            "``ensured_by`` errors:\n" + "\n".join(bad),
        )

    # ADR-0005 Phase 3+ — jobs whose ``phase: post`` was retired
    # because the orchestrator dispatches the same code path via a
    # lifecycle ensurer. The job entry stays REGISTERED so
    # ``run_job(name)`` (auto-heal + operator dashboard) keeps
    # working, but no promise references the job by string ``ensured_by``
    # — the wiring is via ``{type: lifecycle, ...}`` instead.
    #
    # When you cut over a job, add its name here AND pin the wiring
    # in a ``test_<svc>_<job>_promise_driven.py`` ratchet so the
    # cutover can't silently regress.
    _ORCHESTRATOR_LIFECYCLE_DISPATCHED = {
        # ADR-0005 Phase 3 (proof-of-pattern, sonarr+radarr+lidarr):
        # ServarrLifecycle.ensure_jellyfin_notifier wraps the same
        # handler this job points at. See
        # tests/unit/contracts/test_servarr_jellyfin_notifier_promise_driven.py.
        "ensure-arr-jellyfin-notifier",
        # ADR-0005 Phase 3 (jellyseerr family):
        # JellyseerrLifecycle.ensure_oidc / ensure_application_url
        # wrap the legacy ``ensure-jellyseerr-oidc`` settings.json
        # mutation; ``ensure_arr_servers`` delegates back to the
        # legacy ``configure-jellyseerr`` handler. Both jobs stay
        # registered (auto-heal + operator dashboard) but the
        # bootstrap path is the orchestrator's lifecycle dispatch.
        # See tests/unit/contracts/test_jellyseerr_config_promise_driven.py.
        "ensure-jellyseerr-oidc",
        "configure-jellyseerr",
        # ADR-0005 Phase 3 (Bazarr family — five promises share one
        # ensurer because the legacy handler does all five things in
        # one form-encoded POST + one file write).
        # BazarrLifecycle.ensure_config_wiring wraps the same handler
        # this job points at. See
        # tests/unit/contracts/test_bazarr_config_promise_driven.py.
        "ensure-bazarr-language-profile",
        # ADR-0005 Phase 3 follow-on (sonarr + radarr indexer
        # pipeline): ServarrLifecycle.ensure_indexers narrows the
        # legacy whole-pipeline run to a per-*arr probe-then-
        # trigger-Prowlarr-sync flow. The legacy ``push-indexers``
        # job stays registered so ``run_job(name)`` keeps reaching
        # the heavyweight handler for full reconcile. Listed here
        # for cross-reference even though the orphan check below
        # only flags ``ensure-*`` names — future ratchet work that
        # widens the orphan check (e.g. to all jobs whose ``phase``
        # was retired) will pick it up. See
        # tests/unit/contracts/test_servarr_indexers_promise_driven.py.
        "push-indexers",
        # ADR-0005 Phase 3 (wide-handler delegation, sonarr seed
        # series): ServarrLifecycle.ensure_has_series's wirer
        # delegates back to this legacy handler via injected
        # callables — the wirer owns only the idempotent probe
        # (count series >= 5). The job stays registered so
        # ``run_job(name)`` keeps reaching the heavyweight Sonarr
        # API roundtrip + tvdbId-lookup-per-title path. See
        # tests/unit/contracts/test_servarr_seed_series_promise_driven.py.
        "ensure-sonarr-seed-series",
        # ADR-0005 Phase 3 (qBittorrent categories — single-promise,
        # session-cookie auth). ``QbittorrentLifecycle.ensure_categories``
        # delegates to ``CategoriesWirer`` (cookie-jar login + per-
        # category POST). The legacy job stays registered so
        # ``run_job(name)`` (auto-heal + operator dashboard) keeps
        # resolving it; the bootstrap loader skips it because ``phase``
        # is absent. The legacy handler is the canonical example in
        # the silent-error-as-ok bug class — the wirer surfaces login
        # failures as ``Outcome.failure(transient=True)`` so auto-heal
        # sees them. See
        # tests/unit/contracts/test_qbittorrent_categories_promise_driven.py.
        "ensure-qbittorrent-categories",
        # ADR-0005 Phase 3 (maintainerr rules-linked-to-arr — wide-
        # handler delegation): the legacy
        # ``ensured_by: configure-collections`` was a misnomer —
        # ``configure-collections`` is the Jellyfin auto-collections
        # job (lives in ``contracts/services/jellyfin.yaml``),
        # unrelated to Maintainerr's ``radarrSettingsId`` /
        # ``sonarrSettingsId`` linkage. The lifecycle ensurer
        # ``MaintainerrLifecycle.ensure_rules_linked_to_arr`` wide-
        # handler-delegates to ``ensure_maintainerr_integrations``
        # (the real linker, runs ``MaintainerrRuleSyncService.sync_policy_rules``);
        # ``configure-collections`` stays registered for its own
        # Jellyfin auto-collections purpose but no longer claims a
        # bootstrap phase. See
        # tests/unit/contracts/test_maintainerr_rules_promise_driven.py.
        "configure-collections",
        # ADR-0005 Phase 3 (runtime-defaults — three promises share
        # one ensurer because the legacy handler is monolithic: one
        # call patches every *arr's quality-profile language /
        # import-list enableAuto / SAB + delay-profile state in one
        # pass). ``ServarrLifecycle.ensure_runtime_defaults``
        # delegates back to this legacy handler via injected
        # configure_handler + job_context_factory callables (wide-
        # handler pattern from the Jellyseerr family). Three promises
        # bind: sonarr-quality-profiles + radarr-quality-profiles +
        # radarr-import-lists-auto. Listed here for cross-reference
        # even though the orphan check only flags ``ensure-*`` names
        # (this job's name doesn't match the prefix); future ratchet
        # work that widens the check picks it up. See
        # tests/unit/contracts/test_servarr_runtime_defaults_promise_driven.py.
        "apply-arr-runtime-defaults",
        # ADR-0005 Phase 5b (download-client — the deferred 9th wirer):
        # ``ServarrLifecycle.ensure_download_client`` delegates to
        # ``DownloadClientWirer`` (per-arr endpoint + upsert-by-
        # implementation match). Two promises bind:
        # sonarr-download-client (cat=tv) + radarr-download-client
        # (cat=movies). The legacy job stays REGISTERED so
        # ``run_job(name)`` (auto-heal + operator dashboard) keeps
        # resolving it; without ``phase`` the bootstrap loader skips
        # it. See
        # tests/unit/contracts/test_servarr_download_client_promise_driven.py.
        "ensure-arr-download-client",
        # ADR-0005 Phase 5b (jellyfin-libraries — the 10th and final
        # wirer): ``JellyfinLifecycle.ensure_libraries`` delegates to
        # ``JellyfinLibrariesWirer`` (GET /Library/VirtualFolders
        # readback + per-missing-library POST). The ``jellyfin-
        # libraries`` promise binds via lifecycle dispatch. Closes
        # the last string ``ensured_by: ensure-*`` snowflake in
        # contracts (5b.5 will retire the registration shell). The
        # legacy job stays REGISTERED so ``run_job(name)`` (auto-
        # heal + operator dashboard) keeps resolving it; without
        # ``phase`` the bootstrap loader skips it. See
        # tests/unit/contracts/test_jellyfin_libraries_promise_driven.py.
        "ensure-jellyfin-libraries",
    }

    def test_no_orphan_ensure_jobs(self):
        """Every ``ensure-*`` job in contracts SHOULD appear as an
        ``ensured_by`` somewhere. Orphans likely indicate the registry
        drifted away from the real bootstrap surface — UNLESS the job
        is explicitly orchestrator-lifecycle-dispatched (see the
        allowlist above)."""
        # Dict-typed ensurers (Phase 4a) aren't hashable — collect
        # only the string-typed ones for the orphan check. Dict
        # ensurers don't reference contract jobs anyway.
        referenced = {
            p.get("ensured_by") for p in self.promises
            if isinstance(p.get("ensured_by"), str)
        }
        orphans = [
            name for name in self.jobs
            if name.startswith("ensure-")
            and name not in referenced
            and name not in self._ORCHESTRATOR_LIFECYCLE_DISPATCHED
        ]
        self.assertFalse(
            orphans,
            "ensure-* jobs not referenced by any promise — either "
            "add a promise that probes them, delete the orphan job, "
            "OR (if the job is now orchestrator-lifecycle-dispatched) "
            "add it to ``_ORCHESTRATOR_LIFECYCLE_DISPATCHED`` with "
            "a pointer to the cutover ratchet:\n"
            + "\n".join(f"  - {o}" for o in sorted(orphans)),
        )

    def test_promise_ids_are_unique(self):
        ids = [p.get("id") for p in self.promises if p.get("id")]
        dupes = sorted({i for i in ids if ids.count(i) > 1})
        self.assertFalse(
            dupes,
            f"duplicate promise ids: {dupes}",
        )

    def test_namespaced_k8s_resource_probes_have_namespace(self):
        """k8s_resource probes on namespaced kinds MUST specify
        ``namespace:`` — otherwise kubectl falls back to the operator's
        current context, producing silently wrong results."""
        cluster_scoped = {"pv"}
        bad: list[str] = []
        for p in self.promises:
            probe = p.get("probe") or {}
            if probe.get("type") != "k8s_resource":
                continue
            kind = probe.get("kind") or ""
            if kind in cluster_scoped:
                continue
            if not (probe.get("namespace") or "").strip():
                bad.append(
                    f"  {p.get('id')}: namespaced k8s_resource probe "
                    "missing ``namespace`` (would inherit operator's "
                    "current context)"
                )
        self.assertFalse(
            bad,
            "Namespaced k8s_resource probes missing ``namespace``:\n"
            + "\n".join(bad),
        )

    def test_k8s_exec_probes_have_required_fields(self):
        bad: list[str] = []
        for p in self.promises:
            probe = p.get("probe") or {}
            if probe.get("type") != "k8s_exec":
                continue
            for required in ("namespace", "pod_label", "command"):
                if not probe.get(required):
                    bad.append(
                        f"  {p.get('id')}: k8s_exec probe missing "
                        f"required field {required!r}"
                    )
        self.assertFalse(
            bad,
            "k8s_exec probe shape errors:\n" + "\n".join(bad),
        )


if __name__ == "__main__":
    unittest.main()
