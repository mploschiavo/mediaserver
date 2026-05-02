# ADR-0003 — Service-lifecycle protocol and promise-driven orchestration

**Status:** In progress (last updated 2026-05-02). Multi-week migration. Builds on ADR-0001 and ADR-0002, both of which are largely implemented — the new hexagonal layers (`domain/`, `application/`, `adapters/`, `infrastructure/`, `interfaces/`) exist with substantial code (~52k LOC across ~383 files).

- Phase 0: **shipped** in v1.0.291 / v1.0.292 (`jellyfin:ensure-api-key` ensurer) and v1.0.293 (`jobs:close-stale-runs` ensurer). Pattern proven on two services.
- Phase 1: **shipped** in v1.0.295 — `ServiceLifecycle` Protocol, `ProbeResult`, `Outcome[T]`, `OrchestrationContext` in `domain/services/`. Pure addition; no behavior change. 20 unit tests covering factories, frozen-ness, runtime-checkable Protocol semantics, and the package re-export surface.
- Phase 2: **shipped** in v1.0.296 — `JellyfinLifecycle` (wraps existing `infrastructure.jellyfin`) and `ServarrLifecycle(service_id)` parameterized for sonarr/radarr/lidarr/readarr/prowlarr. Six contract YAMLs name `plugin.lifecycle_class`; permissive ratchet asserts conformance. Bazarr deferred to Phase 3. 47 unit tests; pure additive code, runtime behavior unchanged.
- Phase 3: **shipped** across three slices (v1.0.297, .298, .299). 3a: `QbittorrentLifecycle` (session-cookie auth, honest-failure semantics retiring the silent-error-as-ok bug class) + `SabnzbdLifecycle` (INI variant). 3b: `BazarrLifecycle` + `JellyseerrLifecycle` + `MaintainerrLifecycle` (first "no API key concept" shape). 3c: `AutheliaLifecycle` + `AuthentikLifecycle` + `HomepageLifecycle` + `FlaresolverrLifecycle` + `EnvoyLifecycle` — all built on a shared `NoApiKeyLifecycleBase` (~80 LOC saved per service). 16 of the planned services now Protocol-conformant; ratchet floor 6 → 16. 139 unit tests covering all phases. Pure additive code throughout.
- Phase 4: **shipped**. Slice 4a (v1.0.300): types + loader + schema ratchet. Slice 4b (v1.0.301): orchestrator core + dispatcher + cooldown + CLI. Slice 4c (v1.0.302): `orchestrator:satisfy-shadow` job handler + auto-heal hookup at 60s cadence in `dry_run=True` so probes fire but ensurers don't conflict with legacy. Slice 4d (v1.0.303–v1.0.308): live-data discrepancy fixes — synthetic-service URL resolution (controller / gateway_http / gateway_https), promise registry container-path candidate walk, weakened-then-restored gateway-https probe, jellyfin-libraries assert without `Boxsets`, restored strict `radarr-import-lists-auto` after fixing the underlying ensurer, removed silent `app_keys` re-casing in the *arr defaults wrapper.
- Phase 5: **shipped**. Staged rollout from shadow to primary via `ORCHESTRATOR_LIVE_SERVICES` env-var allowlist. 5a (v1.0.309): jellyfin → primary; auto-heal direct hook for `jellyfin:ensure-api-key` retired (orchestrator owns it). 5b/5c/5d: servarr family + remaining services flipped to primary; 16/16 services live. Coverage matrix at `docs/architecture/orchestrator-coverage-matrix.md` documents what the orchestrator covers vs what the bootstrap-phase code paths still own. Slice 5e (deletion of legacy paths) **partially shipped** — see audit at `docs/architecture/phase-5e-deletion-audit.md`. 5e.1 landed (assert-evaluator extracted to its own module so the legacy CLI is no longer on the orchestrator's runtime path). **5e.2 shipped in v1.0.311** — legacy `media-stack-probe-promises` CLI deleted; `verify-fresh-install.sh` runs through `media-stack-verify` (ADR-0004). The remaining 5/6 originally-scoped paths still load-bearing for non-orchestrator flows (bootstrap, manual job invocation, cron) become 5e.3+ work, deferred to ADR-0005.
- Phase 6: **shipped via ADR-0004**. 6.1 (orchestrator state HTTP endpoint), 6.2 (`FreshInstallVerifier` class), 6.3 (`media-stack-verify` CLI), 6.4 (`verify-fresh-install.sh` switchover) in v1.0.310; 6.5 (legacy CLI deletion) in v1.0.311. Operator and orchestrator now agree on "is the stack healthy?" by construction — same data, one runtime; one fewer parallel implementation to maintain.
- Phase 7+: **deferred to ADR-0005**. "Bootstrap consumes orchestrator state" so `_run_preflights`, `phase_scripts.media_server_bootstrap`, and the bootstrap-only ensurers can be retired (5e.3+). Multi-week design+implementation.

**Related:** ADR-0002's tail (Phase 16-F shim removals + `services/` and `core/` cleanup) runs in **parallel** with this ADR — neither blocks the other. ADR-0001 punted "service uniformity" as out of scope; this ADR addresses it.

## Context

The codebase has accumulated three real architectural patterns in tension:

1. **Per-service bespoke OO.** Each service (`jellyfin`, `sonarr`, `qbittorrent`, `sabnzbd`, etc.) has its own ad-hoc set of classes for the same lifecycle questions:

   ```
   infrastructure/jellyfin/    : 13 classes — JellyfinHttpPreflight, JellyfinComposePreflight,
                                 JellyfinControllerDbDiscoveryService, JellyfinControllerApiKeyService,
                                 JellyfinControllerAuthService, JellyfinAdminOps,
                                 EnsureJellyfinControllerMain, JellyfinApiKeyDb,
                                 JellyfinBootstrapConfig, ...

   infrastructure/sabnzbd/     : 3 classes (SabnzbdHttpPreflight, SabnzbdComposePreflight,
                                 SabnzbdApiAccessService)

   infrastructure/qbittorrent/ : 4 classes (QbittorrentHttpPreflight, QbittorrentComposePreflight,
                                 QbittorrentAdminOps, ...)

   infrastructure/servarr/     : ServarrCommon + 5 *Ops classes (good factoring; see below)
   ```

   Five `*HttpPreflight` classes. Four `*ComposePreflight` classes. **Zero shared interface.** Every service answers "is it running?", "does it have an API key?", "how do I mint one?", "where is it persisted?", "how do I read it later?" with bespoke code instead of a uniform Protocol.

2. **Contract YAML knows the abstraction; code does not.** Every `contracts/services/<id>.yaml` has the same shape:

   ```yaml
   service:
     id, name, host, port, health_path, auth_path, auth_mode, login_mode, login_path
     api_key_env, api_key_config, api_key_format    # declarative — sqlite|xml|ini|http
   ```

   The contract is uniform across 29 services. The code re-invents per-service. The contract's `api_key_format=sqlite|xml|ini` field is not consumed by a uniform reader; instead, four different SQLite readers exist for Jellyfin alone (in `health.py::discover_api_keys`, in `_harvest_keys_from_disk`, in `infrastructure/jellyfin/api_key_db.py`, and in `controller_db_discovery_service.py`).

3. **Two orchestrators, neither complete.** Bootstrap uses an imperative pipeline (`_run_preflights` + `_try_satisfy_prereqs` + `phase_scripts.media_server_bootstrap` + `compose_preflight_handler` + `plugin.preflight_handler`). The promise framework (`contracts/promises/promises.yaml` + `media-stack-probe-promises`) runs as a separate verification layer *after* bootstrap. They have overlapping responsibilities, no shared state, and no shared retry/cooldown discipline.

   Concrete failure observed on a fresh compose deploy of `v1.0.290`:

   - The bootstrap "succeeded" (`initial_bootstrap_done=true`, zero errors logged).
   - Jellyfin's `http_preflight.run_preflight` was invoked **31 times in 2 seconds** because multiple `JobRunner` instances each hit `_try_satisfy_prereqs` concurrently, each retrying up to `max_attempts=3`.
   - All 31 invocations timed out at `_wait_ready` (Jellyfin wasn't fully up yet).
   - After bootstrap "completed", **no follow-up retry** ever fired. The Jellyfin API key was never minted.
   - `JELLYFIN_API_KEY` stayed empty in env, the Jellyfin SQLite DB had no key rows, and `discover_api_keys()` returned 7 of 8 expected keys.
   - Eight downstream `ensure_jellyfin_*` operations skipped because their prereq (the missing key) was unsatisfied.

   The promise framework's evaluator could have detected this, retried the ensurer with cooldown, and self-healed. It does not, because it is not the orchestrator.

## Decision

Establish two new architectural primitives and migrate to them in phases:

### 1. `ServiceLifecycle` Protocol

A single Protocol every service implements, declared in `domain/services/lifecycle.py`:

```python
@runtime_checkable
class ServiceLifecycle(Protocol):
    service_id: str

    def probe_running(self, ctx: OrchestrationContext) -> ProbeResult: ...
    def probe_has_api_key(self, ctx: OrchestrationContext) -> ProbeResult: ...
    def mint_api_key(self, ctx: OrchestrationContext) -> Outcome[str]: ...
    def discover_api_key(self, ctx: OrchestrationContext) -> str | None: ...
    def persist_api_key(self, key: str, ctx: OrchestrationContext) -> Outcome: ...
```

One implementation per service — or per service-family for shared shapes:

```
adapters/jellyfin/lifecycle.py        : JellyfinLifecycle    — SQLite read, REST mint
adapters/servarr/lifecycle.py         : ServarrLifecycle     — config.xml read, no mint
adapters/qbittorrent/lifecycle.py     : QbittorrentLifecycle — auth-cookie mint
adapters/sabnzbd/lifecycle.py         : SabnzbdLifecycle     — ini read, regen on demand
adapters/jellyseerr/lifecycle.py      : JellyseerrLifecycle  — settings.json
... (one per service)
```

The contract YAML names the lifecycle:

```yaml
plugin:
  lifecycle_class: media_stack.adapters.jellyfin.lifecycle:JellyfinLifecycle
```

Idempotency is required: `mint_api_key` must return the existing key if found, never re-mint unnecessarily. `discover_api_key` is the single canonical READ path.

### 2. Promise-driven orchestration

Bootstrap, auto-heal, and verification all become `satisfy_promises(registry, ctx)`:

```yaml
- id: jellyfin-running
  probe: { type: lifecycle, service: jellyfin, method: probe_running }
  ensured_by: { type: deploy, target: jellyfin }

- id: jellyfin-api-key-discoverable
  depends_on: [jellyfin-running]
  probe: { type: lifecycle, service: jellyfin, method: probe_has_api_key }
  ensured_by: { type: lifecycle, service: jellyfin, method: mint_api_key }

- id: sonarr-jellyfin-notifier
  depends_on: [jellyfin-api-key-discoverable, sonarr-running]
  probe: { type: http_json, service: sonarr, path: /api/v3/notification, ... }
  ensured_by: ensure-arr-jellyfin-notifier
```

The orchestrator:

- Topological sort by `depends_on`
- For each promise: probe → if fails, run ensurer → re-probe
- Cooldown + exponential backoff per promise
- Auto-heal cycle (every 60s) re-evaluates the same registry — same probes, same ensurers
- Replaces `_run_preflights`, `_try_satisfy_prereqs`, `phase_scripts.media_server_bootstrap`, `compose_preflight_handler`, the `max_attempts` retry loop in `JobRunner.run()`

## Layering rules (extending ADR-0002)

```
domain/services/lifecycle.py             ← Protocol; pure
domain/services/promises.py              ← promise types; pure
application/services/orchestrator.py     ← satisfy_promises(registry, ctx)
adapters/<service>/lifecycle.py          ← one ServiceLifecycle impl per service
infrastructure/promises/registry.py      ← YAML loader for contracts/promises/promises.yaml
```

Ratchet-enforced (extending `tests/unit/ratchets/test_layering.py`):

```
adapters/<service>/lifecycle.py MUST implement ServiceLifecycle
adapters/<service>/lifecycle.py MUST NOT import from another service's adapter
contracts/services/<id>.yaml MUST name `plugin.lifecycle_class`
The promise registry MUST cover every service's "running" and "has_api_key" promises
```

## Honest cost-benefit

**Cost:**

- ~2,000 LOC moves across 29 services into uniform `ServiceLifecycle` impls
- Net code is **negative** by ~3,000 LOC — the 5 `*HttpPreflight`, 4 `*ComposePreflight`, `EnsureJellyfinControllerMain`, and 4 SQLite readers all collapse into per-service lifecycle methods
- The new hexagonal layers (`domain/`, `application/`, `adapters/`) already exist (per ADR-0002 implementation), so no precondition cleanup is required
- ADR-0002's tail (shim removal, `services/`/`core/` cleanup) can run in parallel — these phases don't block each other
- ~3 weeks of focused work, or ~6 weeks as a side stream alongside feature work
- Tests need to grow: each `ServiceLifecycle` impl needs probe + mint + discover + persist tests
- Every existing handler PR opened during the migration window has to know which path to use
- Risk: bootstrap currently works (mostly); a refactor introduces new bugs. Mitigation: shadow-running the new orchestrator in parallel with the old for one release before deletion.

**Benefit:**

- **Bug class eliminated:** the 31-concurrent-preflight stampede is structurally impossible under a single orchestrator with cooldown
- **One source of truth** for "what should be true" — the promise registry. `media-stack-probe-promises` becomes redundant (same code path)
- **Plugin model becomes real:** a third-party `pip install media-stack-emby-adapter` ships a `ServiceLifecycle` impl + a contract YAML; bootstrap picks it up automatically. Compare to today, where adding a service requires touching 5+ files in different layers
- **Per-service code volume drops dramatically.** Jellyfin's 13 classes become 1 lifecycle + smaller helpers. Sonarr/Radarr/Lidarr/Bazarr/Readarr/Prowlarr collapse into one `ServarrLifecycle` parameterized by `service_id`
- **Self-healing falls out of the model.** Auto-heal already runs every 60s; pointing it at the promise registry gives every service automatic retry without any per-service code
- **Failure messages improve.** Instead of "31 invocations, gave up", operators get "promise X has failed 4 times, last error: Y, ensurer Z will retry in 30s"
- **Ratchet-enforceable:** new services can't merge without a `ServiceLifecycle` impl + promise registry entries

### Why do it anyway

The current pattern of "every service is a snowflake" is unsustainable at 29 services. Adding the 30th via the bespoke pattern is more painful than the previous 29; the marginal cost is rising, not falling. The contract YAML already half-knows the right abstraction; the code half-uses it. Finishing the abstraction is a one-time cost that compounds positively for every future service, every future bug fix, and every future operator who needs to understand "what happens during bootstrap?".

## Migration plan

**Phase 0 (this week, ~50 LOC, immediate):**

- Add the missing `jellyfin-api-key-discoverable` promise to `contracts/promises/promises.yaml`
- Wire it to be re-evaluated on the auto-heal cycle (cooldown, idempotent)
- Fix Jellyfin's API key end-to-end as a side effect
- Do NOT introduce `ServiceLifecycle` Protocol yet
- This proves the pattern at minimal scope and unblocks the operator

**Phase 1 (~1 week — was previously framed as "ADR-0002 prerequisite"; revised):**

- Define `ServiceLifecycle` Protocol in `domain/services/lifecycle.py`
- Define `OrchestrationContext`, `ProbeResult`, `Outcome[T]` value types in `domain/services/`
- The new layers already exist (per ADR-0002 implementation), so the Protocol and value types live in their natural homes from day one
- ADR-0002's tail (shim removal in `services/apps/`, `core/` migration) runs in parallel — neither phase blocks the other
- No new code is written against the deprecated paths; new ServiceLifecycle impls go straight into `adapters/<service>/lifecycle.py`

**Phase 2 (~1 week):**

- Implement `JellyfinLifecycle` in `adapters/jellyfin/lifecycle.py` — collapse the 13 existing Jellyfin infrastructure classes into one cohesive lifecycle + small helpers
- Implement `ServarrLifecycle(service_id)` in `adapters/servarr/lifecycle.py` as the parameterized proof — one class for sonarr/radarr/lidarr/readarr/bazarr/prowlarr (already factored as `ServarrCommon` + per-tech ops; this Protocol-shapes the public surface)
- Add `plugin.lifecycle_class` field to the affected contract YAMLs
- Add ratchet that asserts every service YAML names a lifecycle class (initially permissive — applies to Jellyfin and Servarr only; tightens as Phase 3 progresses)
- New code only; old paths still work

**Phase 3 (~2 weeks):**

- Implement remaining `ServiceLifecycle` impls (qBittorrent, SABnzbd, Jellyseerr, Maintainerr, Bazarr already covered by ServarrLifecycle, Authelia, Authentik, Homepage, FlareSolverr, Envoy)
- Each impl is small; mostly mechanical translation from existing per-service preflights
- Per-service tests: probe + mint + discover + persist round-trip
- Old paths still work — no deletion yet

**Phase 4 (~1 week):**

- Implement `application/services/orchestrator.py::satisfy_promises(registry, ctx)`
- Run it shadow-mode during bootstrap (alongside the old pipeline) for one release
- Compare outputs; flag discrepancies in run-history
- Fix any disagreements before flipping the switch

**Phase 5 (~1-2 weeks, staged per service-family):**

Per Phase 4d's live shadow data, the orchestrator probes are honest
and the lifecycle implementations are well-tested (216 unit tests
across Phase 1-3). The ensurer side is less battle-tested in
production scenarios — flipping `dry_run=False` globally would
expose every service's mint/persist code at once. Staged rollout
limits blast radius:

- **Phase 5a (`~1 day`):** flip `dry_run=False` for **Jellyfin
  promises only**. Concretely: add a `force_dry_run_unless` allowlist
  to `satisfy_promises` that runs ensurers ONLY for promises whose
  matching service id is in the allowlist; default starts with
  `["jellyfin"]`. Other promises continue to dry-run-shadow as in
  Phase 4. Soak for ~24h on compose; confirm `JELLYFIN_API_KEY`
  state matches what the legacy `jellyfin:ensure-api-key` Phase 0
  ensurer produced. Keep the legacy ensurer running in parallel
  during the soak — both being idempotent, double-mint is a no-op.

- **Phase 5b (`~1-2 days`):** expand allowlist to Servarr family
  (sonarr, radarr, lidarr, readarr, prowlarr). Soak ~24h. Servarr
  "mint" is poll-and-wait (auto-generated config.xml); the
  orchestrator's mint just observes whether the file is there yet,
  so the risk is even lower than Jellyfin's REST mint.

- **Phase 5c (`~2-3 days`):** expand to qBittorrent + SABnzbd +
  Bazarr + Jellyseerr + Maintainerr (the file-based + auth-cookie
  families). Then auth + no-API-key services (Authelia, Authentik,
  Homepage, FlareSolverr, Envoy).

- **Phase 5d (`~2 days`):** retire the legacy ensurer hooks in
  `api/services/auto_heal.py` (the per-service `run_job` calls for
  `jellyfin:ensure-api-key`, `jobs:close-stale-runs`, etc.). The
  orchestrator covers the same ground via its own ensurer cycle.

- **Phase 5e** (revised 2026-05-02 from original "~3 days delete
  six legacy paths"): see `docs/architecture/phase-5e-deletion-audit.md`.
  Audit finds 5 of 6 originally-listed deletions are still
  load-bearing for non-orchestrator flows (bootstrap pre-controller
  hooks, JobRunner-driven jobs, manual/cron invocations). Honest
  scope:
  - **5e.1** (shipped, `53da33e`): extract `_evaluate` from
    probe_promises CLI to `infrastructure/promises/assert_eval.py`
    so the CLI can be retired independently.
  - **5e.2** (blocked on ADR-0004 Phase 6.4): delete
    `media-stack-probe-promises` CLI once `verify-fresh-install.sh`
    swaps to the new promise-driven verifier.
  - **5e.3+** (separate future phase, "bootstrap consumes
    orchestrator state"): retire `_run_preflights`,
    `phase_scripts.media_server_bootstrap`, and the bootstrap-phase
    `jellyfin:ensure-api-key` job by making the orchestrator's
    first tick part of the bootstrap critical path. Real design
    work; warrants its own ADR amendment.
  - **NOT deletable** without first reshaping JobRunner callers:
    `compose_preflight_handler` (pre-controller), `_try_satisfy_prereqs`
    (JobRunner internals), `max_attempts` retry loop (same).

Rollback: at any phase, revert the allowlist to the previous size.
The orchestrator's ensurers are idempotent, so a regression
("orchestrator and legacy disagree on what should happen") just
shows up as a no-op overlap; revert to shadow and investigate.

**Phase 6 (cleanup):**

- Delete the 4 SQLite readers, the 5 `*HttpPreflight` classes, the 4 `*ComposePreflight` classes
- Their behaviors are absorbed into per-service `ServiceLifecycle` impls
- Final ratchet pass: every layering rule green, every service has a lifecycle, every promise has an ensurer

Total: **~5 weeks of focused work**, or ~10 weeks as a side stream alongside feature work. **Net LOC: -3,000** (the existing per-service `*HttpPreflight`, `*ComposePreflight`, `EnsureJellyfinControllerMain`, and 4 SQLite readers all collapse into uniform `ServiceLifecycle` impls; the ratio of deletions to additions is roughly 2:1).

## Enforcement

A new ratchet `tests/unit/ratchets/test_service_lifecycle_ratchet.py`:

- Every `contracts/services/<id>.yaml` (excluding `_template.yaml`, `_core.yaml`) MUST name `plugin.lifecycle_class`
- The named class MUST exist and MUST be a `ServiceLifecycle`
- Every promise in `contracts/promises/promises.yaml` MUST have either an `ensured_by: ensure-<name>` (ensurer registered in code) or `ensured_by: { type: lifecycle, service: <id>, method: <name> }` resolving against the lifecycle Protocol
- New services that merge without a lifecycle FAIL CI

Once the ratchet is in place, the architecture cannot drift back.

## Out of scope

- CQRS / event sourcing — `RunRecord` is already JSONL append-only; not needed at this scale
- Service mesh / sidecar abstractions — Envoy + the existing routing layer is sufficient
- A new layer between adapters and infrastructure — `ServiceLifecycle` lives in `domain/services/`, the impls live in `adapters/<service>/`; ADR-0002's hexagonal layout already accommodates this
- Replacing the job framework — `JobRunner` survives; `satisfy_promises` runs jobs through it, not in place of it

## Stewardship

This ADR is **directional**. Phase 0 ships immediately as a tactical bug fix. Phases 1+ require explicit steward approval before each phase begins, and pause on any test regression or operator-visible breakage. The migration may be paused, restarted, or scoped down if the cost-benefit shifts. The "migration debt zero" milestone is what this ADR commits to deliver — the timeline and exact phasing remain negotiable.

If after Phase 2 the proof-of-concept (Jellyfin + Servarr lifecycles) reveals that the abstraction is wrong, this ADR is reversible: the new code is additive; the old paths still work; we delete the new layer and try again. **The cost of being wrong is a 1-week rollback, not a multi-month rebuild.**
