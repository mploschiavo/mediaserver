# ADR-0008 — Disk-pressure guardrails: state machine, manual controls, and UI

**Status:** Draft (2026-05-05). Operational hardening; not architecturally
load-bearing. The current `disk_guardrails` config exists but is silently
no-op'd on compose (broken `monitor_path`), can't be tuned per-install
without forking contracts, and has no manual override surface — operators
can only watch the auto-cleanup decide for itself, not invoke or block
actions. This ADR formalizes the four fixes surfaced during a
laptop-deploy live-soak (2026-05-05): broken probe, missing profile
overrides, no manual controls, and no UI surface.

## Context

The media stack writes to disk continuously: qBittorrent stages torrents
to `/data/torrents/`, Sonarr/Radarr import to `/media/...`, Jellyfin
transcodes to `/data/transcode/`. On a sub-500GB laptop a fresh install
fills 60GB+ within a few hours of seeding indexers. Without a working
guardrail the operator finds out when the OS reports `ENOSPC`.

A guardrail framework exists today in
`contracts/defaults/operations.yaml` (`disk_guardrails:` block plus
`media_hygiene:` block) and
`src/media_stack/adapters/compose/services/container_runtime.py`
(`disk_allocation_gb` budget check). What it does NOT have:

1. **Working probe path on compose.** `monitor_path: /srv-stack` resolves
   to a non-existent path inside the controller container on compose
   (`/srv-config` is the actual mount). The threshold check has been
   reading `df` of a missing dir and silently bailing — the guardrail
   has been off in practice for an unknown number of releases.

2. **No per-install threshold tuning.** Defaults at 65% / 58% are
   reasonable on a 50TB NAS and dangerous on a 467GB laptop. The only
   override surface is editing
   `contracts/defaults/operations.yaml` directly, which contaminates
   the contracts dir with per-install state.

3. **No manual operator surface.** An operator who wants to invoke
   cleanup on demand, lock down all downloads while ripping a Blu-ray,
   or release an auto-engaged lockdown early has no API or UI for any
   of it. The dashboard shows usage but offers no actions.

4. **One-tier policy.** Existing `disk_guardrails` only reacts at one
   threshold (`max_used_percent` triggers cleanup). There's no
   tier-2 "stop new content from arriving" lockdown when cleanup
   alone can't keep up — the operator's options are watch the disk
   fill or pull the plug.

The 2026-05-05 live-soak on a 467GB laptop made all four gaps visible
within an hour. This ADR specifies the durable fix.

## Decision

Adopt a **state machine with AUTO and MANUAL triggers**, where the
thresholds gate the auto-trigger but every state transition is also
invokable ad-hoc by the operator.

### Four states

```
NORMAL       used < watch_percent           no action
WATCH        watch <= used < cleanup        UI banner; logging; no behavior change
CLEANUP      cleanup <= used < lockdown     existing qbit_cleanup runs; arr-failed-queue
                                            cleanup runs; auto-grabs still allowed
LOCKDOWN     used >= lockdown_percent       all download clients paused; arr RSS sync
                                            disabled; cleanup keeps running
```

Default thresholds: `watch=50` / `cleanup=65` / `lockdown=75` /
`release=60`. The 15% gap between `lockdown` (entry) and `release`
(exit) provides hysteresis so the system can't flap.

### AUTO vs MANUAL trigger separation

The same backing actions (`pause_all_downloads`, `release_all_downloads`,
`run_cleanup_now`) are wired to two trigger paths:

* **AUTO**: the orchestrator's 60s tick reads `df`, computes
  `used_percent`, transitions state per the threshold table. AUTO
  states release automatically when disk recovers.
* **MANUAL**: explicit operator action via dashboard button or API
  call. MANUAL states do NOT auto-release — the operator is in
  control until they explicitly release.

Distinguished by `state` value:

```
NORMAL | WATCH | CLEANUP | AUTO_LOCKDOWN | MANUAL_LOCKDOWN
```

### Five ad-hoc actions, all valid at any time

| Action | API | Effect | Valid when |
|---|---|---|---|
| Run cleanup now | `POST /api/disk-guardrails/cleanup` | Synchronous qbit_cleanup; returns `{deleted, freed_gb}` | always |
| Engage lockdown | `POST /api/disk-guardrails/lockdown` | Pauses every client; state → `MANUAL_LOCKDOWN`; persists | always |
| Release lockdown | `POST /api/disk-guardrails/release` | Resumes everything; state → `NORMAL`; next tick re-evaluates | state in {AUTO_LOCKDOWN, MANUAL_LOCKDOWN} |
| Pause guardrails (TTL) | `POST /api/disk-guardrails/pause-auto?hours=1` | Auto-threshold loop becomes no-op for N hours; current state stays | always |
| Force evaluate now | `POST /api/disk-guardrails/evaluate` | Force one immediate probe + transition (for "I just freed 50GB") | always |

A `GET /api/disk-guardrails` returns `{state, used_percent, thresholds,
engaged_at, engaged_by, paused_clients, transitions[]}` for the UI.

### Trigger-interaction rules

These rules cover every corner case so the state machine can never
deadlock or drift:

* **Operator engages lockdown at 30% disk** → `MANUAL_LOCKDOWN`.
  Auto-thresholds keep evaluating but they cannot auto-release a
  manual lockdown. Stays paused until operator releases.
* **State is `AUTO_LOCKDOWN`, operator clicks Engage** → upgrades to
  `MANUAL_LOCKDOWN`. Auto-release at the recovery threshold no longer
  fires. Operator deliberately wants it locked.
* **State is `MANUAL_LOCKDOWN`, operator clicks Release** → state
  becomes `NORMAL`. Next tick re-evaluates: if disk is still over
  the lockdown threshold, the auto-system immediately re-engages as
  `AUTO_LOCKDOWN`. Won't loop because `AUTO_LOCKDOWN`'s release at
  the recovery threshold fires normally once disk drops.
* **State is `AUTO_LOCKDOWN`, operator clicks Release** → forces
  release. If disk is still over `lockdown_percent`, the next tick
  re-engages it. Useful for "let it through for 30 seconds while
  I cancel something."
* **Pause guardrails 1h** → `auto_check_paused_until` is set to
  `now + 3600`. The threshold check becomes a no-op for 1 hour.
  Already-paused clients stay paused. After 1h, normal evaluation
  resumes. The TTL is extended by clicking again or cancelled by
  clicking "Resume guardrails."

### Three-tier configuration override

Profile-level `disk_guardrails:` block overrides
`contracts/defaults/operations.yaml`. UI-saved values override the
profile. Effective config is the merged result, top-down:

```
contracts/defaults/operations.yaml      (built-in defaults, lowest priority)
   ↓ merge
deploy/examples/bootstrap-profiles/<profile>.yaml   (per-platform tuning)
   ↓ merge
/srv-config/.controller/disk-guardrails.yaml        (UI-edited overrides, highest)
```

Profile schema gains a `disk_guardrails:` block with the same shape as
the operations.yaml defaults (selective override, not full replacement).

### Persistence and restart-safety

State lives at `/srv-config/.controller/disk-guardrails.state.json`:

```
{
  "state": "MANUAL_LOCKDOWN",
  "engaged_at": 1746400000.0,
  "engaged_by": "operator:matthew",
  "auto_check_paused_until": null,
  "paused_clients": ["qbittorrent", "sabnzbd", "sonarr", "radarr"],
  "last_transitions": [...]
}
```

After a controller restart mid-lockdown, the state file is loaded; each
client in `paused_clients` is re-confirmed paused (idempotent re-pause
is a no-op); the UI shows the same state. Manual flag survives
restarts.

### Audit-logged transitions

Every state change writes a row to the existing audit log:

```
disk_guardrail_lockdown_engaged    actor=operator:matthew   detail={used_percent: 62, manual: true}
disk_guardrail_cleanup_invoked     actor=operator:matthew   detail={deleted: 14, freed_gb: 32.5}
disk_guardrail_lockdown_released   actor=auto               detail={used_percent: 59}
```

The transition feed in the UI reads from this audit stream.

## Phases

### Phase 1 — Foundations (probe fix + override schema + laptop tuning)

No behavior change, no UI; pure correctness + tuning.

* Fix `monitor_path` resolution: at startup, validate the configured
  path exists. If not, fall back in priority order (`/srv-config` →
  `/srv-stack` → `/`) and log a `[WARN]` line citing the configured
  vs effective path.
* Add a runtime invariants ratchet
  `disk_guardrails_monitor_path_resolves` that fails fast at boot if
  the effective path doesn't resolve (catches future regressions).
* Add `disk_guardrails:` block to the profile schema. Write the
  three-tier merge in `services/profile_config.py` (or wherever the
  profile→effective-config translation lives today).
* Update `media-compose-standard.yaml` with laptop-tuned values:
  `monitor_path: /srv-config`, `max_used_percent: 50`,
  `target_used_percent: 40`, `lockdown_threshold: 75`,
  `lockdown_release_threshold: 60`.
* Smoke-test on compose: log line confirming the resolved path, no
  silent NXDOMAIN-style failure.

**Deliverables**: 4 source-file edits, 1 new ratchet test, 1 profile
update. Net behavior: existing single-tier cleanup actually fires
(was silently off); no new actions or UI yet.

### Phase 2 — State machine and lockdown service

The core architecture lift. Behind-the-scenes wiring; UI lives in
Phase 3.

* New module `src/media_stack/api/services/disk_guardrails_service.py`:
  * State machine implementation (5 states, AUTO + MANUAL trigger
    separation, the interaction rules above).
  * Persistence: load + save state file.
  * Audit-log integration via the existing audit chain.
* New module `src/media_stack/api/services/download_lockdown_service.py`:
  * Per-client pause/resume actions (qBittorrent, SABnzbd, Sonarr,
    Radarr, Lidarr, Readarr, Prowlarr).
  * Idempotent: re-engaging when already engaged is a no-op; same
    for re-release.
  * Per-client failure isolation: a failed pause on one client logs
    `[WARN]` and does not block the other clients.
* New promise `disk-usage-within-budget` in
  `contracts/services/_core.yaml` (cross-cutting block):
  * Probe: read `df` of effective `monitor_path`; pass when
    `used_percent < lockdown_threshold`.
  * Ensurer: when probe fails, call
    `disk_guardrails_service.evaluate_and_transition()` which dispatches
    to `download_lockdown_service.engage()`.
  * Reverse path: when probe passes after being failed AND state is
    `AUTO_LOCKDOWN`, call `release()`. `MANUAL_LOCKDOWN` is unaffected.
* New `RouteModule` `routes/disk_guardrails.py`:
  * `GET /api/disk-guardrails` (status)
  * `POST /api/disk-guardrails/cleanup`
  * `POST /api/disk-guardrails/lockdown`
  * `POST /api/disk-guardrails/release`
  * `POST /api/disk-guardrails/pause-auto?hours=N`
  * `POST /api/disk-guardrails/evaluate`
* OpenAPI spec entries for the 6 endpoints.
* Unit tests covering every interaction rule.

**Deliverables**: 2 new services, 1 new RouteModule, 1 new promise, 6
new spec entries, ~80 unit tests. Manual control from `curl` works;
auto-system functional at the new thresholds; UI still old.

### Phase 3 — UI surface

Dashboard work; no controller changes.

* New **Ops → Storage** card (or expanded existing storage card) with:
  * Status header: usage bar, state badge, "since X minutes ago"
  * Threshold inputs (4 sliders or numeric inputs): `WATCH` /
    `CLEANUP` / `LOCKDOWN` / `RELEASE`. Save button persists to
    `disk-guardrails.yaml`.
  * Action buttons: `[Run cleanup now]` `[Engage lockdown]`
    `[Release lockdown]` `[Pause guardrails 1h]` `[Force evaluate]`.
    Confirmation dialogs on the destructive ones.
  * Cleanup-policy section (collapsed by default): per-category
    seeding/age/ratio knobs.
  * Audit-style transition log: last 20 state changes, with actor
    column distinguishing AUTO from operator-by-name.
* SSE-fed live updates: when state changes server-side, dashboard
  badge updates without a page refresh. (Hooks into the existing
  `EventBus`.)
* Tests: rendering, action wiring, threshold-validation regex (range
  + ordering: `watch <= cleanup <= lockdown`).

**Deliverables**: 1 new feature dir under `ui/src/features/storage/`,
6 new query/mutation hooks, ~30 component tests. Operator can do
everything from the browser.

### Phase 4 — Cleanup + retire

Once Phase 1-3 are in production for ~2 weeks and stable:

* Document the legacy `media_hygiene.required` flag's interaction
  with the new state machine; if no observable difference, mark
  deprecated and retire next minor.
* Decide whether to fold `media_hygiene.qbit_ipfilter` and
  `media_hygiene.cleanup_arr_failed_queue` into the new
  `disk_guardrails` config block (they're related; today they're
  parallel branches of operations.yaml).
* Optional follow-up (out of scope for this ADR): Maintainerr-style
  library-quota retention for the `/media` tree, separate from
  download-pressure guardrails. Different feature, separate ADR if
  pursued.

## Alternatives considered

### Single tier, threshold + cleanup only

Status quo plus the monitor_path fix. Rejected: when cleanup can't
keep up (e.g., recently-added torrents not yet eligible for deletion
by age/seeding rules), nothing prevents disk fill. The lockdown tier
exists exactly for that case.

### Single threshold for everything (cleanup + lockdown at same %)

Simpler config, but loses the "give cleanup a head start before
locking" pattern. With only one threshold the system either over-
reacts (locking down at 65% when cleanup alone would suffice) or
under-reacts (locking at 80% by which time cleanup is way behind).
The two-tier design lets cleanup work for 10 percentage points
before the heavier hammer comes out.

### Operator-set static lockdown without auto-thresholds

What was originally suggested in conversation. Rejected: requires
the operator to babysit the disk meter or write their own watchdog.
The whole point of a guardrail is to do nothing when nothing's
needed and act when it is.

### Hard-stop downloads at any disk pressure (sledgehammer)

Pause everything when used > 70%, release at 60%. Rejected: too
aggressive. Cleanup alone often resolves the pressure within one or
two ticks; if every spike triggered a full lockdown, operators would
see "downloads stopped" notifications constantly.

### Just exposing the existing `media_hygiene.qbit_cleanup` enable
flag in the UI

Doesn't solve the lockdown gap, doesn't solve the silent-no-op
monitor_path bug, doesn't give operators ad-hoc control. Half-fix.

## Consequences

### Positive

* Disk fill becomes recoverable rather than terminal. The 75%
  threshold + lockdown gives the operator a self-service backstop.
* Operators get the controls they actually need (run cleanup,
  pause downloads, override thresholds) instead of a black-box
  "we'll figure it out" automation.
* `monitor_path` regression is caught at boot by the new ratchet,
  not silently after operators wonder why their disk filled.
* Profile-level overrides remove the "fork the contracts dir to
  tune thresholds" anti-pattern.

### Negative

* New service module + new promise + new route module + new UI
  card + state file + audit-log integration. Phase 2 is the largest
  single-feature commit since ADR-0007 wave 6 (~1500 LoC including
  tests).
* `MANUAL_LOCKDOWN` introduces a state the operator has to remember
  to release. We mitigate via TTL on `pause-auto` and prominent UI
  badge, but the failure mode "operator engaged lockdown weeks ago
  and forgot" is real.
* Audit-log volume increases. Every state transition writes one row.
  At 60s ticks and a flapping system that's at most 1440 rows/day
  but typical operation will be near zero. Acceptable.
* The `disk-usage-within-budget` promise adds one more probe to the
  orchestrator's tick. Probe is cheap (one `statfs` syscall) but it's
  one more entry to count toward future tick-time budgets.

### Neutral

* No data migration. New state file is created on first probe.
* No backwards-compat concerns: the existing `disk_guardrails` config
  block continues to work; new keys are additive.
* Compose and k8s use the same code path. Profile-level overrides
  let each install tune independently.

## Stewardship

Owner: storage / orchestrator subgraph. Reviewed alongside the
existing `media_hygiene` and `qbit_cleanup` paths. New audit-log
fields land in the same audit chain so existing log-reading tools
continue to work.

Rollback: each phase is its own commit. Phase 1 reverts to silent-
no-op (no worse than current). Phase 2 reverts removes the new
endpoints + state machine; the existing `qbit_cleanup` path reverts
to its single-tier behavior. Phase 3 reverts removes the UI card;
the API endpoints stay for `curl` use until Phase 2 is also reverted.

## Relationship to other ADRs

* **ADR-0003 (orchestrator)**: this ADR's `disk-usage-within-budget`
  promise is registered through the same orchestrator framework as
  every other service-lifecycle promise. The state machine lives
  outside the orchestrator (operator-facing concept) but its
  AUTO trigger fires off the orchestrator's 60s tick.
* **ADR-0006 (per-service promise registries)**: the new promise
  lives in the cross-cutting registry (`contracts/services/_core.yaml`)
  alongside other stack-wide concerns.
* **ADR-0007 (OpenAPI-driven routing)**: the 6 new endpoints are
  registered as `RouteModule` subclasses per the standard pattern;
  the OpenAPI spec gains 6 new entries.
* **Out of scope**: Maintainerr-style library retention (different
  domain — pruning watched/old content from /media), Docker build-
  cache pruning (CI/CD concern, not runtime), per-app size quotas
  (Sonarr-side feature). Each is its own potential ADR.
