# ADR-0006 — Per-service promise registries

**Status:** Phase 1 shipped (2026-05-03). Phase 2 (per-family
migrations) + Phase 3 (cleanup) pending. Builds on ADR-0003
(orchestrator primitives) and ADR-0005 (orchestrator-driven
bootstrap). Doesn't unblock anything load-bearing — this is a
locality / authoring-ergonomics improvement that compounds as new
services ship.

Phase 1 deliverables landed:

- ``infrastructure/promises/registry.py`` refactored into a class
  hierarchy: :class:`ContractsLocator` (dev/container path
  resolution), :class:`ProbeSpecParser` + :class:`EnsurerSpecParser`
  (Strategy + Dispatch Table over the discriminated unions),
  :class:`PromiseEntryParser` (composition over the two parsers),
  :class:`PromiseRegistryLoader` (Repository / Aggregator over
  per-service + cross-cutting sources), :class:`PromiseRegistryResult`
  (frozen result-bundle with promise list + source-path map +
  warnings).
- Module-level ``load_registry`` / ``default_registry_path`` /
  ``default_contracts_root`` preserved as thin shims over default-
  instantiated class instances. The ``load_registry(path=...)``
  single-file form (used by ``test_promises_registry_loader.py``)
  keeps working unchanged.
- Loader walks BOTH sources: ``contracts/services/*.yaml::plugin.promises``
  AND ``contracts/promises/cross_cutting.yaml`` (with fallback to
  the legacy ``promises.yaml`` for the deprecation grace window).
  Today the per-service walk reports zero entries — Phase 2 starts
  filling them.
- Cross-file validation runs at every ``aggregate()`` call:
  * Duplicate id across files → :class:`PromiseRegistryError` with
    BOTH source paths in the message.
  * ``depends_on`` references unknown promise → error with the
    parent promise id + the unresolvable dep id.
  * Ensurer-reference cross-file warnings — placeholder; Phase 2
    populates the warning messages once per-service migrations
    establish the expected co-location pattern.
- 21 new tests at
  ``tests/unit/infrastructure/test_promise_registry_loader_aggregation.py``
  pin the aggregation, validation, parser dispatch, and locator
  behaviour. The 56 existing tests covering the orchestrator,
  blocking loop, contract integration, and dispatch ratchets
  continue to pass against the refactor with no behavioural change.

What's still TODO before Phase 2:

- Per-family migrations (one commit per service family). Order:
  Jellyfin first (matches ADR-0005 Phase 2's family proof), then
  Servarr (sonarr/radarr/lidarr/readarr/prowlarr together), then
  qBit/SAB, Bazarr, Jellyseerr, Maintainerr, Authelia/Authentik,
  Homepage/FlareSolverr, gateway/envoy.
- After each family lands, re-run ``bin/ops/orchestrator-eval`` on
  compose to confirm the promise set is unchanged byte-for-byte.
- Add the cross-file ensurer-resolution warning logic (Phase 2 work
  — needs the family-by-family expected pattern locked in first).

## Context

Today the promise registry lives in a single 1063-line YAML at
`contracts/promises/promises.yaml`. Editing a service's behavior
typically means touching TWO files:

  * `contracts/services/<svc>.yaml` for the service's jobs,
    lifecycle class, preflight handler, adapter classes, etc.
  * `contracts/promises/promises.yaml` for the service's promises +
    their probes + their ensurer references.

The cross-reference goes both directions:

  * A promise's `ensured_by: <job-name>` resolves against
    `<svc>.yaml`'s `plugin.jobs:` entries.
  * A `LifecycleEnsurer` resolves the lifecycle class declared in
    the same `<svc>.yaml`.
  * A job that exists ONLY because a promise dispatches it (e.g.
    `ensure-jellyfin-libraries`) sits in one file while the
    promise pinning its existence sits in the other.

The dual-file pattern has compounded since ADR-0003 (~52 promises
across ~14 services + a handful of cross-cutting ones). Three real
problems:

1. **Drift risk.** Renaming a job, a service, or a probe assertion
   means remembering to update both files. Search-and-replace works
   for ids; not for assertion semantics or evidence keys.
2. **Review locality.** A "what does Jellyfin do at bootstrap?"
   audit currently means opening `jellyfin.yaml` AND grepping
   `promises.yaml` for every `id: jellyfin-*` plus every
   `service: jellyfin` probe. Co-location lets one scroll cover it.
3. **Onboarding cost.** A new service author currently has to
   discover that promises live in a separate file from everything
   else they author. Co-locating teaches itself.

Cross-cutting promises (gateway readiness, ingress, audit-log
existence, infra invariants) genuinely don't belong to any one
service. Those are real cross-cutting concerns, not "leftover
because moving them is hard" — they should keep a top-level home.

## Decision

Move per-service promises into the corresponding service contract
under a new `plugin.promises:` block. Keep cross-cutting promises
in a thin `contracts/promises/cross_cutting.yaml`. Update the
loader to aggregate from both sources.

### Schema

Per-service promises live under `plugin.promises:` in
`contracts/services/<svc>.yaml`, alongside the service's existing
`plugin.jobs:` / `plugin.event_handlers:` / etc.:

```yaml
# contracts/services/jellyfin.yaml
plugin:
  jobs:
    "ensure-jellyfin-libraries":
      handler: media_stack.application.jellyfin.runtime_ops:ensure_jellyfin_libraries
      label: "Ensure libraries"
      requires: [media_server_reachable, media_server_api_key]
  promises:
    - id: jellyfin-libraries
      description: Jellyfin has Movies, TV Shows, Music, Books libraries pointing at /media/*
      ensured_by: ensure-jellyfin-libraries  # ← resolves in same file
      platforms: [compose, k8s]
      bootstrap_blocking: true
      probe:
        type: http_json
        service: jellyfin
        path: /Library/VirtualFolders
        auth: jellyfin_key
        assert: ...
```

Cross-cutting promises live in a renamed thin file:

```yaml
# contracts/promises/cross_cutting.yaml
promises:
  - id: gateway-https-listener-up
    description: Edge gateway HTTPS listener accepting connections
    platforms: [k8s]
    bootstrap_blocking: true
    probe:
      type: http_status
      service: gateway_https
      path: /healthz
      assert_expr: status == 200
    ensured_by:
      type: infra
      target: gateway
  - id: audit-log-exists
    description: Controller audit-log file exists on the PVC
    platforms: [compose, k8s]
    probe:
      type: file_text
      path: .controller/audit.log.jsonl
      assert: data is not None
    ensured_by:
      type: infra
      target: audit_log
```

The schema for each promise entry is unchanged from
`promises.yaml` today — the field set, the probe/ensurer
discriminated unions, `bootstrap_blocking`, `depends_on`,
`platforms`. Only the file location changes.

### Loader changes

`infrastructure/promises/registry.py::load_registry` becomes a
two-stage aggregator. Reading the spec in plain English:

1. Walk `contracts/services/*.yaml`. For each file with a
   `plugin.promises:` list, parse each entry through the existing
   `_parse_promise` helper. Tag the source file on the parsed
   `Promise` (a `_source_path` attribute or a parallel index)
   so error messages can point operators at the right file.
2. Walk `contracts/promises/*.yaml` (plural — both
   `cross_cutting.yaml` and the legacy `promises.yaml` during the
   migration). Parse each entry the same way.
3. Validate aggregate invariants:
   * No duplicate `id` across files (current loader already rejects
     within a file; the cross-file check is new).
   * Every `depends_on` resolves to a known promise id (previously
     implicit because all promises were in one file; now needs to
     be a defended invariant).
   * Every `LifecycleEnsurer:<service>:<method>` resolves to a
     declared `plugin.lifecycle_class` somewhere in the loaded
     contracts.
   * Every `JobEnsurer:<job-name>` resolves to a `plugin.jobs:`
     entry SOMEWHERE — but warns when the resolution crosses a
     file (a `jellyfin-libraries` promise referencing an
     `ensure-jellyfin-libraries` job in `prowlarr.yaml` is almost
     certainly a typo).
4. Sort the aggregate by `id` for deterministic ordering (the
   orchestrator's topo sort handles real ordering; this is for log
   stability).

The class structure follows the OO discipline — a
`PromiseRegistryLoader` class (constructor-injected `contracts_dir:
Path`) with `load_per_service()`, `load_cross_cutting()`, and
`aggregate(...)` methods replacing the current loose
`load_registry()` function. The module-level `load_registry()`
becomes a thin shim around a default-instantiated loader.

### What gets retired

Once Phase 2 of this ADR completes:

| Legacy artifact | Replacement |
|-----------------|-------------|
| `contracts/promises/promises.yaml` (1063-line monolith) | per-service `plugin.promises:` blocks + thin `contracts/promises/cross_cutting.yaml` |
| Implicit "loader walks one file" assumption | `PromiseRegistryLoader` with explicit aggregation step |
| Cross-file ensurer lookup happening at orchestrator dispatch time | Loader-time validation; the orchestrator just dispatches |

## Migration plan

Same staged-rollout shape as ADR-0003 / ADR-0005: per-family
migrations, idempotent overlap during transition, single-commit
revert at every step.

**Phase 1** (~3 days) — loader + dual-source support:

- Implement `PromiseRegistryLoader` with `load_per_service()` /
  `load_cross_cutting()` / `aggregate()` methods. Loader reads
  BOTH `contracts/services/*.yaml::plugin.promises` AND
  `contracts/promises/*.yaml::promises`. No promises are
  migrated yet — the legacy file stays canonical.
- Add the validation passes: cross-file id uniqueness, depends_on
  resolution, ensurer reference resolution.
- Tests: synthetic contract trees with promises in both
  locations; verify aggregation, ID-collision detection,
  cross-file depends_on rejection.
- Existing 52 promises keep working unchanged because nothing
  has moved yet.

**Phase 2** (~1 week) — per-family migration:

- For each service family (Jellyfin, Servarr, qbit/sab,
  Bazarr, Jellyseerr, Maintainerr, Authelia, Authentik,
  Homepage, FlareSolverr, Envoy, etc.), in one commit per
  family:
  * Move the family's promises from `promises.yaml` into the
    corresponding `<svc>.yaml::plugin.promises:` block.
  * Refresh the per-service contract's section comments to
    explain the new co-location.
  * Single-commit revert if the family breaks.
- After each family lands, run the orchestrator eval CLI
  (`bin/ops/orchestrator-eval`) against compose + k8s to
  confirm the promise set is unchanged.

**Phase 3** (~2 days) — cleanup:

- Rename what's left of `contracts/promises/promises.yaml` to
  `contracts/promises/cross_cutting.yaml` once only the
  cross-cutting set remains. The legacy filename keeps
  resolving for one release cycle as a deprecation grace
  window (loader walks the directory; both names are
  recognized).
- Add a ratchet that fails CI if a per-service promise
  appears in `contracts/promises/cross_cutting.yaml`. The
  rule: cross-cutting means no `service: <single-svc>` field
  in the probe AND no `LifecycleEnsurer:<single-svc>` in the
  ensurer.
- Drop the legacy filename support after one release.

## What this DOES NOT do

- **Doesn't change the Promise schema.** Every field, every
  probe/ensurer kind, every assertion stays identical. The
  migration is purely about WHERE the YAML lives.
- **Doesn't change the orchestrator.** `PromiseOrchestrator`
  consumes the aggregated registry the same way it consumes the
  monolithic one.
- **Doesn't change cross-service `depends_on`.** A promise in
  `jellyfin.yaml` can still `depends_on: [<promise in some other
  file>]`. The aggregate registry presents the same flat namespace
  the loader produced before.
- **Doesn't break the operator CLI.** `orchestrator-eval` keeps
  its current shape — it queries the aggregated registry through
  the same `load_registry()` entrypoint.

## Honest cost-benefit

**Cost:**

- ~2 weeks of focused authoring work (52 promises across ~14
  service families)
- One real architectural primitive (the loader's aggregation +
  validation pass) that needs careful testing for the cross-file
  invariants (id uniqueness, depends_on resolution)
- Each per-family migration commit needs a quick eyeball
  against `bin/ops/orchestrator-eval` output to confirm zero
  drift
- Risk: a typo in a `<svc>.yaml::plugin.promises:` block
  silently drops the promise from the registry. Mitigation:
  Phase 1's id-uniqueness + ratchet against the EXPECTED set
  (the count today is 52 — Phase 3 ratchet locks it).

**Benefit:**

- **Locality.** "What does Jellyfin do at bootstrap?" is one file
  read. Adding a new service is one file authored.
- **Drift mitigation.** Renaming `ensure-jellyfin-libraries`
  surfaces in code review against the same file — the promise
  reference and the job entry are visible side-by-side.
- **Cross-file ensurer warnings.** A promise referencing an
  ensurer in an unrelated file (typo, copy-paste error) gets
  flagged at load time, not at dispatch time.
- **Onboarding signal.** New service authors discover promises
  via the contract template, not via "and there's also this
  separate registry file."
- **Smaller, easier diffs.** Adding a service no longer mutates
  the central registry.

### Why do it

The orchestrator's value (ADR-0003 / ADR-0005) is "single source
of truth for what should be true." That truth is currently
authored in two files per service. As long as the per-service
truth is split, every service-author edit is two diffs and one
extra mental hop. The split is also what makes the
`<svc>.yaml::plugin.*` contract pattern feel half-finished — it
covers jobs, lifecycle, event_handlers, adapter_classes, but not
promises.

The longer this ADR is deferred, the more entries accumulate in
the monolithic file (Phase 3 of ADR-0003 + ADR-0005 land more
promises) and the more the migration costs.

## Open questions

1. **Cross-cutting filename.** `cross_cutting.yaml` is wordy;
   `contracts/promises/global.yaml` or `infrastructure.yaml` are
   alternatives. Lean toward `cross_cutting.yaml` because
   "cross-cutting" is the mental model the file actually
   implements; "global" reads as catch-all.

2. **Duplicate-id semantics across migration.** During Phase 2,
   if a promise is mid-move (already authored in `<svc>.yaml`
   AND not yet deleted from `promises.yaml`), the loader's
   id-uniqueness check would fire. Two options:
   a) Refuse the load (current loader semantics — simpler).
   b) Prefer the per-service entry, log a deprecation warning.
   Lean toward (a). It forces the migration commit to be
   atomic per-promise (or per-family with the legacy entries
   removed in the same commit).

3. **Ratchet for the per-service split.** Phase 3 wants a
   ratchet that flags new per-service promises landing in
   `cross_cutting.yaml`. The check is: probe.service or
   ensurer.service is set AND points at a service whose
   contract YAML has a `plugin.promises:` block. Question is
   whether to make this a strict ratchet (fail) or a soft
   warning. Lean toward strict — the soft warning is dead
   weight; we either care or we don't.

4. **Lifecycle of the legacy filename.** `promises.yaml` will
   exist as cross_cutting.yaml's predecessor for ~one release
   cycle. Operators with vendored copies need a graceful
   warning when they hit the old filename. The loader logs at
   INFO when it loads from the legacy path; CHANGELOG calls
   it out at the version where the legacy path is dropped.

5. **Cross-platform aliases.** A promise that applies to
   `[compose, k8s]` lives once; one that applies only to k8s
   has `platforms: [k8s]`. Should the per-service contract
   declare a default `platforms:` for ALL its promises (DRY)?
   Lean against — explicit per-promise is clearer when 80% of
   promises share a default but the 20% exceptions are the
   ones that get bugs.

6. **Schema validation timing.** Today the YAML loader rejects
   malformed entries at `load_registry()` call time, which is
   per-process startup. Should we add a CI-time validator that
   runs against every `<svc>.yaml`? The ratchet suite already
   does this implicitly via the test that loads the registry.
   Probably fine.

## Stewardship

Same shape as ADR-0003 / ADR-0005: directional commitment, phased
rollout, explicit steward approval before each phase. Reversibility:
at every phase, the previous state's loader still works (the
aggregator reads BOTH locations during Phase 2 — a per-family
migration that breaks something can be reverted to the legacy
file with a single commit).

## Relationship to other ADRs

- **ADR-0003** (service lifecycle + orchestrator): provides the
  `PromiseOrchestrator` + lifecycle dispatch. This ADR doesn't
  touch any of that — only the source-of-truth file layout for
  the registry it consumes.
- **ADR-0005** (orchestrator-driven bootstrap): added
  `bootstrap_blocking`, `BlockingSummary`, the synthetic
  bootstrap job. The schema field added there carries
  unchanged into the per-service blocks.
- **ADR-0001 / ADR-0002** (repo / hexagonal restructure): the
  loader change lives in `infrastructure/promises/registry.py`
  (cross-cutting persistence concern). The class refactor here
  follows the same hexagon-conformant shape ADR-0005 used —
  loader is a class, module-level function is a thin shim.
