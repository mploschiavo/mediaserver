# ADR-0005 — Bootstrap consumes orchestrator state

**Status:** Phases 1–5a fully shipped. Phases 1–3 landed
2026-05-03; Phase 4 (``LifecycleWirerBase`` extraction) followed
shortly after — all 8 wirer classes (``JellyfinNotifierWirer``,
``IndexerPipelineWirer``, ``BazarrConfigWirer``,
``JellyseerrConfigWirer``, ``RuntimeDefaultsWirer``,
``SeedSeriesWirer``, ``CategoriesWirer``,
``MaintainerrCollectionsWirer``) inherit from
``adapters/_shared/lifecycle_wirer_base.py`` and share the
``ProbeResult`` / ``Outcome`` / HTTP-classifier / secret-discovery
helpers. Phase 5a (legacy ``/status`` shape removal) shipped
2026-05-06: UI in v1.3.76 stopped consuming
``current_action`` / ``phases_completed`` / legacy ``phase``;
controller v1.0.322 stopped emitting them; CLI wait flow
(``ControllerJobWaitService`` /
``BootstrapPodHttpClient``) migrated to ``/api/jobs/running`` +
``initial_bootstrap_done`` + ``error`` for terminal-state and
in-flight-action checks. Both targets (k8s + compose) live on the
new contract. Builds on ADR-0003 (orchestrator) and ADR-0004
(verifier). Unblocks ADR-0003 Phase 5e.3+ deletions.

**Next:** Phases 5b and 5c are sequenced and in flight (no
deferrals). 5b retires the legacy ``ensure-*`` registrations
behind a new ``/api/lifecycle-ensurers/{service}/{method}``
endpoint that operator-dashboard "Run now" + auto-heal both
migrate to. 5c retires ``_run_preflights`` + JobRunner's
bootstrap-exclusive machinery and lands the closure: bootstrap
becomes a registered Job-framework job, ``action_trigger`` and
``current_action`` / ``action_history`` go away, every top-level
unit of work flows through one path. See the Phase 5 section
below for the per-step plan.

**Side-fix shipped on the way**: ``ControllerState.initial_bootstrap_done``
now persists across restarts via the existing
``runtime-config.json`` sidecar, via a new
``mark_initial_bootstrap_done()`` method. Without persistence, an
already-bootstrapped install would wedge the dashboard banner on
Queued every time the controller pod restarted. UI's
history-derived fallback (Phase 5a) is still the durable
defense; this is the principled-pair on the controller side. The
flag itself goes away in 5c.4 once bootstrap is a normal
Job-framework job (run history is the single source of truth).

Phase 3 final state (final wave shipped at ``e4c9b5b4``):

| Wirer class | Promises covered | Reference commit |
|---|---|---|
| ``JellyfinNotifierWirer`` | sonarr/radarr/lidarr ``-jellyfin-notifier`` (3) | ``1f13a8a6`` (proof-of-pattern) |
| ``IndexerPipelineWirer`` | sonarr/radarr ``-has-indexers`` (2) | ``f241f639`` |
| ``BazarrConfigWirer`` | 5 bazarr ``-*`` promises | ``f241f639`` |
| ``JellyseerrConfigWirer`` | 3 jellyseerr ``-*`` promises | ``f241f639`` |
| ``RuntimeDefaultsWirer`` | sonarr/radarr ``-quality-profiles`` + ``radarr-import-lists-auto`` (3) | ``e4c9b5b4`` |
| ``SeedSeriesWirer`` | ``sonarr-has-series`` (1) | ``e4c9b5b4`` |
| ``CategoriesWirer`` | ``qbittorrent-categories`` (1) | ``e4c9b5b4`` |
| ``MaintainerrCollectionsWirer`` | ``maintainerr-rules-linked-to-arr`` (1) | ``e4c9b5b4`` |

Across the wave, three patterns crystallized into the recipe (see
``ref_adr_0005_phase_3_cutover_recipe`` memory):

- **Standard wirer** (notifier / indexers / qbit / runtime-defaults
  probes) — wirer owns the HTTP shape, payload, idempotent skip.
- **Monolithic-handler decision** (Bazarr, RuntimeDefaults
  ensurer) — when one operation drives N invariants, use 1 shared
  ensurer + N distinct probes; pin shared-ensurer invariant.
- **Wide-handler delegation** (Jellyseerr, SeedSeries, Maintainerr) —
  when the legacy handler is >150 LoC of multi-subsystem
  orchestration, keep it as the implementation; wirer delegates
  via injected ``configure_handler`` + ``job_context_factory``
  callables.

Each cutover follows a 7-step recipe (read handler → extract
wirer class → lifecycle-method delegators → YAML cutover → drop
``phase: post`` from legacy job → add to
``_ORCHESTRATOR_LIFECYCLE_DISPATCHED`` allowlist → write
wiring-pin ratchet) plus a mandatory ``grep -rn "<job>.*priority"
tests/`` step to find sister ratchets pinning legacy phase /
priority and restructure them in the same diff.

Bug caught during the wave: ``maintainerr-rules-linked-to-arr``'s
legacy ``ensured_by: configure-collections`` was a misnomer —
``configure-collections`` is the Jellyfin auto-collections job,
unrelated to Maintainerr. The cutover untangles this; the real
handler is ``ensure_maintainerr_integrations``.

## Phase 4 — ``LifecycleWirerBase`` extraction (shipped)

8 sibling wirer classes shared the same shape (constructor +
``probe`` + ``ensure`` + helpers). Duplicate-code ratchet had
bumped from 19 → 23 across the Phase 3 wave. Phase 4 extracted
``adapters/_shared/lifecycle_wirer_base.py`` exposing:

- ``_probe_ok`` / ``_probe_failed`` / ``_probe_unknown`` —
  ``ProbeResult`` constructors with ``ctx.now()`` evaluation
  timestamp.
- ``_outcome_success`` / ``_outcome_transient`` /
  ``_outcome_permanent`` — ``Outcome[None]`` constructors.
- ``_classify_http_outcome`` — urllib exception → canonical
  ``Outcome`` shape (HTTPError = permanent, URLError /
  OSError / TimeoutError = transient, anything else
  propagates).
- ``_discover_secret`` — ``ctx.secrets`` first, ``os.environ``
  fallback. The established pattern across every wirer +
  ServarrLifecycle.

All 8 wirers inherit; signatures are intentionally NOT
abstracted (Servarr-family wirers parameterize on
``service_id``, single-service wirers take only ``ctx`` —
constraining ``probe`` / ``ensure`` would force an awkward
common-denominator).

## Phase 5 — retire bootstrap-only ensurers + close ADR-0003 Phase 5e.3+

Sliced into 5a / 5b / 5c:

**Phase 5a — legacy ``/status`` shape removal.** The controller's
``/status`` endpoint exposes a pre-Job-framework shape
(``current_action``, ``phases_completed``, legacy ``phase``)
that the bootstrap progress banner consumes alongside the
canonical ``/api/jobs/running`` + ``/api/jobs?history``
contracts. Two contracts for the same job → multi-source
tearing in the UI's setup-experience derivation. Phase 5a
retires the legacy shape:

1. UI stops consuming the legacy fields (``setupState.ts``'s
   ``timelineFromLegacyStatus`` path deleted; banner wrapper
   drops its ``/status`` query). The banner now reads only
   from the Job framework.
2. Controller deletes the fields from the ``/status`` response
   in a follow-up release, only after the UI rollout has
   stuck.

**Phase 5b — bootstrap-only ensurer retirement.** Bootstrap-only
ensurers (``apply-arr-runtime-defaults``,
``ensure-arr-download-client``, etc.) lose their handler
registrations — the wirers ARE the implementation now; the
legacy handlers exist only for ``run_job(name)`` / auto-heal
compatibility, which 5b also retires.

The audit during the v1.0.324 release confirmed the work
spans more than a YAML cleanup: phase-less ``ensure-*``
registrations are still resolved by ``run_job(name)`` from two
consumers — operator-dashboard "Run now" buttons and auto-heal's
recovery dispatch. Deleting registrations without migrating
those consumers breaks both flows. ``ensure-arr-download-client``
is also still actively scheduled (``phase: post, priority: 85``)
because Phase 3 never produced a 9th wirer for it.

Sequenced steps (each lands as its own commit + image bake +
deploy):

1. **5b.1 — DownloadClientWirer.** New
   ``adapters/servarr/download_client_wiring.py`` mirroring the
   existing 8 wirers (Servarr-family, parameterized on
   ``service_id``). Promises ``sonarr-download-client``,
   ``radarr-download-client`` (and lidarr/readarr if present)
   flip from string ``ensured_by: ensure-arr-download-client``
   to ``{type: lifecycle, service: <arr>, method:
   ensure_download_client}``. Drop ``phase: post`` +
   ``priority: 85`` from ``core.yaml::ensure-arr-download-client``.
2. **5b.2 — ``POST /api/lifecycle-ensurers/{service}/{method}``
   endpoint.** Service: ``LifecycleEnsurerInvoker`` looks up the
   registry entry, calls ``dispatch_ensurer(ctx, service,
   method)``, returns the ``Outcome`` envelope. CSRF +
   admin-gated. Same dispatch path the orchestrator already uses
   for promise satisfaction — three callers sharing one
   underlying mechanism, no per-caller branching.
3. **5b.3 — UI Run-now migration.** Audit existing
   ``useRunAction("<name>")`` consumers; for ensurer-shaped names
   thread through a new ``useRunLifecycleEnsurer(service, method)``
   hook calling the new endpoint. Genuine top-level job names
   (``orchestrator:satisfy-shadow``, ``jobs:close-stale-runs``,
   etc.) keep ``useRunAction``.
4. **5b.4 — Auto-heal migration.** Replace
   ``action_trigger("ensure-X", {"_source": "auto-heal"})``
   recovery dispatches with
   ``LifecycleEnsurerInvoker.invoke(service, method,
   source="auto-heal")``. Per-ensurer mapping table; e2e
   recovery test must keep passing.
5. **5b.5 — Delete legacy ensurer registrations.** Drop the
   phase-less stubs from ``core.yaml`` / ``jellyseerr.yaml`` /
   ``bazarr.yaml`` / ``sonarr.yaml`` / ``radarr.yaml``. New
   ratchet ``test_no_string_ensured_by`` pins that no
   ``ensured_by: <string>`` entries remain (everything is
   ``{type: lifecycle, ...}``).
6. **5b.6 — Live validation.** Bake controller + UI image. Roll
   to compose + k8s. Smoke: fresh install completes; auto-heal
   recovers a manually-killed *arr; "Run now" dispatches via the
   new endpoint.

**Phase 5c — ``_run_preflights`` + JobRunner internals + bootstrap
becomes a Job-framework job.** The legacy preflight pipeline in
``application/jobs/`` becomes redundant once the orchestrator
drives every per-service ensurer. The run-history /
phase-orchestration code paths that exclusively serve bootstrap
collapse. Closes ADR-0003 Phase 5e.3+ — the deferred
legacy-path deletions.

The audit confirmed ``_run_preflights`` is load-bearing today —
sole sync caller is ``job_adapters.py:1898`` inside
``discover-api-keys``. JobRunner also carries
bootstrap-exclusive machinery: ``_try_satisfy_prereqs``
(framework.py:637), the ``max_attempts`` retry loop
(framework.py:427), batch run-history bookkeeping
(framework.py:409). Retiring them is an architectural move, not a
delete-and-go.

Sequenced steps:

1. **5c.1 — ``discover-api-keys`` delegates to the orchestrator.**
   The legacy job becomes a thin wrapper invoking
   ``orchestrator.satisfy_scope([<arr>-api-key-discoverable, ...])``
   and waiting. Delete ``_run_preflights`` and the
   ``_run_handler_specs`` plumbing. ADR-0005 Phase 2's
   ``bootstrap:satisfy-promises`` post-phase already discovers
   keys via these promises; this consolidates to one path.
2. **5c.2 — Retire ``_try_satisfy_prereqs`` + ``max_attempts``
   retry loop.** Both exist because legacy bootstrap re-tried
   when prereqs weren't met. The orchestrator's tick is itself a
   settle-loop. JobRunner becomes single-pass: dispatch what's
   ready, mark unsatisfiable as ``error``.
3. **5c.3 — Retire batch run-history bookkeeping.**
   ``record_run_start`` / ``record_run_complete`` batch IDs and
   parent-child run linking — bootstrap was the only consumer.
   Per-job history continues working through the normal
   record-history path.
4. **5c.4 — Bootstrap becomes a Job-framework job.** This is the
   actual ADR-0005 closure. ``bootstrap`` registers in the
   contract registry as a normal top-level Job-framework job
   whose handler is "queue ``orchestrator:satisfy-shadow`` and
   wait." The legacy ``action_trigger`` path retires entirely;
   ``current_action`` and ``action_history`` come off
   ``ControllerState`` (the UI's Phase 5a fallback to
   ``action_history`` becomes unnecessary because bootstrap's
   ``run_id`` and history ``ts`` are now naturally available
   like every other job). ``controller_serve.py``'s
   ``_action_worker`` subprocess machinery collapses into the
   standard ``JobRunner.run`` call.
5. **5c.5 — Live validation + ADR closures.** Same scenario set
   as 5b.6. ADR-0003 Phase 5e.3+ marked closed (the deferred
   deletions ARE these). ADR-0005 status flips to "Phase 5
   fully shipped."

**Architectural end-state after 5c.4 lands:**

Two principled paths. No snowflakes.

- **``JobRunner.run(name)``** — every top-level scheduled or
  operator-runnable unit of work. Bootstrap, orchestrator
  ticks, media-integrity scans, queue housekeeping. One
  history table, one event stream, one set of dashboard
  widgets.
- **``dispatch_ensurer(service, method)``** — atomic promise
  satisfaction. Three callers sharing the same mechanism: the
  orchestrator's tick, the new
  ``/api/lifecycle-ensurers/{service}/{method}`` endpoint, and
  auto-heal's recovery dispatch. No per-caller branching
  inside ``dispatch_ensurer``.

The two paths exist because they have different lifecycle
granularity (promise = atomic, job = run-record-bearing).
Collapsing them would force an awkward common denominator.
Document this distinction explicitly in OpenAPI so the
``/api/jobs/run/{name}`` and ``/api/lifecycle-ensurers/...``
surfaces don't drift toward "two ways to do the same thing."

**Restart-resilience side-fix shipped 2026-05-07**:
``ControllerState.initial_bootstrap_done`` now persists to the
existing ``runtime-config.json`` sidecar via a new
``mark_initial_bootstrap_done()`` method (called from
``finish()`` and the two ``controller_serve.py`` flip sites).
Without persistence, every controller restart on an
already-bootstrapped install reset the in-memory flag to
``False`` and the dashboard banner wedged on Queued. The UI's
history-derived fallback (Phase 5a) is still the durable
defense; persistence is the principled fix on the
state-of-the-world. Pinned by
``test_state_architecture.py::TestInitialBootstrapDonePersistence``.
After 5c.4 lands, both this flag AND the
``action_history``-based fallback go away — bootstrap's run
record is the single source of truth, like every other job.

Phase 2 cutover landed — what changed:

- ``bootstrap:satisfy-promises`` graduated from the Phase-1
  ``orchestrator_satisfy`` holding-area phase into ``post`` priority
  100 (the FINAL step of the bootstrap post phase). The orchestrator
  is now part of the bootstrap critical path: every legacy post-phase
  ensurer runs first, then the orchestrator's blocking loop verifies
  the result and dispatches lifecycle ensurers for any promise that
  isn't yet ``ok``. The holding-area phase was retired from the
  ``_KNOWN_PHASES`` allowlist.
- The three Jellyfin family promises (``jellyfin-running``,
  ``jellyfin-api-key-discoverable``, ``jellyfin-libraries``) carry
  explicit ``bootstrap_blocking: true`` annotations. Default is True
  — the explicit annotation documents intent for future readers
  (this family is the cutover proof).
- ``jellyfin:ensure-api-key`` no longer carries ``phase: post``. It
  is still REGISTERED so ``run_job(name)`` (auto-heal + operator)
  resolves it, but the bootstrap DAG no longer schedules it directly.
  The orchestrator dispatches the same handler via the
  ``LifecycleEnsurer:jellyfin:mint_api_key`` ensurer attached to
  ``jellyfin-api-key-discoverable``.
- New contract-integration ratchet at
  ``tests/unit/contracts/test_adr_0005_phase_2_cutover.py`` pins the
  cutover wiring (synthetic job placement, family annotation,
  ensure-api-key unscheduled but registered, DAG inclusion). 11
  tests; reverting Phase 2 = revert this commit and the ratchet
  flips back into the prior state cleanly.

What's still TODO before Phase 3:

- Live test on compose: fresh ``compose down -v && up``. Verify
  bootstrap completes within timeout; ``JELLYFIN_API_KEY`` is set
  by the orchestrator dispatch path; no duplicate ``mint_api_key``
  invocations show up in the run-history.
- Live test on k8s: same thing in a fresh namespace.
- 24h soak on each platform.
- If a promise reports ``failed_permanent`` during bootstrap (e.g.
  Jellyfin pod stuck in CrashLoopBackOff), confirm the dashboard's
  status banner surfaces the ``permanent_failure_id`` rather than
  hanging on "loading".

Phase 1 deliverables (carried forward — synthetic job moved phase in
Phase 2, but the underlying primitive + class hierarchy + tests are
the same):

- ``Promise.bootstrap_blocking: bool = True`` field + YAML loader
  acceptance + strict bool validation. Default ``True`` preserves
  conservative wait-on-everything semantics until operators
  explicitly opt promises OUT.
- ``BlockingSummary`` domain dataclass + ``BlockingSummary.at``
  classmethod factory.
- ``PromiseOrchestrator`` class wraps ``tick()`` (single tick,
  formerly the loose ``satisfy_promises`` function) and
  ``tick_until_done()`` (multi-tick blocking loop with timeout +
  permanent-failure abort + tick interval). Helper classes
  ``PromiseGraph``, ``ProbeStatusInterpreter``, and
  ``BlockingLoopGuard`` carry the topology / status-mapping /
  termination-predicate concerns out of the orchestrator body.
- ``OrchestratorJobHandler`` class hierarchy in
  ``application/jobs/orchestrator_satisfy.py`` —
  ``OrchestratorShadowJobHandler`` (the existing 60s auto-heal
  tick) and ``OrchestratorBootstrapJobHandler`` (new, calls
  ``tick_until_done``) share env-knob / live-services /
  history-emit plumbing. Module-level ``satisfy_shadow`` /
  ``satisfy_blocking`` functions are thin shims over singleton
  instances so contract YAMLs that name a function path resolve
  unchanged.
- ``contracts/services/guardrails.yaml`` registers
  ``bootstrap:satisfy-promises`` in a new ``orchestrator_satisfy``
  phase that the bootstrap DAG loader's ``phase_order`` doesn't
  know about. The job is discoverable via ``run_job(name)`` and
  the contract registry, but is NOT scheduled as part of any
  bootstrap or auto-heal cycle yet.
- 59 unit tests covering Promise field validation, blocking-loop
  guard predicates, all three ``tick_until_done`` termination
  paths, env-knob parsing on the bootstrap handler, and CLI
  smoke. The 26 pre-existing orchestrator tests continue to pass
  against the class refactor with no behavioural change.

Phase 1 is intentionally landing-zone-only: zero promises have
been annotated with ``bootstrap_blocking: false`` yet, and the
bootstrap DAG continues to schedule its existing per-job
ensurers. Phase 2 (Jellyfin family proof) is the first cutover.

## Context

The Phase 5e deletion audit (`docs/architecture/phase-5e-deletion-
audit.md`) found that ADR-0003's "delete legacy preflight code paths"
goal is blocked by bootstrap's structural dependency on the legacy
patterns:

- `_run_preflights` is invoked synchronously inside specific
  bootstrap-phase jobs to wait for upstream services
- `phase_scripts.media_server_bootstrap` is run during the
  media_server phase for jellyfin's initial controller setup
- The bootstrap-phase `jellyfin:ensure-api-key` job (priority 80)
  ensures the Jellyfin API key is minted BEFORE downstream jobs in
  the same phase (`ensure-jellyfin-libraries`, etc.) need it

The orchestrator (ADR-0003) handles continuous-mode self-heal but
runs ASYNCHRONOUSLY every 60s. During bootstrap, downstream jobs
can't wait 60s — they need the world synchronously satisfied before
they fire. So bootstrap keeps its own per-job ensurer pattern, and
the orchestrator's coverage overlaps redundantly during steady
state.

The architectural goal: **make the orchestrator's
`satisfy_promises()` part of the bootstrap critical path**, so the
per-job bootstrap-phase ensurers can be retired and the orchestrator
becomes the single source of truth for both bootstrap and steady
state.

## Decision

Reshape bootstrap into two distinct phases:

### Phase A — Framework setup (sequential, NOT orchestrator-driven)

Things only bootstrap can do because they preceed the orchestrator's
ability to run:

1. **Pre-controller / platform setup**: compose preflight handlers,
   k8s manifest apply, image pulls. These run BEFORE the controller
   container exists, by definition.
2. **Controller boot**: process start, config load, migrations, run-
   history file open.
3. **State seeding**: `seed-runtime-overrides`, profile load,
   contracts dir mount.
4. **Auth bootstrap**: `configure-auth` + `envoy-config` run in the
   `infrastructure` phase to establish the request-routing path the
   orchestrator's probes will later use.

These remain in the legacy bootstrap DAG. The orchestrator can't
replace them because they're about getting the controller into a
state where it CAN orchestrate.

### Phase B — Service ensurance (orchestrator-driven)

After Phase A completes, bootstrap invokes:

```python
summary = satisfy_promises(
    platform=detect_platform(),
    dry_run=False,
    live_services=ALL_SERVICES,
    timeout_seconds=BOOTSTRAP_PROMISE_TIMEOUT,  # default 240s
    blocking=True,
)
```

This:
- Probes every applicable promise
- Dispatches ensurers for any that fail
- Re-probes
- Repeats with cooldown until either:
  - All applicable promises reach `ok` (success — bootstrap complete)
  - Timeout reached (failure — bootstrap declares partial complete)
  - A promise reaches `failed_permanent` (failure — operator action)

The per-job bootstrap-phase ensurers (`jellyfin:ensure-api-key`,
`ensure-jellyfin-libraries`, `apply-arr-runtime-defaults`, etc.) are
**no longer scheduled by the bootstrap DAG**. They're invoked only
through the orchestrator's promise→ensurer dispatch when the
matching probe fails. Single code path.

After Phase B, `initial_bootstrap_done = True` is set the same way
it is today.

## Phase ordering vs depends_on

Bootstrap currently uses phases (`preflight`, `infrastructure`,
`media_server`, `download_clients`, `default`, `post`) to order the
job DAG. The orchestrator uses `depends_on` between promises. These
need to map.

**Mapping rule:** Phase A jobs stay in the phase-ordered DAG. Phase
B promises declare `depends_on` on the relevant Phase A invariants
(e.g., `jellyfin-running` depends on jellyfin's container being up,
which Phase A guarantees by getting through `infrastructure` phase).

The bootstrap loader (the thing that builds the job DAG today) gains
a new step:

1. Build Phase A DAG from contract YAMLs (only jobs not covered by
   promises)
2. Append a single synthetic "satisfy promises" job at the end of
   Phase A's chain
3. The synthetic job invokes `satisfy_promises(blocking=True)` and
   blocks until success / timeout / permanent failure

## Failure semantics

Today bootstrap fails when any phase-marked-required job errors. The
new architecture preserves that for Phase A. Phase B's failure
semantics:

- **Transient failures** (e.g., service warming up) are expected
  during bootstrap; the orchestrator's cooldown + retry handles them
  silently within the timeout
- **Permanent failures** (operator config error) abort bootstrap
  immediately — no point waiting 240s for something that's
  structurally broken
- **Timeout** (240s default) declares "bootstrap completed with
  warnings"; sets `initial_bootstrap_done=True` so the UI doesn't
  hang on the loading state, but flags the failed promises in
  `/api/promises/state` for operator review

## Per-promise blocking vs timeout-bound

Some promises take longer than others. Sonarr's `mass-search-throttled`
runs for 600+ seconds (saw this in earlier diagnosis). We can't
block bootstrap on it.

**Solution:** annotate each promise with a `bootstrap_blocking`
field:

```yaml
- id: jellyfin-api-key-discoverable
  bootstrap_blocking: true   # bootstrap waits for this
  ...

- id: mass-search-completed
  bootstrap_blocking: false  # orchestrator handles in steady-state
  ...
```

`satisfy_promises(blocking=True)` only waits on promises with
`bootstrap_blocking=true`. Others get probed once during bootstrap
to populate state, then the auto-heal cycle takes over.

Default `bootstrap_blocking=true` for promises with `phase: post` or
unspecified phase; `false` for long-running operational promises.

## What gets retired in this ADR

Once Phase B is the canonical service-ensurance path:

| Legacy artifact | Replacement |
|-----------------|-------------|
| `_run_preflights` (per-job preflight in bootstrap) | Promise's `<service>-running` probe |
| `phase_scripts.media_server_bootstrap` | `JellyfinLifecycle.mint_api_key` (already exists) |
| Bootstrap-phase `jellyfin:ensure-api-key` job | Orchestrator's `LifecycleEnsurer` for `jellyfin-api-key-discoverable` |
| Per-service `phase: post` ensurer jobs (~25) | Orchestrator's `JobEnsurer` invocation through promises |

Once these are retired, the JobRunner's `_try_satisfy_prereqs` and
`max_attempts` retry loop become dead-code candidates (since all
non-promise JobRunner invocations also go through the orchestrator
now).

## Migration plan

Same staged-rollout shape as ADR-0003: per-service-family
promotions, idempotent overlap during transition, single-commit
revert at every step.

**Phase 1** (~1 week) — design + scaffolding:

- Add `bootstrap_blocking` field to the `Promise` dataclass (default
  True; False for operational promises). Loader update + ratchet.
- Add `blocking` + `timeout_seconds` parameters to
  `satisfy_promises()`. When `blocking=True`, the function loops
  until steady-state or timeout.
- Add a synthetic bootstrap job (`bootstrap:satisfy-promises`) that
  invokes `satisfy_promises(blocking=True)` and reports the
  TickSummary.
- Wire it into the bootstrap DAG as a final-phase job.
- Tests: synthetic registry with mixed blocking/non-blocking
  promises, verify the right ones are awaited.

**Phase 2** (~1 week) — Jellyfin family proof:

- Annotate Jellyfin-family promises (`jellyfin-running`,
  `jellyfin-api-key-discoverable`, `jellyfin-libraries`) with
  `bootstrap_blocking: true`.
- Remove `phase: post` from `jellyfin:ensure-api-key` job in
  `contracts/services/jellyfin.yaml` (so bootstrap stops scheduling
  it directly; orchestrator dispatches it via the promise).
- Live test: fresh `compose down -v && up`. Verify bootstrap
  completes within timeout, jellyfin is fully configured, no
  duplicate ensurer fires.
- Soak window: 24h on compose + k8s.

**Phase 3** (~1-2 weeks) — Servarr family + remaining:

- Same as Phase 2 for sonarr/radarr/lidarr/readarr/prowlarr +
  qbit/sab/bazarr/jellyseerr/maintainerr.
- Per-family soak (24h each).
- Audit shows `phase_scripts.media_server_bootstrap` is no longer
  invoked; remove the field from the contract YAMLs (compose
  resolver gracefully handles missing field).

**Phase 4** (~1 week) — `_run_preflights` retirement:

- Audit which jobs still invoke `_run_preflights`.
- For each, either:
  - Remove the call (the orchestrator's promise probe now covers
    the same wait)
  - Reshape the job to gate on `<service>-running` promise being
    `ok` via the orchestrator's state file
- Once no callers remain, delete `_run_preflights` and its handler
  registration.

**Phase 5** (~1 week) — JobRunner internals retirement:

- Audit remaining `_try_satisfy_prereqs` callers. Should be only
  the orchestrator's own JobEnsurer dispatch + manual/cron
  invocations.
- Retire `max_attempts` retry loop in `JobRunner.run()` — the
  orchestrator's cooldown tracker provides the equivalent for
  promise-driven flows; manual/cron callers either don't need
  retry (operator can re-invoke) or get a much simpler retry
  in their wrapper.
- Delete `_try_satisfy_prereqs` if call count reaches zero.

## What this DOES NOT do

- **Doesn't change pre-controller compose hooks.** The
  `compose_preflight_handler` field stays — these run before the
  controller container exists, can't be moved into the orchestrator
  by definition.
- **Doesn't change the auto-heal cycle.** The orchestrator's
  per-60s tick continues; this ADR adds a synchronous bootstrap-
  time invocation alongside it.
- **Doesn't break manual job invocation.** The dashboard's
  "run job" buttons keep working — they go through JobRunner
  directly, same as today.

## Honest cost-benefit

**Cost:**

- ~3-4 weeks of focused work, scattered across Phases 1-5
- One real architectural primitive added (`blocking=True` mode in
  `satisfy_promises`) — needs careful testing for timeout behavior,
  cycle detection, partial-progress reporting
- Per-promise `bootstrap_blocking` annotation across the registry
  (~50 promises to triage)
- Per-service per-family soak windows are necessary; can't rush
- Risk: a bug in `satisfy_promises(blocking=True)` could hang
  bootstrap. Mitigation: hard timeout + clear "completed with
  warnings" failure mode

**Benefit:**

- **Single source of truth for service ensurance** — no more
  duplicate paths between bootstrap and orchestrator
- **ADR-0003 Phase 5e.3+ deletions become safe**:
  `_run_preflights`, `phase_scripts.media_server_bootstrap`, the
  bootstrap-phase ensurer jobs, eventually `_try_satisfy_prereqs`
  and `max_attempts`
- **Promise registry IS the bootstrap plan** — adding a new
  service to the stack means writing its lifecycle + adding
  promises; no separate bootstrap-job authoring needed
- **Fresh-deploy verifier** (ADR-0004) becomes more meaningful —
  it's checking the same code path bootstrap just ran
- **Bug-class eliminated**: "bootstrap declares done but the
  orchestrator's continuous mode finds the same invariant broken"
  — the two pipelines used to be able to disagree; after this ADR
  they're literally the same code

### Why do it

The orchestrator's value is "single source of truth for what should
be true". As long as bootstrap has its own ensurer DAG, that value
is half-realized. ADR-0003 got us to "orchestrator is THE
continuous-mode pipeline"; this ADR finishes the job by making it
THE bootstrap pipeline too.

The longer this ADR is deferred, the more drift accumulates between
the two paths. Each new service or fix has to be authored twice
(once for bootstrap, once for orchestrator) until the
consolidation lands.

## Open questions

1. **`bootstrap_blocking` defaults.** If we annotate every promise
   manually it's ~50 entries. If we infer from `phase: post` /
   `phase: media_server` etc. it's automatic but couples to the
   legacy phase taxonomy. Lean toward inference + per-promise
   override.

2. **Retry semantics during blocking mode.** Today bootstrap retries
   each job up to `max_attempts` (default 3). The orchestrator's
   cooldown is time-based (30s transient / 300s permanent). For a
   blocking 240s window, that's at most 8 transient retries, which
   feels right. Confirm.

3. **Where the synthetic job goes.** Append at end of `phase: post`?
   Or new `phase: orchestrator_satisfy` after `post`? Lean toward
   new phase to avoid intermixing.

4. **Compatibility with existing bootstrap consumers.** The
   `initial_bootstrap_done` flag is read by the dashboard banner +
   onboarding flow. Verify the new pattern still flips it at the
   right moment (after Phase B success or timeout).

5. **Audit-log integration.** Bootstrap currently writes one entry
   to the audit log per phase. Phase B writes per-promise events
   too. Decide reporting shape: roll-up summary vs per-promise
   detail.

6. **Test infrastructure.** End-to-end test that proves
   "fresh-deploy completes via promise-driven bootstrap" needs a
   real container or strong stubs. Existing `verify-fresh-install.sh`
   can be the test harness once ADR-0004 ships.

## Stewardship

Same shape as ADR-0003: directional commitment, phased rollout,
explicit steward approval before each phase. The architectural goal
is "single ensurance pipeline"; the timeline and exact phasing
remain negotiable. Reversibility: at every phase, the previous
state's bootstrap path still works (the legacy ensurer jobs aren't
deleted, just unscheduled, until Phase 4's audit confirms zero
callers).

## Relationship to other ADRs

- **ADR-0003** (service lifecycle + orchestrator): provides the
  primitives this ADR builds on (Promise, ServiceLifecycle,
  satisfy_promises). Phase 5e.3+ deletions become possible only
  after this ADR lands.
- **ADR-0004** (promise-driven verifier): orthogonal but
  complementary. The verifier reads the orchestrator's persisted
  state regardless of whether bootstrap drove it or auto-heal did.
- **ADR-0001 / ADR-0002** (repo / hexagonal restructure): the
  layering rules apply unchanged. The synthetic
  `bootstrap:satisfy-promises` job lives in `application/jobs/`,
  reads from `domain/services/promises.py`, dispatches through
  `application/services/orchestrator.py`.
