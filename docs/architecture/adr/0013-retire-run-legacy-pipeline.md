# ADR-0013 — Retire `run-legacy-pipeline`: one job framework, no bespoke paths

**Status:** Phases 1–3b accepted (2026-05-10). Phase 4 (per-*arr
adapter-hook migrations), Phase 5 (remaining apps), and Phase 6
(retire + hard-gate) remain. Closes the third leg of the
"single-framework" effort that ADR-0009 and ADR-0010 began.

Authors: matthew

## Status snapshot — 2026-05-10 (Phases 1–3b shipped)

The qBittorrent leg of the migration is fully on the unified Job
framework. The orchestrator's reconcile loop now drives credential
sync + rotation end-to-end; the deploy-time `compose_preflight` is
a thin shim that delegates to the same lifecycle method.

**Commits in this session** (all pushed to `main`):

| Commit     | Phase | Summary                                                                                  |
|------------|-------|------------------------------------------------------------------------------------------|
| `49d4999d` | doc   | ADR-0013 created                                                                          |
| `18effa24` | doc   | Clarification: relationship to ADR-0003 / ADR-0010 (no regression on lifecycle pattern)   |
| `00ae44d4` | 2     | qBittorrent verify-only ensurer: `probe_credentials_synced` + `ensure_credentials` + contract entry + promise |
| `1aa06cc8` | 3     | `ContainerAccess` Protocol in domain + `ComposeContainerAccess` in infra + `LifecycleResolver` wires `extra["container_access"]` + qBit `ensure_credentials` upgraded to verify+rotate |
| `2125bbcb` | 3b    | `K8sContainerAccess` (kubectl-backed) + `LifecycleResolver` detects platform + `compose_preflight` body retired (now a 30-line shim that calls the lifecycle) |

**One body, two entry points** (the end-state Phase 3b achieved):

```
deploy-time call:                       reconcile-tick call:
  deploy_stack_main                       JobRunner.run("qbittorrent:ensure-credentials")
    └── compose_preflight shim              └── _make_lifecycle_wrapper (Phase 2 bridge)
          └─────────────────┐                   └────────────┐
                            ▼                                ▼
                   QbittorrentLifecycle.ensure_credentials(OrchestrationContext)
                            │
                            ▼
                   probe → if .ok return success;
                   if .unknown return transient;
                   if .failed: pull ctx.extra["container_access"]
                       (ComposeContainerAccess on compose, K8sContainerAccess on k8s)
                       and rotate (read_logs → temp pw → exec_shell login + setPreferences)
```

The `[ERR] [ERR] run-legacy-pipeline: qBittorrent login failed
with secret credentials` error class can no longer surface — every
trigger reaches the same idempotent rotation body, and rotation
failures emit structured `Outcome.failure(transient=…, evidence=
{phase: …})` that lands in `/api/jobs.history` with cooldown.

**Phase 4 backlog — per-*arr legacy adapter-hook actions.**
`ServarrPipelineService._configure_single_app` runs six adapter
phases per *arr app (`load → precheck → prepare → configure →
ensure → status_check`). Each phase mutates real *arr config:
mediamanagement, download-handling, quality-upgrade, discovery
lists, root folder, etc. Some pieces have already been split off
into per-promise contract jobs (the `radarr.yaml` / `sonarr.yaml`
comments name them — `radarr-has-indexers`, `radarr-import-lists-
auto`, `radarr-download-client`, `radarr-jellyfin-notifier`,
`radarr-quality-profiles`, plus `radarr-api-key-discoverable`).
The unmigrated tail per *arr:

| Action (legacy `adapter.configure()` body) | Per-arr promise candidate          |
|--------------------------------------------|------------------------------------|
| mediamanagement settings (renames, hardlinks, recycle bin, etc.) | `<arr>-mediamanagement-policy`      |
| download-handling settings (failed-import, redownload, etc.)     | `<arr>-download-handling-policy`    |
| root-folder configuration                  | `<arr>-root-folder`                |
| quality-profile + cutoff reconciliation    | (partly covered by `*-quality-profiles`) |
| metadata-provider settings                 | `<arr>-metadata-provider`          |
| custom-format application                  | `<arr>-custom-formats`             |
| naming-convention application              | `<arr>-naming-convention`          |

Each row times four *arrs (radarr, sonarr, lidarr, readarr) gives
~28 contract jobs. Each is a small migration following the Phase
2 recipe: add a lifecycle method on `ServarrLifecycle` (or its
per-arr subclass) returning `Outcome`, bind it via
`LifecycleHandlerAdapter.bind`, add the promise + job entry. The
existing `arr_media_management_cfg` / `arr_download_handling_cfg`
/ `arr_quality_upgrade_cfg` config blocks already feed these
actions; the lifecycle method reads them from `ctx.config`.

**Phase 5 backlog — remaining apps**:

| App         | What's left in legacy runner                         |
|-------------|------------------------------------------------------|
| sabnzbd     | api-key handling already on lifecycle; remote-path mapping for usenet still legacy |
| jellyseerr  | most actions migrated; arr-server linking still legacy |
| maintainerr | rules-linked-to-arr is migrated; rule-translation still legacy |
| jellyfin    | libraries / livetv / prewarm already on jobs framework; sidecar config still legacy |
| homepage    | service URL templating still inside `runner.run` adapter hook |
| authelia    | initial seed step is migrated; group-mapping reconciliation still legacy |

Each app's checklist is what's referenced in its
`contracts/services/<app>.yaml` adapter_classes / event_handlers
blocks but has no `satisfies:` promise yet.

**Phase 6 — retire** (no commits yet):

* `services/apps/core/job_adapters.py::run_legacy_pipeline` deleted
* `contracts/services/core.yaml::run-legacy-pipeline` entry deleted
* `application/jobs/controller_runner.py::_build_runner` deleted
* `services/controller_service.py::_run_servarr_pipeline`,
  `_run_full_prechecks`, `_prepare_download_clients`,
  `_run_servarr_pipeline` deleted (and the `ControllerService`
  class shrinks to its remaining responsibilities or is itself
  retired)
* New hard-gate ratchet: `LegacyRunnerForbidden` — any reference
  to `run_legacy_pipeline` or `_build_runner` outside the migration
  shim fails CI immediately. (The shim itself goes away in this
  same commit, so the ratchet just enforces "no resurrection.")

**Discipline rules (apply to every Phase 4-5 commit)** — same as
the original ADR-0013 scope; restated here so each commit audits
itself:

1. Single framework — every migrated action becomes a
   `LifecycleHandlerAdapter.bind(...)` Job handler.
2. `OrchestrationContext` only — no `JobContext` reads inside
   lifecycle methods; the framework's `_make_lifecycle_wrapper`
   handles the bridge.
3. Idempotent ensurers — safe to invoke when the promise is
   already satisfied.
4. Transient-vs-permanent — auth/DNS/5xx → transient; schema /
   4xx / config-missing → permanent.
5. No new lazy imports inside method bodies in `domain/` —
   ADR-0011 leaf invariant. Lazy imports inside
   `application/` / `infrastructure/` lifecycle methods are
   allowed when justified by a real circular-import (Phase 3b
   added one inside `LifecycleResolver._k8s_container_access` to
   defer `kube_cmd` resolution until we know k8s is the active
   platform).
6. Contract-test coverage per migration — each commit adds tests
   that exercise the new ensurer end-to-end against a stub
   `OrchestrationContext`.
7. Per-service-split `_EXPECTED_TOTAL` ratchets get bumped in the
   same commit as the new promise is added (Phase 2/3 commits
   demonstrated this; Phase 4/5 follow the same rule).

## Relationship to ADR-0003 / ADR-0010 (read first if you're worried we're regressing)

The lifecycle-class pattern (e.g. `BazarrLifecycle`) is *not* what
this ADR retires. Three different dispatch paths have existed:

| Path                              | Status              | Retired by |
|-----------------------------------|---------------------|------------|
| `dispatch_ensurer(svc, method)`   | retired             | ADR-0010   |
| `JobRunner.run(name)` (framework) | the surviving path  | n/a        |
| `runner.run(runtime_state)` (legacy adapter-hooks pipeline) | active | this ADR |

ADR-0003 introduced lifecycle classes + promises. ADR-0010 noted the
"two mechanisms" problem (ensurer-dispatch vs Job-dispatch) and
retired the former — but explicitly kept lifecycle *classes* as the
per-service Python implementation, registered as Job handlers via
`LifecycleHandlerAdapter.bind(...)`. ADR-0013 reuses that exact
recipe. We are not introducing a fourth path, not reviving
`dispatch_ensurer`, and not regressing on ADR-0010. We are retiring
the *third* dispatch path — the v1.x adapter-hooks monolith
(`runner.run(runtime_state)`) — which pre-dates ADR-0003 and
neither ADR-0009 nor ADR-0010 touched.

After this ADR ships, exactly one dispatch path remains
(`JobRunner.run`) and lifecycle classes remain its per-service
implementation shape — exactly the end-state ADR-0010 described.

## Context

The controller still has a third dispatch path running alongside
the unified Job framework: the legacy adapter-hooks pipeline.

**The framework path** (where everything is supposed to land):

* Contract YAML at `contracts/services/<svc>.yaml` declares the job
  with `handler:`, `phase:`, `priority:`, `requires:`.
* `LifecycleHandlerAdapter.bind(<LifecycleClass>, "ensure_method")`
  produces the `(OrchestrationContext) -> dict` callable the
  framework invokes.
* The orchestrator's reconcile loop ticks promises; failed ensurers
  get cooldown / retry / `transient`-vs-`permanent` semantics.
* Every invocation lands in `/api/runs` and `/api/jobs.history`
  with `source` / `actor` tagging, latency, anomaly stats.
* This is what Bazarr's `ensure-config-wiring`, Jellyseerr's
  `ensure-oidc`, Sonarr's `ensure-indexers`, etc. already use.

**The bespoke path** (`run-legacy-pipeline`):

* Defined at `src/media_stack/services/apps/core/job_adapters.py:396`
  inside `CoreActionAdapters.run_legacy_pipeline(self, ctx)`.
* Registered as a single contract entry at
  `contracts/services/core.yaml:135` in the `pre_bootstrap` phase.
* Body: imports `_build_runner`, builds a `runner` from the bootstrap
  args, calls `runner.run(runtime_state)`. That call invokes the
  whole adapter-hooks pipeline — every app's install + configure —
  in one synchronous monolith.
* The work it does has no contract entries. The orchestrator can't
  see what's running. Failures get one rolled-up "legacy pipeline
  failed" line; cooldowns and retries don't apply per action.
* Several per-service contracts already split a *piece* off
  (e.g. `contracts/services/radarr.yaml:119`, `sonarr.yaml:141`
  comments referencing "legacy whole-pipeline run to its per-*arr
  essence — short-circuiting the same HTTP call the legacy pipeline
  made internally"). The remaining surface inside `runner.run` is
  the un-migrated tail.
* The `run_legacy_pipeline` docstring acknowledges it is
  transitional but load-bearing: "removing it would silently drop
  work for any service that hasn't been fully migrated to a contract
  job."

The `qBittorrent` credential failure observed today (`run-legacy-
pipeline: qBittorrent login failed with secret credentials`) is one
concrete artefact of the bespoke path. The compose preflight handler
that resets qBittorrent's password
(`infrastructure/qbittorrent/compose_preflight.py:232::ensure_compose_torrent_client_credentials`)
runs only when the stack is brought up via
`python -m media_stack.cli.commands.deploy_stack_main`. A plain
`docker compose up -d` skips it; the legacy pipeline then tries to
log into qBittorrent with `STACK_ADMIN_PASSWORD` and fails. Same
work, registered as a proper Job, would self-heal: the orchestrator
sees the failed promise, ticks the ensurer, runs the credential
reset, satisfies the promise. Nothing about that is qBittorrent-
specific — it's the cost of the bespoke path.

The same shape holds for every action still inside `runner.run`:
the work isn't ratchet-tracked, can't be retried with cooldowns,
doesn't show up in jobs history, and only fires from the deploy CLI
not the long-running controller.

## Decision

**Migrate every remaining action inside `runner.run(runtime_state)`
into per-service contract jobs, then retire `run-legacy-pipeline`
and `_build_runner`.** No new dispatch paths, no new framework —
the goal is exactly one path (the framework ADR-0009/0010
established) and zero bespoke ones.

### Common path (the only one)

For each action currently inside `runner.run`:

1. Identify the lifecycle method that owns the work (e.g.
   qBittorrent credential reset → an `ensure_credentials` method
   on `QbittorrentLifecycle`, parameterised on
   `OrchestrationContext`).
2. Add a contract entry to the relevant
   `contracts/services/<svc>.yaml` declaring the Job's `handler:`
   as a `LifecycleHandlerAdapter.bind(...)` reference.
3. The orchestrator picks up the new entry on next reconcile;
   `JobRunner.run` is the only entry point.

That's the same recipe Bazarr / Jellyseerr / Sonarr / etc. already
use. No file in `src/` gets a "for legacy" branch; the legacy path
is being deleted, not extended.

### Phase plan

The migration is incremental — each phase ships independently and
keeps both paths coexisting until the last action moves.

**Phase 1 — Inventory.** Walk `_build_runner` /
`runner.run(runtime_state)` and list every adapter-hooks action
that fires. Output: a table of `(service, action, current owner,
target contract job name)`. One commit, no behaviour change.

**Phase 2 — Migrate qBittorrent credential reset.** Smallest cohesive
slice that closes today's bug. A new
`qbittorrent:ensure-credentials` contract entry whose handler is
`LifecycleHandlerAdapter.bind(QbittorrentLifecycle,
"ensure_credentials")`. The lifecycle method takes the existing
preflight body (`infrastructure/qbittorrent/compose_preflight.py:232`)
and re-shapes it as `(OrchestrationContext) -> Outcome`. The legacy
pipeline's qBittorrent login attempt becomes a no-op once the
ensurer has satisfied the promise the first time.

**Phase 3 — Migrate per-*arr remaining actions.** The
`radarr.yaml:119` / `sonarr.yaml:141` comments name the next pieces:
each *arr has a small chunk of `runner.run` work that hasn't moved
yet. One commit per service, ~one contract entry per action.

**Phase 4 — Migrate cross-service actions.** The actions inside
`runner.run` that touch multiple services (e.g. seed-runtime-
overrides) become contract jobs whose `requires:` field encodes
the cross-service ordering instead of relying on `runner.run`'s
hard-coded sequence.

**Phase 5 — Migrate remaining apps.** Whatever is left after
Phases 2-4 — homepage's installer, authelia's seeder, etc. The
checklist is whatever Phase 1's inventory turned up.

**Phase 6 — Retire.** Once Phase 1's inventory shows zero rows
remaining inside `runner.run`:

* Delete `run_legacy_pipeline` from `services/apps/core/job_adapters.py`.
* Delete the `run-legacy-pipeline` entry from `contracts/services/core.yaml`.
* Delete `_build_runner` and the `runner.run(runtime_state)` plumbing.
* Add a hard-gate ratchet: any new entry referencing
  `run_legacy_pipeline` or `_build_runner` fails CI immediately.
* Bump the contract schema's `phase: pre_bootstrap` job count's
  hard floor.

### Discipline rules (apply to every Phase 2-5 commit)

Same rules ADR-0012 codified for the OO burndown — the migrations
must not introduce new bespoke paths or regressions:

1. **Single framework.** Every migrated action becomes a contract
   entry whose handler is `LifecycleHandlerAdapter.bind(...)`. No
   new ad-hoc registrations, no `if service == "xyz"` branches,
   no module-level `def run_xyz()` callbacks.
2. **`OrchestrationContext` only.** Lifecycle methods read from
   `ctx.config` / `ctx.secrets` / `ctx.service_id`. The
   application-layer wrapper at `application/jobs/framework.py`'s
   `_make_lifecycle_wrapper` (added 2026-05-10 for the JobContext
   bridge) is the only translator; lifecycle code never sees a
   `JobContext`.
3. **Idempotent ensurers.** Every migrated handler must be safe
   to invoke when the promise is already satisfied (return
   `Outcome.ok` with empty `evidence`, no I/O). The orchestrator
   ticks the ensurer until the promise is satisfied; that loop
   only terminates if the ensurer is idempotent.
4. **Transient-vs-permanent.** Auth failures, DNS misses, 5xx
   responses → `transient=True`. Schema mismatches, 4xx config
   errors, missing required env → `transient=False`. The
   orchestrator's cooldown logic depends on the distinction.
5. **No new lazy imports inside method bodies.** The leaf-invariant
   ratchet (ADR-0011) treats domain/`adapters/` as leaves. Anything
   the lifecycle methods need from infrastructure is injected via
   `OrchestrationContext` or a constructor-bound port.
6. **Contract-test coverage per migration.** Each Phase 2-5 commit
   adds a test that exercises the new ensurer end-to-end against a
   stub `OrchestrationContext`; the test must run in `tests/unit/`
   without the deploy CLI.

### Why the bespoke path can't stay

* **Can't be observed.** Work inside `runner.run` doesn't emit
  RunRecords, doesn't show in `/api/jobs.history`, doesn't
  contribute to the SLO dashboards. Operators can't tell whether
  qBittorrent's credential sync ran successfully today.
* **Can't be retried per-action.** The whole pipeline is one
  retry unit; a transient DNS failure in one app aborts the rest.
* **Can't be triggered by the orchestrator.** The legacy runner
  fires only from the deploy CLI; the long-running controller's
  reconcile loop can't drive it. That's why `docker compose up`
  doesn't self-heal qBittorrent — the runner only ran during the
  initial deploy that wiped its config.
* **Can't be unit-tested cleanly.** The runner has a single
  monolithic entry point; its actions don't have isolated test
  surfaces. The new contract jobs each have one.
* **Can't be ratchet-tracked.** ADR-0009/0010's contract-job hard
  gate doesn't apply to the legacy runner because it predates
  those ratchets. New work added inside `runner.run` slips through
  CI silently.

## Consequences

**Positive:**

* The controller has exactly one dispatch path. Operator mental
  model: "every action is a contract entry; failures retry with
  backoff; everything appears in jobs history."
* `docker compose up -d` self-heals same as the deploy CLI does,
  because the orchestrator (not the deploy CLI) drives every
  ensurer.
* Today's qBittorrent compose error becomes a non-event: the
  promise `qbittorrent_credentials_match` flips to satisfied on
  the first reconcile tick after the temporary password is
  observed, with no operator intervention.
* The `RunBootstrapJobRunner` god-class (30 methods today) shrinks
  significantly as `runner.run`'s tail moves out.

**Negative:**

* Phase 1's inventory is real work — `_build_runner` is opaque
  and the action set isn't documented. Plan for one contract-
  archaeology pass.
* Phase 3-5 are multi-week. Each per-service migration is small
  but the count of services with un-migrated tail is high.
* During Phases 2-5, both paths coexist. Care is needed to ensure
  a newly-migrated action doesn't double-fire (legacy runner
  attempts the work AND the new ensurer attempts it). Mitigation:
  the legacy runner's branches for migrated actions get short-
  circuited via a feature flag (`MIGRATED_TO_CONTRACT_<svc>`)
  that the runner checks; the flag flips at the same commit that
  lands the new contract entry.

**Neutral:**

* Behaviour is preserved end-to-end. Each Phase 2-5 commit is a
  pure migration — same actions, same outcomes, just routed
  through the framework instead of the runner.
* No new files in `src/` for the migration itself; the work
  expands existing per-service `Lifecycle` classes and existing
  `contracts/services/<svc>.yaml` files. Bigger by a few hundred
  lines per service; same total LoC count when the runner is
  retired.

## Cross-references

* **ADR-0003** — service lifecycle + promise orchestration. Provides
  `Promise` / `Outcome` / `OrchestrationContext` types; this ADR
  consumes them as-is.
* **ADR-0009** — trigger-driven Jobs framework. Establishes that
  `JobRunner.run` is the canonical trigger entry point. ADR-0013
  finalises that no other entry point exists.
* **ADR-0010** — collapse ensurers into Jobs. The "everything is a
  Job contract" decision; ADR-0013 is the dual: "and nothing is a
  hand-written runner pipeline."
* **ADR-0011** — import direction discipline. The lifecycle methods
  this ADR creates must respect leaf-invariants; the framework's
  `_make_lifecycle_wrapper` is the only application-layer-allowed
  bridge.
* **ADR-0012** — drive LOOSE/STATIC ratchets to zero. Same
  one-path-only philosophy applied to module-level functions; this
  ADR applies it to dispatch paths.

Hotfix bridge that surfaced this ADR: commit `65145b9e` (2026-05-10
v1.0.328) added `_make_lifecycle_wrapper` to `application/jobs/
framework.py` so the framework's `JobContext` translates to
`OrchestrationContext` before invoking lifecycle handlers. The fix
is forward-compatible with this ADR's migration: every Phase 2-5
ensurer uses the same translator.

## Stewardship

Owner: orchestrator subgraph (same as ADR-0009/0010 — the work
extends the framework those ADRs built).

Rollback: each Phase 2-5 commit is independently revertible — the
legacy runner is only fully retired in Phase 6, and Phase 6 is
gated on Phase 1's inventory hitting zero. If any phase breaks
production, the revert path is `git revert <commit>` and the
legacy runner picks up the action again on next deploy.

Observability for the migration itself: each Phase 2-5 commit's PR
description includes the inventory-table row(s) it crossed off.
The PR review for Phase 6 verifies the inventory is empty.
