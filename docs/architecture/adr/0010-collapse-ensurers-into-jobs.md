# ADR-0010 — Collapse ensurers into Jobs

**Status:** Accepted (2026-05-08). Phase 7 of the orchestration
unification effort. Companion to ADR-0009 (Phase 6, declarative
trigger-driven Jobs). ADR-0009 unified the orchestration layer so
that every Job is contract-driven and `JobRunner.run` is the only
entry point for triggered work. This ADR applies the same model to
the primitive layer underneath: the lifecycle ensurers that the
orchestrator currently dispatches alongside Jobs. After Phase 7 the
controller has one concept (Job), one entry point (`JobRunner.run`),
one contract type (the Job contract), and one promise→action
resolution table (built from Job contracts at boot).

Authors: matthew

## Context

ADR-0003 introduced the promise/lifecycle foundation: each promise
declares an `ensured_by` reference (a `LifecycleEnsurer:<service>:<method>`
or a `JobEnsurer:<job-name>`), and the orchestrator's reconcile loop
calls `dispatch_ensurer` to bring the promise back to satisfied.
ADR-0005 Phase 3 ported the legacy `ensure-*` job handlers to a
class-based wirer pattern (`LifecycleWirerBase` subclasses, one per
service-topic pair) so that the orchestrator's lifecycle dispatch
became the canonical mechanism. ADR-0006 Phase 2 co-located each
service's promises with its contract YAML.

ADR-0009 (Phase 6) layered the trigger-driven Jobs framework on top:
Jobs declare `triggers:` and `after:` in YAML, the `TriggerEngine`
indexes them at boot, and `JobRunner.run(name)` is the single entry
point for every triggered execution.

The result: the controller now has **two mechanisms doing the same
thing**.

|                       | Ensurer (today)                   | Job (today)                    |
|-----------------------|-----------------------------------|--------------------------------|
| What it is            | Function that achieves a state    | Function that achieves a state |
| How registered        | Python (lifecycle wirers)         | YAML contract                  |
| How invoked           | `dispatch_ensurer(service, method)` | `JobRunner.run(name)`        |
| Prerequisites         | Implicit in wirer call order      | Declared in contract           |
| Audit/SSE             | Not visible in Jobs UI            | Visible                        |
| Triggerable           | No                                | Yes                            |

Same shape, two mechanisms. The split is historical: ensurers came
first as atomic operations on individual promises; Jobs were layered
on top later and the ensurer layer kept its bespoke dispatcher.

### What exists today

Thirteen files under `src/media_stack/adapters/`:

* `_shared/lifecycle_wirer_base.py` — the `LifecycleWirerBase` class
  with shared probe/outcome shortcuts, the urllib HTTPError
  classifier, and the `ctx.secrets → os.environ` secret-discovery
  helper. The base intentionally does NOT abstract `probe(...)` /
  `ensure(...)` signatures — Servarr-family wirers take a
  `service_id` parameter, single-service wirers take only `ctx`.
* Twelve subclasses (the wirer files):
  * `qbittorrent/categories_wiring.py`
  * `jellyfin/libraries_wiring.py`
  * `jellyseerr/api_key_wiring.py`
  * `jellyseerr/config_wiring.py`
  * `bazarr/config_wiring.py`
  * `maintainerr/rules_wiring.py`
  * `servarr/runtime_defaults_wiring.py`
  * `servarr/download_client_wiring.py`
  * `servarr/indexer_pipeline.py`
  * `servarr/seed_series_wiring.py`
  * `servarr/notifier_wiring.py`
  * `servarr/api_key_wiring.py`

(The original Phase 7 brief mentioned 13 wirer files; `grep -rln
"LifecycleWirerBase" src` confirms 12 subclasses + the base. The
Servarr `seed_series_wiring.py` was the last one to land — there is
no missing 13th file.)

`src/media_stack/infrastructure/promises/dispatcher.py:712` —
`dispatch_ensurer(spec, *, resolver, now, secrets)` is the single
entry point. It branches on the discriminated union: `LifecycleEnsurer`
routes through `_ensure_lifecycle` (resolves the wirer instance via
`LifecycleResolver`, calls the named method); `JobEnsurer` routes
through `_ensure_job` (legacy path); `DeployEnsurer` and `InfraEnsurer`
return `Outcome.success` with `reason=externally_ensured`.

`src/media_stack/api/services/lifecycle_ensurer_invoker.py` —
ADR-0005 Phase 5b's `LifecycleEnsurerInvoker` class. Wraps
`dispatch_ensurer` for the operator dashboard's "Run now" buttons
and the auto-heal path. Constructor-injects `resolver`,
`registry_loader`, `dispatch_fn`, `clock`, `secrets_resolver`,
`logger`. Two production callers reach into `dispatch_ensurer`
through this surface.

`src/media_stack/application/services/orchestrator.py:659` — the
orchestrator's reconcile loop calls `dispatch_ensurer(promise.ensurer,
resolver=self._resolver, now=ensure_started, secrets=secrets)` once
per unsatisfied promise per tick, then re-probes.

`src/media_stack/application/jobs/framework.py:333` — `JobRunner`
class, `run` method at line 405. Event-driven dispatcher over a Job
tree: flattens, walks rounds, each round either makes progress or
stops. Records into the job-history table with the
`source` / `actor` tags ADR-0005 Phase 5b added.

The two dispatch surfaces are independent. The orchestrator's
ensurer dispatch does not flow through the JobRunner; the JobRunner
does not call `dispatch_ensurer`. Each has its own audit-emission
path, its own retry semantics, and its own UI surface.

### *arr family parameterization

The Servarr family is four services (sonarr, radarr, lidarr, readarr)
plus prowlarr, with roughly five ensurer methods each (api-key probe,
runtime-defaults, download-client wiring, indexer pipeline, notifier).
That's ~20 (service, method) pairs handled by Python parameterization
in the six `servarr/*_wiring.py` files: each wirer's `probe(ctx,
service_id)` / `ensure(ctx, service_id)` reads the per-service
config out of `ctx.config` and dispatches against the right *arr
host. Hand-writing 20 near-identical YAML contracts would be the
wrong shape — the parameterization is the whole point.

The remaining ensurers are single-service: `jellyfin-libraries`,
`qbittorrent-categories`, `jellyseerr-api-key`, `jellyseerr-config`,
`bazarr-config`, `maintainerr-rules`. (`gluetun` and `prowlarr`
single-service ensurers were considered during the brief but neither
is in the wirer list — gluetun's health is a probe-only promise; the
prowlarr indexer pipeline is part of the Servarr family wirers.)
Six single-service ensurers total.

## Decision

Collapse ensurers into Jobs. After Phase 7:

* Each ensurer becomes a single-step Job contract.
* The promise it satisfies is declared on the Job via a new
  `satisfies: [promise-name]` schema field. `JobRunner.complete`
  emits a `promise.satisfied` event for each entry, which
  ADR-0009's `TriggerEngine` processes (the engine already consumes
  promise-state events as one of its trigger sources).
* Lifecycle wirers, the `LifecycleWirerBase`, and the wirer registry
  are deleted.
* `dispatch_ensurer` is deleted from
  `infrastructure/promises/dispatcher.py`.
* The promise→action resolution table (currently built by Python at
  module-load time when the wirer files import) becomes a
  promise→Job table built at boot by the same loader that builds
  ADR-0009's trigger index.

Single concept (Job), single entry point (`JobRunner.run`), single
contract type, single audit/SSE surface.

### Schema addition

A new optional list-of-strings field on Job contracts:

```yaml
plugin:
  jobs:
    "ensure-jellyfin-libraries":
      handler: media_stack.application.jellyfin.runtime_ops:ensure_jellyfin_libraries
      label: "Ensure libraries"
      requires: [media_server_reachable, media_server_api_key]
      satisfies: [jellyfin-libraries]
```

Loader validates that every name in `satisfies:` resolves to a
loaded promise (the same registry the orchestrator consults today).
A typo fails fast at boot, not at first reconcile-tick. A Job may
satisfy multiple promises — rare but not prohibited (an `ensure-*`
Job that fixes both an "API key set" and a "config valid" promise,
for example).

### *arr family: contract generation

A small build-step script reads the *arr-family service list out of
`contracts/services/services.yaml` (the catalog file added by
Phase 7.2 — see "Phases" below) and emits one Job contract per
(service, ensurer-method) pair into `contracts/jobs/generated/`.
Output is checked in: diff-friendly, code-reviewable, no runtime
mechanism. Codegen is the protobuf model — generation happens on
build, the runtime reads only the generated YAML.

The script is ~50 LoC in `tools/contract_codegen/`. Source-of-truth
is the *arr family list and the per-method template; the runtime
sees ~20 generated Job contracts indistinguishable from
hand-written ones.

A CI ratchet (`make codegen-check`) reruns the generator and asserts
the output matches what's checked in. Regenerating after a template
change is a one-line build step.

### Single-service ensurers: hand-written contracts

The six single-service ensurers become hand-written Job contracts
in `contracts/jobs/`. ~5 files (jellyseerr's two wirers collapse
into one Jellyseerr contract with two Jobs; same for any other
service that grew a second wiring). The hand-written file count is
load-bearing only insofar as it stays small — if a future service
grows past two ensurer methods, the maintainer's call whether to
keep them hand-written or feed the family generator instead.

## Phases

Each phase is its own commit, revertible without touching later
phases. Same staged-rollout shape as ADR-0003 / ADR-0005 / ADR-0006.

### Phase 7.1 — `satisfies:` schema field

Add the field to the Job contract loader. Loader validates that
every name in `satisfies:` resolves to a known promise; unknown
names fail boot with the file path and the offending Job name in
the error.

`JobRunner.complete` emits one `promise.satisfied` event per entry
in `satisfies:` on successful Job completion (no event on failure —
the orchestrator's re-probe path is the source of truth for
`promise.satisfied`; this event is the JobRunner-side signal that a
satisfaction attempt _completed_, which ADR-0009's TriggerEngine
already consumes).

No ensurer is migrated yet. Ensurers continue to dispatch through
`dispatch_ensurer`. The schema field is dead until Phase 7.4 deletes
the wirers.

### Phase 7.2 — Contract generator

`tools/contract_codegen/`:

* `tools/contract_codegen/__init__.py` — module marker.
* `tools/contract_codegen/generate_servarr_jobs.py` — ~50-line script.
* `tools/contract_codegen/templates/servarr_job.yaml.j2` — the
  per-method template.

Reads the *arr family list out of `contracts/services/services.yaml`
(creating it as a thin catalog file if it doesn't already exist —
`contracts/services/` is currently per-service yaml only). Emits
one file per (service, method) pair into
`contracts/jobs/generated/`. Outputs are checked in.

CI ratchet: `make codegen-check` reruns the generator into a temp
dir and `diff`s against the checked-in output. Drift fails CI.

No runtime change. The generated contracts are dead until
Phase 7.4 deletes the wirers.

### Phase 7.3 — Hand-written single-service Job contracts

Six contracts under `contracts/jobs/` (or co-located on each
service's existing `contracts/services/<svc>.yaml::plugin.jobs`
block, per ADR-0006's locality preference — the migration commit
chooses per-service which is clearer):

* `jellyfin/ensure-libraries.yaml`
* `qbittorrent/ensure-categories.yaml`
* `jellyseerr/ensure-api-key.yaml`
* `jellyseerr/ensure-config.yaml`
* `bazarr/ensure-config.yaml`
* `maintainerr/ensure-rules.yaml`

Each is a single-step Job calling the same handler the corresponding
wirer's `ensure(...)` method calls today. `satisfies:` references
the matching promise. `requires:` matches the wirer's implicit
prerequisite list (most wirers depend on the service's
`*_reachable` and `*_api_key` promises; those become explicit).

No ensurer is deleted yet. Both surfaces coexist: the orchestrator
still dispatches the lifecycle ensurer, the new Jobs are dead until
Phase 7.4 cuts the wirer over.

### Phase 7.4 — Delete the wirer infrastructure

Atomic deletion commit:

* `src/media_stack/adapters/_shared/lifecycle_wirer_base.py` —
  delete.
* The 12 wirer files — delete.
* The wirer registry (the `LifecycleResolver` machinery in
  `infrastructure/promises/dispatcher.py` that resolves a service id
  to a wirer instance) — delete.
* `dispatch_ensurer` and the `_ensure_lifecycle` /
  `_ensure_job` / `_ensure_deploy` / `_ensure_infra` helpers in
  `infrastructure/promises/dispatcher.py` — delete.

Net delete: ~1500 LoC. The probe-side dispatcher (`dispatch_probe`
in the same file) stays — probes are not ensurers.

### Phase 7.5 — Migrate the two `dispatch_ensurer` callers

Two production call sites:

* `src/media_stack/application/services/orchestrator.py:659` — the
  reconcile loop's per-promise ensurer dispatch. Replace with
  `JobRunner.run(promise.ensured_by_job_name)` where
  `ensured_by_job_name` is resolved from the new
  `satisfies:`-built reverse index. The orchestrator's re-probe
  immediately after still happens.
* `src/media_stack/api/services/lifecycle_ensurer_invoker.py` —
  the operator-facing manual-invoke path (the dashboard's "Run now"
  buttons + auto-heal). Replace `dispatch_fn` with a thin wrapper
  that calls `JobRunner.run(job_name)`. The
  `LifecycleEnsurerInvocation` value object stays as the wire
  shape; the `(service, method)` pair maps to the Job name through
  the reverse index. Source-tagging (`operator` / `auto-heal` /
  `orchestrator-tick`) flows through `JobRunner`'s existing
  `source` / `actor` constructor kwargs.

Atomic step semantics preserved: a single-step Job is still atomic.
The orchestrator's "did it work?" answer is the re-probe, same as
today.

### Phase 7.6 — Architecture ratchets

* `tests/unit/architecture/test_no_lifecycle_wirers.py` — pin the
  absence of `LifecycleWirerBase`. Walks `src/` and asserts no class
  inherits from it (the base itself is gone after Phase 7.4, so the
  test is "no class anywhere matches the legacy shape").
* `tests/unit/architecture/test_no_dispatch_ensurer.py` — pin the
  absence of `dispatch_ensurer`. Greps `src/` for the symbol.

Same ratchet shape as `test_no_legacy_handlers.py` and the existing
ADR-0007 architecture pins.

### Phase 7.7 — Bake

Bundled with ADR-0009's Phase 6 image cut. Single image
(v1.0.326 / UI v1.3.83) ships both the trigger-driven Jobs framework
and the ensurer collapse. The two ADRs are co-deployed because
Phase 7.5's caller migration depends on `JobRunner.run` being the
canonical entry point — which is itself ADR-0009's deliverable.

## Cost analysis

* **+** ~25 generated Job contracts (~80 LoC YAML each, but the
  generator emits them — review-cost is the template, not 25 files).
* **+** ~5 hand-written Job contracts for the single-service
  ensurers (~80 LoC each).
* **+** ~50-line contract generator (`tools/contract_codegen/`).
* **+** Two architecture ratchets (~30 LoC each).
* **−** ~1500 LoC of wirer + ensurer + dispatch infrastructure
  deleted (`LifecycleWirerBase` + 12 wirer subclasses +
  `dispatch_ensurer` + `_ensure_lifecycle` / `_ensure_job` helpers
  + the `LifecycleResolver` plumbing).

Roughly LoC-neutral. Conceptually a much smaller surface: one
mechanism instead of two, one dispatch entry point instead of two,
one audit/SSE surface instead of two.

## Risk areas

1. **Job framework overhead per atomic operation.** A
   single-step Job does more bookkeeping than a direct
   `dispatch_ensurer` call (job-history row, prereq evaluation,
   round-stepping). Measured during ADR-0007 work as negligible —
   single-digit milliseconds per dispatch, dominated by the actual
   handler's HTTP I/O. Re-confirm with a microbenchmark before
   Phase 7 closes; the full reconcile loop runs every 30s and each
   tick dispatches ~10 ensurers, so even a 10ms-per-call regression
   is invisible at the loop level.

2. **Generator is a new build step.** Mitigated: 50-line script,
   output checked in, CI ratchet enforces freshness. Adds no runtime
   mechanism. Operators who don't touch the *arr family never run
   it. The script is invoked from `make codegen` and from
   `make codegen-check` (the CI assertion); both are existing make
   targets the build already runs.

3. **Contract bloat in source tree.** ~25 generated contracts
   under `contracts/jobs/generated/` is real but acceptable. One
   subdirectory, easy to scope-out in code review (a
   `.gitattributes` `linguist-generated=true` entry collapses it on
   GitHub). The hand-written ~5 single-service contracts live
   wherever the per-service ADR-0006 layout puts them.

## What this enables

A plugin shipping a new *arr-class service is contracts-only after
Phase 7:

1. Add the service to `contracts/services/services.yaml`.
2. Re-run the generator (or include pre-generated contracts in the
   plugin bundle).
3. Ship `contracts/jobs/plugins/<plugin>/` with any plugin-specific
   Jobs and triggers.

Zero Python changes for additive plugins. The generic *arr ensurer
methods (set quality profile, set indexer, set download client,
seed series, set notifier) are already covered by the generated
contracts because they're parameterized by `service_id` against a
common API surface.

A non-*arr-class plugin still ships Python (its handler module) plus
contracts (one Job per atomic operation). The Python is the
business logic; the contracts are the wiring. Same shape as today's
non-Servarr ensurers, with Job contracts in place of wirer classes.

## Cross-references

* **ADR-0003** (service lifecycle + promise orchestration): provides
  the `Promise` / `Outcome` / `LifecycleResolver` types this ADR
  consumes. The promise schema is unchanged; only the resolution of
  `ensured_by` shifts from Python wirers to Job contracts.
* **ADR-0005** (orchestrator-driven bootstrap): the
  `LifecycleEnsurerInvoker` introduced by Phase 5b is the second of
  the two `dispatch_ensurer` callers Phase 7.5 migrates. The
  invocation-source tagging (operator / auto-heal /
  orchestrator-tick) flows through `JobRunner`'s existing
  `source` / `actor` kwargs.
* **ADR-0006** (per-service promise registries): the new
  `satisfies:` field references promises from the per-service
  registries this ADR finalized. The hand-written single-service
  Job contracts in Phase 7.3 follow the same per-service-yaml
  locality the promises themselves use.
* **ADR-0007** (OpenAPI-driven routing): the `RouteModule` /
  architecture-ratchet pattern Phase 7.6 follows.
* **ADR-0009** (trigger-driven Jobs framework): companion ADR.
  ADR-0009 makes `JobRunner.run` the canonical trigger entry point;
  this ADR makes it the canonical _ensurer_ entry point too. The
  two ADRs ship together (Phase 7.7 bake).

## Stewardship

Owner: orchestrator subgraph. Reviewed alongside the existing
`LifecycleResolver` + `dispatch_ensurer` callers, the `JobRunner`
machinery, and the per-service promise registries. The two
production callers (`Orchestrator` reconcile loop,
`LifecycleEnsurerInvoker`) are the load-bearing migration sites;
both are unit-test-covered already and the migration commits keep
those tests passing.

Rollback: each phase is a single commit, revertible without
touching later phases. Phase 7.4's deletion is the irreversible
boundary — once the wirer infrastructure is gone, the orchestrator
must use the Job path. Phase 7.3 leaves both surfaces coexisting,
which is the safe pre-cutover state. Phase 7.5's caller migration
is reversible by reverting that commit alone (the wirers are
already deleted, so revert means restoring `dispatch_ensurer` from
git history); in practice if Phase 7.5 fails the recovery is to
fix-forward, not revert.
