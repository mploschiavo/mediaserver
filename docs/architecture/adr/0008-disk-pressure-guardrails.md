# ADR-0008 — Disk-pressure guardrails: lockdown + manual controls + UI

**Status:** Draft (2026-05-05). Operational hardening. Adds the **lockdown
tier** and the **manual-action surface** to an already-substantial
guardrail framework that the codebase already ships. This ADR explicitly
INTEGRATES with `application.guardrails.GuardrailRegistry` and the legacy
`services.disk_guardrails_service.DiskGuardrailsService` rather than
inventing a parallel system.

## Context

A 2026-05-05 laptop live-soak surfaced an inability to stop new
content from arriving when disk pressure spikes faster than cleanup
can keep up. Initial inspection suggested four bugs; **a careful read
of the existing code shows three of them are already solved** and only
one is genuinely missing.

### What already exists in the codebase (DO NOT reinvent)

`src/media_stack/application/guardrails/` — a full **`GuardrailRegistry`**
framework:

* Decorator-based rule registration (`@register_guardrail`) on a
  process-wide singleton.
* Per-rule operator overrides persisted atomically to
  `/srv-config/.controller/guardrails.json` (the `_override_path()`
  resolver honors `CONFIG_ROOT`).
* `evaluate_all()` returns `Trigger` objects sorted by severity;
  `remediate_all()` returns `Action` objects.
* History ring (last 20 evaluations per rule), consecutive-tick
  streak tracking, severity-monotonic sorting.
* `tick()` in `services/guardrails/evaluation_loop.py` runs one
  evaluation cycle and writes to the existing job-history table
  with `actor="auto-heal"` so guardrail fires render alongside
  cron + manual jobs in the dashboard's job-history view.
* Eight domain modules already split out:
  `auth, bandwidth, cost, dependency, external_api, job_health,
  media_quality, storage`.

`src/media_stack/application/guardrails/domains/storage.py` — seven
storage rules already registered:

* `_PerMountThreshold` — per-mount used-percent thresholds with
  per-mount overrides; emits `qbit_cleanup` Action on breach.
* `_FreeSpaceFloor` — absolute byte floor; works on big volumes
  where percent-used is misleading.
* `_PerContentTypeQuota` — operator-defined GB ceilings for
  movies/tv/music/books folders. **This is the closest thing the
  codebase has to "library retention"** — `_PerContentTypeQuota`
  detects breach; Maintainerr's rules engine does the actual
  pruning (controller wires the integration via
  `adapters/maintainerr/rules_wiring.MaintainerrCollectionsWirer`,
  ADR-0005 Phase 3).
* `_InodeFloor` — `os.statvfs`-driven inode-pressure rule. State
  collector at `state_collector.py:111-130` already calls
  `os.statvfs` and populates `state["mount_inodes"]`.
* `_UnpackerScratchFloor` — Unpackerr scratch-headroom rule
  (related to "orphan/incomplete" file detection).
* `_TrashRetention` — arr recycle-bin age cap.
* `_SnapshotRetention` — snapshot age cap.

`src/media_stack/services/disk_guardrails_service.py` — legacy
210-line `DiskGuardrailsService` with `enforce()` entry point:

* **Already has the monitor_path fallback chain** (lines 56-80):
  if the configured `monitor_path` doesn't exist, walks 9 candidate
  paths (`DISK_GUARDRAILS_MONITOR_PATH` env, `STACK_ROOT` env,
  `/srv-stack`, `/srv-stack/media`, `/srv-stack/data`, `/srv-stack/data/torrents`,
  `/srv-stack/data/usenet`, `MEDIA_ROOT` env, `DATA_ROOT` env, `config_root`)
  and uses the first that exists. **Also already emits the WARN line**
  on a missing configured path.
* `qbit_cleanup` candidate selection by category, age, ratio,
  seeding-time. Sort order is `(completion_on, size)` ascending —
  effectively FIFO by completion timestamp. Wired into
  `infrastructure/servarr/runtime/hygiene_ops.py:170` as the
  `media-hygiene` job's cleanup action.
* The monitor_path is `compose-standard.yaml`'s `/srv-stack` literal;
  it doesn't exist on compose, so the WARN fires and the fallback
  chain takes over — silently using `config_root` (`/srv-config`)
  in practice. Behavior IS happening; we just weren't reading the
  log.

`adapters/maintainerr/rules_wiring.py::MaintainerrCollectionsWirer`
— full Maintainerr integration (ADR-0005 Phase 3):

* Probes `/app/maintainerr/api/collections` for non-None
  `radarrSettingsId` / `sonarrSettingsId` linkage.
* Heavy-handler delegation to
  `MaintainerrService.ensure_integrations` for the actual
  rule sync.
* Operator-configured retention rules in Maintainerr's UI drive
  the actual /media pruning. **This is the answer for library
  retention; we DO NOT reinvent it.**

### What is genuinely missing

After the audit, only one of the originally-suggested bugs is real,
and three architectural gaps remain:

| Originally proposed | Actual status |
|---|---|
| Fix `monitor_path` silent no-op | **Already fixed** (services/disk_guardrails_service.py:56-80). The 9-candidate fallback chain handles `/srv-stack`-missing on compose. |
| Profile-level `disk_guardrails:` override schema | **Partially exists**: `GuardrailRegistry` has per-rule overrides in `guardrails.json`. Legacy `DiskGuardrailsService` reads from `cfg["disk_guardrails"]` which feeds from `media-stack.config.json`. Both surfaces exist; profile-level layering is the only gap. |
| Inode-pressure monitoring | **Already exists** as `_InodeFloor` rule. |
| Library retention | **Maintainerr handles this**, plus `_PerContentTypeQuota` covers the detection side. |
| **Lockdown tier** ("stop new content from arriving") | **Genuinely missing**. No rule, no service, no API. |
| **AUTO + MANUAL trigger separation** | **Genuinely missing**. The Registry's `tick()` is auto-only. No code path for "operator clicks Engage Lockdown at 30% disk". |
| **Manual API endpoints** | **Genuinely missing**. No `POST /api/disk-guardrails/{cleanup,lockdown,release,pause-auto,evaluate}`. Today's only path is the auto-heal tick. |
| **Smart cleanup ordering** | **Genuinely missing**. Current sort is FIFO by `(completion_on, size)`; no largest-first or watched-first option. |

## Decision

Add the lockdown layer **on top of** the existing `GuardrailRegistry`,
plus the manual-action surface, plus a small set of cleanup-ordering
options. Three new pieces of code, no parallel system.

### 1. New Registry rule: `storage:lockdown_threshold`

A new rule in `application/guardrails/domains/storage.py` alongside
the seven existing rules:

```
_LockdownThreshold(
    id="storage:lockdown_threshold",
    default_threshold={
        "lockdown_percent": 75.0,
        "release_percent": 60.0,
    },
)
```

* `evaluate(state)`: returns `"critical"` when any monitored mount
  exceeds `lockdown_percent` AND the lockdown isn't already engaged;
  returns `"warning"` if engaged-and-still-over; returns `None`
  otherwise.
* `remediate(state)`: returns `Action(action="lockdown_engage", ...)`
  when newly-critical, or `Action(action="lockdown_release", ...)`
  when previously-engaged AND `state["disk"][label].percent_used` is
  now under `release_percent`.
* Registry persistence and overrides come for free — operator can
  edit thresholds via the existing `update_threshold()` API.

The 15-percentage-point gap between `lockdown_percent` (75) and
`release_percent` (60) is hysteresis — prevents flapping.

### 2. New `DownloadLockdownService` (action implementer)

`src/media_stack/services/download_lockdown_service.py` — a new
service that the registry's remediation phase dispatches to when it
sees `action="lockdown_engage"` or `"lockdown_release"`:

* `engage()` — pause every download client + disable arr RSS sync +
  disable arr download clients. Per-client failure isolation: a
  failed pause logs `[WARN]` and doesn't block the rest. Idempotent
  re-engage is a no-op.
* `release()` — resume everything that was paused. State persisted
  separately (see below) so a release-without-engage is safe.

Per-client adapters live in
`adapters/_shared/download_client_lockdown.py` to keep the per-API-
shape code (qBittorrent, SABnzbd, Sonarr, Radarr, Lidarr, Readarr)
out of the orchestration layer.

### 3. Lockdown state file (persistence + AUTO/MANUAL flag)

`/srv-config/.controller/disk-lockdown.state.json`:

```json
{
  "engaged": true,
  "trigger": "auto",                    /* "auto" or "manual" */
  "engaged_at": 1746460000.0,
  "engaged_by": "auto:disk-78%",        /* or "operator:matthew" */
  "auto_check_paused_until": null,      /* epoch when "pause guardrails" TTL expires */
  "paused_clients": ["qbittorrent", "sabnzbd", "sonarr", "radarr"]
}
```

* AUTO trigger releases automatically when `_LockdownThreshold`'s
  evaluate returns to `None` (disk dropped below `release_percent`).
* MANUAL trigger does NOT auto-release — operator must explicitly
  release via the API. Distinguished by the `trigger` field.
* After controller restart mid-lockdown, the file is loaded; each
  client in `paused_clients` is re-confirmed paused (idempotent).
* `auto_check_paused_until` is a separate TTL bypass for the
  "pause guardrails 1h" use case (rip a Blu-ray without the system
  fighting back).

### 4. Five manual API endpoints

New `RouteModule` `routes/disk_guardrails.py`:

| Endpoint | Effect |
|---|---|
| `GET /api/disk-guardrails` | Return current state, thresholds (from registry), recent transitions, paused_clients |
| `POST /api/disk-guardrails/cleanup` | Run `DiskGuardrailsService.enforce()` synchronously regardless of disk %; returns `{deleted, freed_gb}` |
| `POST /api/disk-guardrails/lockdown` | `DownloadLockdownService.engage()`; trigger=`manual`; persists |
| `POST /api/disk-guardrails/release` | `DownloadLockdownService.release()`; clears state |
| `POST /api/disk-guardrails/pause-auto?hours=N` | Sets `auto_check_paused_until = now + 3600*N` |
| `POST /api/disk-guardrails/evaluate` | Force one immediate `tick()` of the registry (bypasses the 60s cadence) |

OpenAPI spec entries for all six. Auth-gated identical to other
mutating endpoints (CSRF + role check).

### 5. Smart cleanup ordering

Add an `order_strategy` knob to `cfg["disk_guardrails"]["qbit_cleanup"]`:

```yaml
disk_guardrails:
  qbit_cleanup:
    order_strategy: oldest_first   # default = current behavior (FIFO by completion_on)
    # also: largest_first | poor_ratio_first | watched_first
```

`watched_first` reads Jellyfin's `UserData.Played` table via the
existing `media_server_adapters` to weight torrents whose mapped
media files have been finished. Cheap query — single SQLite SELECT
across the playstate table, joined to qbit's tracked hashes by
filename.

The sort lives in `DiskGuardrailsService.enforce()` (currently lines
173-176, FIFO). Strategy chosen via a small dispatch table. Defaults
to `oldest_first` so existing operators see no behavior change.

### 6. Profile-level override schema (small)

The Registry already persists overrides at
`/srv-config/.controller/guardrails.json`. The legacy
`DiskGuardrailsService` reads from `cfg["disk_guardrails"]` which
flows from `media-stack.config.json` (built from profile.yaml at
boot). Two existing surfaces; the gap is per-install **defaults**
in profile.yaml.

Add a `disk_guardrails:` block to the profile schema mirroring the
`operations.yaml` shape, merged in at `media-stack.config.json`
generation time. Three-tier:

```
operations.yaml defaults
   ↓ merge
profile.yaml disk_guardrails:
   ↓ merge
guardrails.json (UI-saved overrides; per-rule)
```

Existing `operations.yaml` shape stays as-is; profile.yaml gets a
new optional block; UI saves still go through the registry's
`update_threshold` path (single source of truth at runtime).

### 7. UI: Ops → Storage card (Phase 3)

A new card on the Ops page:

* **Status header** — usage bar, state badge (`NORMAL` /
  `AT-WATCH` / `CLEANUP-FIRED-RECENTLY` / `AUTO_LOCKDOWN` /
  `MANUAL_LOCKDOWN`), "since X minutes ago", paused-client count.
* **Threshold inputs** — bound to the registry's `_LockdownThreshold`
  rule. Save invokes `update_threshold` which writes
  `guardrails.json`.
* **Action buttons** — `[Run cleanup now]`, `[Engage lockdown]`,
  `[Release lockdown]`, `[Pause guardrails 1h]`, `[Force evaluate]`.
  Confirmation dialogs on destructive actions.
* **Cleanup-policy section** (collapsed) — the existing
  `qbit_cleanup` knobs (categories, min_age, min_ratio,
  min_seeding_time, max_delete_per_run, ordering strategy) bound
  to a new `POST /api/disk-guardrails/cleanup-policy` that writes
  to `media-stack.config.json` overrides.
* **Transition feed** — pulled from the existing job-history table
  filtered to `actor="auto-heal:guardrail:storage:*"` rows.
* **SSE-fed live updates** — when state changes server-side, badge
  + button enabled-state updates without a refresh. Hooks into the
  existing `EventBus`.

## Phases

### Phase 1 — Lockdown rule + state file + DownloadLockdownService

No UI; no new manual endpoints. Adds the auto-only lockdown layer
on top of the existing registry tick.

* New `_LockdownThreshold` rule registered in
  `domains/storage.py` (alongside the seven existing rules).
* New `DownloadLockdownService` with `engage()` / `release()`.
* Per-client adapters in `_shared/download_client_lockdown.py`.
* State file at `/srv-config/.controller/disk-lockdown.state.json`
  with load + save; idempotent re-engage on controller restart.
* Registry's `remediate_all` learns to dispatch
  `action="lockdown_engage"` and `action="lockdown_release"` to the
  service. Existing `Action` protocol unchanged; new action names
  are additive.
* Restore-after-restart test: kill the controller mid-lockdown,
  bring it back, confirm clients are re-paused without operator
  intervention.

**Deliverables**: 1 new rule, 1 new service, 1 new adapter module, 1
state file, ~25 unit tests covering engage/release/restart/idempotency.
Behavior: when disk crosses 75% the system pauses every download
client automatically; releases at 60%. No operator surface yet.

### Phase 2 — Manual API endpoints + AUTO/MANUAL trigger separation

Adds the operator surface on top of Phase 1.

* New `routes/disk_guardrails.py` `RouteModule` with the six
  endpoints listed above.
* OpenAPI spec entries for all six (`contracts/api/openapi.yaml`).
* `trigger` field added to the lockdown state file. Manual lockdown
  does not auto-release; auto-thresholds in `_LockdownThreshold` do
  not transition `trigger="manual"` state to `NORMAL`.
* `auto_check_paused_until` TTL bypass for `pause-auto`.
* Force-evaluate endpoint calls `evaluation_loop.tick()` directly
  for the "I just freed 50GB, release now" use case.
* Smart-cleanup-ordering knob added to
  `DiskGuardrailsService.enforce()` (single dispatch table).

**Deliverables**: 1 new route module, 6 spec entries, ~40 unit
tests covering each interaction-rule corner case from the table
below. Manual control via `curl` works end-to-end.

### Phase 3 — UI Ops → Storage card

Pure dashboard work; no controller changes.

* New feature dir under `ui/src/features/storage/`.
* Status header + threshold inputs + 5 action buttons +
  cleanup-policy section + transition feed.
* SSE subscription on the existing EventBus for state-change
  push-updates.
* React Query hooks for the six API endpoints.
* Component tests for rendering, confirmation-dialog wiring,
  threshold-validation regex (range + ordering).

**Deliverables**: 1 new feature dir, 6 hook files, 1 page route,
~30 component tests.

### Phase 4 — Profile-schema override + Maintainerr cross-link

* Add `disk_guardrails:` block to the profile schema. Wire the
  three-tier merge (operations.yaml → profile → guardrails.json)
  into `media-stack.config.json` generation. Lowest priority of
  the four phases — most operators will set thresholds via the
  UI, not by editing profile.yaml.
* Document the interaction with Maintainerr in
  `docs/operator/storage-guardrails.md`: "lockdown handles
  download-side pressure; Maintainerr handles library-side
  retention; they don't fight each other because lockdown's
  release threshold (60%) is well above the typical breakpoint
  for Maintainerr collection-rule firing."
* Optional follow-up (separate ADR if pursued): notification
  channels (email + webhook for "lockdown engaged" transitions).
  Out of scope here — deserves its own design around stack-wide
  alerting that subsumes audit-log webhooks too.

## Trigger-interaction rules (every corner case the state machine handles)

* **Operator engages lockdown at 30% disk** → state file's
  `trigger="manual"`. `_LockdownThreshold` keeps evaluating but
  cannot transition manual state to release.
* **Auto-engaged, then operator clicks Engage** → upgrades
  `trigger` from `auto` → `manual`. Auto-release at 60% no longer
  fires (manual is sticky).
* **Manual-engaged, operator clicks Release** → state cleared.
  Next registry tick re-evaluates. If disk still over 75%, the
  rule re-engages with `trigger="auto"`. The auto-system's release
  at 60% will fire normally once disk drops.
* **Auto-engaged, operator clicks Release** → forces release. If
  disk still over 75%, next tick re-engages auto. Useful for
  "let it through for 30 seconds while I cancel something."
* **`auto_check_paused_until > now`** → registry's lockdown rule
  evaluation is short-circuited to return `None`. Already-paused
  clients stay paused (we don't auto-release during a TTL bypass —
  unlocking is an explicit operator action). After TTL, normal
  evaluation resumes.
* **Restart mid-lockdown** → state file loaded, paused clients
  re-confirmed (idempotent), UI shows the same state.
* **Single download client API fails during engage** → log `[WARN]`,
  record in `paused_clients` only the successes, continue with the
  rest. Next tick retries the failed client (engage is idempotent).
* **Maintainerr fires a collection-rule deletion during lockdown**
  → no conflict. Lockdown pauses incoming; Maintainerr deletes
  outgoing. Both contribute to disk recovery.

## Audit-logged transitions

Every state transition writes a row via the existing audit chain:

```
disk_guardrail_lockdown_engaged    actor=operator:matthew  detail={used_percent: 62, trigger: manual}
disk_guardrail_cleanup_invoked     actor=operator:matthew  detail={deleted: 14, freed_gb: 32.5, strategy: oldest_first}
disk_guardrail_lockdown_released   actor=auto              detail={used_percent: 59, trigger_was: auto}
```

The transition feed in the UI reads from the same audit stream
that powers the existing health-stories panel.

## Alternatives considered

### Stand up a new parallel system instead of integrating with `GuardrailRegistry`

Rejected. The Registry already provides everything we need (rule
registration, override persistence, history tracking, evaluation
loop, action dispatch, dashboard hooks). A parallel system would
duplicate ~400 LoC of well-tested infrastructure and create a second
"is this rule firing?" answer surface. The integration cost is one
new rule plus one new action name in the existing protocol.

### Single threshold for cleanup + lockdown (one knob, simpler config)

Rejected. The existing `_PerMountThreshold` (cleanup at 75%) and
the new `_LockdownThreshold` (lockdown at 75%) target different
actions: cleanup deletes, lockdown pauses. Conflating them either
over-reacts (locking down at 65% when cleanup alone would suffice)
or under-reacts (locking at 80% by which time cleanup is way
behind). The two-tier design lets cleanup work for 10 percentage
points before the heavier hammer comes out.

### Operator-set static lockdown without auto thresholds

Originally suggested in conversation. Rejected: requires the
operator to babysit the disk meter or write their own watchdog.
The whole point of a guardrail is to do nothing when nothing's
needed and act when it is. The MANUAL trigger preserves the
operator's ability to lock independently — they don't need to
sacrifice automation to get manual control.

### Hard-stop downloads at any disk pressure (sledgehammer)

Pause everything when used > 70%, release at 60%. Rejected: too
aggressive. Cleanup alone often resolves the pressure within one
or two ticks; if every spike triggered a full lockdown, operators
would see "downloads stopped" notifications constantly.

### Just expose the existing `_PerContentTypeQuota` enable flag

Doesn't solve the lockdown gap, doesn't give operators ad-hoc
control, doesn't address the FIFO cleanup-ordering limitation. The
existing rule fires `Action(action="notify")` only — it doesn't
actually pause anything. Half-fix.

## Consequences

### Positive

* Disk fill becomes recoverable rather than terminal. The 75%
  threshold + lockdown gives the operator a self-service backstop.
* Operators get the controls they actually need (run cleanup,
  pause downloads, override thresholds) instead of a black-box
  "we'll figure it out" automation.
* Single source of truth for guardrail config — the Registry's
  override file. No parallel state stores to keep coherent.
* Maintainerr stays the answer for library retention; this ADR
  doesn't compete with it.

### Negative

* New service module + new rule + new route module + new UI card.
  Phase 1 + Phase 2 combined are ~600 LoC of code + ~80 unit
  tests. Phase 3 UI adds another ~300 LoC.
* `MANUAL_LOCKDOWN` introduces a state the operator has to
  remember to release. Mitigated via the dashboard's prominent
  state badge and the `pause-auto` TTL for short-lived overrides.
* One more action name in the `Action` protocol. Existing
  consumers (notification adapters, audit-log writers) need to
  recognize `lockdown_engage` / `lockdown_release` as valid
  actions. Backward-compatible additions.

### Neutral

* The existing seven storage rules continue to work unchanged.
  This ADR only adds an eighth.
* The existing `DiskGuardrailsService.enforce()` continues to be
  the cleanup engine. This ADR adds an `order_strategy` knob and
  a manual invocation endpoint; the rest of the implementation
  stays.
* Compose and k8s use the same code path. Profile-level overrides
  let each install tune independently.

## Stewardship

Owner: storage / orchestrator subgraph. Reviewed alongside the
existing `media_hygiene` and `qbit_cleanup` paths, the
`GuardrailRegistry` infrastructure, and the Maintainerr integration.
New audit-log fields land in the same audit chain; existing
log-reading tools continue to work without modification.

Rollback: each phase is its own commit. Phase 1 reverts removes the
lockdown rule + service; the existing seven rules + cleanup engine
revert to today's behavior. Phase 2 reverts removes the manual
endpoints + AUTO/MANUAL trigger separation. Phase 3 reverts removes
the UI card; the API endpoints stay for `curl` use until Phase 2 is
also reverted.

## Relationship to other ADRs

* **ADR-0003 (orchestrator)**: this ADR's `_LockdownThreshold` rule
  is registered through the same `GuardrailRegistry` framework as
  every other guardrail. The auto-trigger fires off the same
  `evaluation_loop.tick()` the existing rules use.
* **ADR-0005 (orchestrator-driven bootstrap)**: the
  `DownloadLockdownService` engage/release uses the same
  download-client API surface (qBittorrent / SABnzbd / arr) the
  existing wirers use. No new HTTP shapes; reuses
  `categories_wiring`, `qbittorrent/admin_ops`, etc.
* **ADR-0006 (per-service promise registries)**: the lockdown rule
  is registered in `application/guardrails/domains/storage.py` —
  the cross-cutting domain registry, not a per-service contract
  YAML. The shape matches the existing seven storage rules.
* **ADR-0007 (OpenAPI-driven routing)**: the six new endpoints are
  registered as `RouteModule` subclasses per the standard pattern;
  the OpenAPI spec gains six new entries.
* **Maintainerr integration** (ADR-0005 Phase 3): unchanged.
  `MaintainerrCollectionsWirer` continues to be the answer for
  library-side retention. The new lockdown handles only the
  download-arrival side.
* **Out of scope** (each its own potential ADR if pursued):
  notification channels (email/webhook for state transitions —
  belongs in a stack-wide alerting ADR), Docker build-cache
  pruning (CI/CD concern, not runtime), per-app size quotas
  (Sonarr-side feature).
