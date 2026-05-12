# Media integrity — anti-duplicate-download contract

The media-integrity subsystem exists so non-technical users never see
duplicate-download problems. Its two guardrails run continuously and
silently; only the (rare) indecisive case surfaces in the UI.

## Why this exists

**2026-04-24 production incident.** 14 movies had duplicate files on
the k8s cluster — 27.83 GB of waste. Same for 8 subtitle files on
Bazarr. Root causes:

- Radarr's ``movieFileDeleted`` can fire when the DB unlinks a file
  without a matching disk delete; a subsequent import lands the new
  file alongside the orphan.
- Bazarr's provider cascade can fetch ``.en.srt`` from two providers
  in sequence when the first import fails silently.
- Across every Servarr, ``autoUnmonitorPreviouslyDownloaded*`` gates
  the re-grab loop. Without it, every RSS sweep is a candidate for
  re-downloading content we already have.

The fix is both:
1. **Config enforcement** — apply the canonical policy at boot and
   on a 15-minute tick so no *arr ever runs with drifted settings.
2. **Reconciliation** — walk the inventory and delete loser copies
   using each *arr's own quality score as the tiebreaker.

## Architecture

As of v1.0.184 the subsystem is registered as four native jobs in
the controller's Job framework. The cadence is driven by the
controller's `SchedulerService` (the single existing scheduler in
the codebase); manual SPA triggers route through the same
`run_job(...)` entry-point so every invocation lands in the unified
`GET /api/jobs.history[]` feed. The legacy
`MediaIntegrityScheduler` daemon thread is deprecated and inert in
production wiring (still importable so existing tests load).

```
contracts/services/media_integrity.yaml          (job registration)
contracts/servarr-policy.yaml                    (policy contract)
    │
    ▼
┌──────────────────────────────────────────────────────────────────┐
│  Job framework  (cli/commands/job_framework.py)                  │
│  ┌──────────────────────────────────────────────────────────┐    │
│  │  media-integrity:scan          (every 15 min, scheduled) │    │
│  │  media-integrity:reconcile     (every 6 h,    scheduled) │    │
│  │  media-integrity:enforce-config(daily,        scheduled) │    │
│  │  media-integrity:resolve-review (manual-only)            │    │
│  └────────────────────────┬─────────────────────────────────┘    │
└───────────────────────────┼──────────────────────────────────────┘
                            ▼
┌───────────────────────────────────────┐
│       MediaIntegrityService           │
│   (singleton, held by controller)     │
└──┬──────────────┬──────────────┬──────┘
   │              │              │
   ▼              ▼              ▼
ServarrConfig   MediaIntegrity   Bazarr{Settings,Subtitle}
Enforcer        Reconciler       Enforcer + Reconciler
   │              │                      │
   ▼              ▼                      ▼
Radarr / Sonarr / Lidarr / Readarr adapters    BazarrAdapter
(ArrApp protocol)                              (BazarrApp protocol)
```

### Trigger paths

| Trigger | Path | History entry source |
| --- | --- | --- |
| Cron tick (every 15 m / 6 h / 24 h) | controller `_scheduler_loop` → `action_trigger` → `run_job` | `scheduler` |
| `POST /actions/media-integrity:reconcile` | dispatch → `run_job` | `manual` (with `actor`) |
| `POST /api/media-integrity/reconcile` (legacy) | `_dispatch_media_integrity_via_job` shim → `run_job` | `manual` (with `actor`) |
| Auto-heal loop | `action_trigger("media-integrity:reconcile", {"_source": "auto-heal"})` | `auto-heal` |

Key source locations:

- Job registration: `contracts/services/media_integrity.yaml`
- Job handlers: `src/media_stack/services/media_integrity/job_handlers.py`
- Policy contract: `contracts/servarr-policy.yaml`
- Protocol + domain types: `src/media_stack/services/media_integrity/arr_protocol.py`, `bazarr_protocol.py`
- Adapters: `src/media_stack/services/media_integrity/adapters/`
- Policy loader: `src/media_stack/services/media_integrity/policy.py`
- Enforcer: `src/media_stack/services/media_integrity/enforcer.py`
- Reconciler: `src/media_stack/services/media_integrity/reconciler.py`
- Bazarr: `src/media_stack/services/media_integrity/subtitle_reconciler.py`
- Orchestrator: `src/media_stack/services/media_integrity/service.py`
- Factory (production wiring): `src/media_stack/services/media_integrity/factory.py`
- Scheduler hook (DEPRECATED, no-op in production): `src/media_stack/services/media_integrity/scheduler_hook.py`
- API handler: `src/media_stack/api/services/media_integrity_handlers.py`
- HTTP→Job shim: `_dispatch_media_integrity_via_job` in `src/media_stack/api/handlers_post.py`
- Scheduler seeding: `_scheduler_loop` in `src/media_stack/cli/commands/controller_serve.py`
- UI route: `/media-integrity` in the React SPA (`ui/src/routes/media-integrity.tsx`).
  See [ui-design-system.md](../reference/ui-design-system.md) for the component
  catalog and [ui-container.md](ui-container.md) for how the bundle is
  served.

## Policy contract

`contracts/servarr-policy.yaml` is the single source of truth. Every
value is pinned by `tests/unit/test_servarr_policy_contract_ratchet.py`
— changing any line requires editing the ratchet test in the same
commit, which means the change gets code-reviewed against a named
failure mode.

Canonical keys (Servarr):

| Key | Prevents |
|---|---|
| `auto_unmonitor_previously_downloaded` | re-grab loops |
| `use_hardlinks` | orphan files on torrent delete |
| `delete_empty_folders` | reconciler ambiguity on empty releases |
| `unmonitor_deleted` | "delete bad file → grab another copy" cycles |
| `rename_files` | ``movieFileDeleted`` desync incidents |
| `create_empty_media_folders` | false-positive "zero releases" checks |
| `skip_free_space_check` + `minimum_free_space_mb` | truncated imports |
| `import_extra_files` + `extra_file_extensions` | second subtitle-download pass |

Canonical keys (Bazarr):

| Key | Prevents |
|---|---|
| `rename_files` | stale sub filename alongside canonical video |
| `auto_sync` | subtitle download firing after video import |
| `upgrade_allowed` | second download pass for a good subtitle |
| `ignore_deleted` | re-download after user manually removed a sub |

### Quality cutoff policy

`quality.cutoff: "WEBDL-1080p"` is the tier AT WHICH upgrades stop:

- Library has HDTV-1080p → keep searching for WEBDL-1080p or better.
- Once WEBDL-1080p or better exists → stop searching.

This is deliberately stricter than the cluster's pre-incident value
(HDTV-1080p), which allowed any 1080p variant to replace any other.
That laxity contributed to the Spider-Man duplicate incident.

## API

All paths are under `/api/media-integrity/`.

### `GET /api/media-integrity/status`

Authenticated — not admin-only. Returns the last pass outcomes:

```json
{
  "last_enforce": { "ts": "2026-04-24T12:00:00Z", "detail": { ... } },
  "last_reconcile": { "ts": "2026-04-24T12:15:00Z", "detail": { ... } },
  "policy_version": 1,
  "servarr_adapters": ["radarr", "sonarr"],
  "bazarr_present": true
}
```

### `POST /api/media-integrity/reconcile`

Admin-only. Walks every adapter, heals duplicate files, emits
`media_integrity.duplicate_resolved` / `media_integrity.duplicate_review_needed`
events. Idempotent — a repeat call after a clean sweep is a no-op.

### `POST /api/media-integrity/enforce-config`

Admin-only. Brings every adapter's `/config/mediamanagement`,
`/config/naming`, and (for Bazarr) `/api/system/settings` back into
compliance with the canonical YAML. Failures on one adapter do not
stop enforcement on the others.

## UI surface

The `/media-integrity` route in the SPA is the operator-facing tab. As
of UI v1.1.0 it composes:

- **StatusOverview** — animated bytes-counter cards for last-enforce
  and last-reconcile outcomes.
- **AdapterTable** — per-adapter status (TanStack Table on `>=md`,
  card-list fallback on mobile).
- **NeedsReviewPanel** — duplicate-review items with optimistic
  resolve via TanStack Query mutations; this is the only place a
  `media_integrity.duplicate_review_needed` event surfaces in the UI.
- **ReconcileButton** / **EnforceButton** — primary actions wired to
  `POST /api/media-integrity/reconcile` and
  `POST /api/media-integrity/enforce-config`. Settle into a Sonner
  toast.
- **ProgressBar** — shimmer animation while a pass is in flight;
  respects `prefers-reduced-motion`.

Component vocabulary lives in [ui-design-system.md](../reference/ui-design-system.md).

## Scheduler

As of v1.0.184 cadence is driven by the controller's
`SchedulerService` + the Job framework — no dedicated daemon thread.
Four jobs are registered via `contracts/services/media_integrity.yaml`:

| Job name | Cadence (≈cron) | Purpose |
| --- | --- | --- |
| `media-integrity:scan` | every 15 min (`*/15 * * * *`) | cheap status snapshot for the dashboard card |
| `media-integrity:reconcile` | every 6 h (`0 */6 * * *`) | full duplicate reconcile across every *arr + Bazarr |
| `media-integrity:enforce-config` | daily (`0 4 * * *`) | apply canonical *arr + Bazarr policy; idempotent |
| `media-integrity:resolve-review` | manual-only | apply operator-resolved review queue items |

Schedules are seeded by `_scheduler_loop` in
`cli/commands/controller_serve.py` (the same place that seeds
`run-media-hygiene`, `recover-stuck-imports`, etc.). The
`SchedulerService` operates on `interval_seconds`; the equivalences
above are the contract — clock-aligned cron expressions aren't
supported by the existing scheduler.

`MediaIntegrityScheduler` (the legacy daemon thread) is **deprecated**.
It still imports cleanly so existing test fixtures load, but
production wiring no longer constructs or starts it. Instantiating it
emits a `DeprecationWarning`.

Removed env-var tunables (no replacement; the framework owns cadence
now):

- `MEDIA_INTEGRITY_BOOT_DELAY_SEC`
- `MEDIA_INTEGRITY_RECONCILE_INTERVAL_SEC`
- `MEDIA_INTEGRITY_ENFORCE_AT_BOOT`
- `MEDIA_INTEGRITY_ENFORCE_EACH_TICK`

## Winner-picking rules

### Servarr (files per release)

When a release has ≥ 2 files, the reconciler keeps the one with:

1. **Highest** `adapter.quality_score(file)` (Servarr's own quality
   profile ordering).
2. On tie: **earliest** `added_at`.
3. On further tie: **smallest** `size`.

Total tie → emit `MediaIntegrityDuplicateReviewNeeded`, leave both
files in place, surface as a row in the **NeedsReviewPanel** on the
`/media-integrity` route.

### Bazarr (subtitles per release)

Subtitles are grouped by **(release_id, release_kind, language,
forced, hi)**. `en.srt` + `en.forced.srt` are **NOT** duplicates.
`en.srt` + `en.srt` (different providers) **ARE** duplicates.

Within a group the same 3-rule picking order applies, using Bazarr's
own subtitle score instead of the Servarr quality score.

## Hardlink safety

The policy sets `use_hardlinks: true` across every Servarr. Deleting
a library file does NOT delete the torrent's copy — only the inode
reference count drops. This is what makes reconciliation safe under
the canonical policy.

Deployment parity requirement: the download client and library
volumes MUST be on the same filesystem. Enforced by the Compose + k8s
layouts (see [deploy-parity.md](deploy-parity.md)).

## Observability

### Events (on the in-process bus)

- `media_integrity.config_enforced` — per-adapter, per-pass
- `media_integrity.config_enforce_failed` — per-adapter, per-section
- `media_integrity.duplicate_resolved` — silent success; no user UI
- `media_integrity.duplicate_review_needed` — the ONLY UI signal
- `media_integrity.reconcile_failed` — per-release or per-adapter

### Audit log entries

- `media_integrity_config_enforced`
- `media_integrity_config_enforce_failed`
- `media_integrity_duplicate_resolved`
- `media_integrity_duplicate_review_needed`
- `media_integrity_reconcile_failed`

The audit log is hash-chained (see [security.md](security.md)); these
entries carry the same tamper-evidence as session + auth events.

### Secret redaction

Error messages from adapters go through `_redact()` (in enforcer.py +
reconciler.py + subtitle_reconciler.py + factory.py) before hitting
audit or bus. The scrub handles:

- `apikey=...` / `api_key=...` / `x-api-key=...` query and header forms.
- Any 32+ hex-char run (matches the shape of Servarr API keys).
- Truncation to 500 chars to prevent runaway error bodies.

## Runbook

### Duplicate keeps reappearing

The reconciler ran but the *arr re-downloaded. Symptom: the Security
tab shows `duplicate_resolved` entries for the same release on
consecutive passes.

**Diagnosis**: `auto_unmonitor_previously_downloaded` is false on
that *arr. The enforcer's next tick will fix it; to force now:

    POST /api/media-integrity/enforce-config

### A NeedsReviewPanel row shows up

The reconciler found a genuine tie (same quality, same added_at,
same size). Open `/media-integrity`, expand the row in the
**NeedsReviewPanel** to inspect the candidate paths, pick one via the
*arr's web UI, then click **Reconcile** (or POST
`/api/media-integrity/reconcile` directly). The optimistic mutation in
the panel clears the row on success and rolls back on failure.

### Bazarr subtitle dupe cluster

If the Bazarr section of the status shows persistent dupes, check the
provider cascade. Likely fix is in the policy's `auto_sync` and
`upgrade_allowed` flags — both should be true.

### Contract change required

Update `contracts/servarr-policy.yaml` AND
`tests/unit/test_servarr_policy_contract_ratchet.py` in the same
commit. The ratchet test documents **why**:

> If you change ANY line below, include in the commit message which
> prod failure mode the change addresses.

Reviewers should push back on policy changes with no named incident.

## Adding a new *arr

Current Servarr support: Radarr/Sonarr/Lidarr/Readarr + Bazarr. To add
a future *arr (e.g., Whisparr, Prowlarr-with-policy-surface):

1. Add a module under `src/media_stack/services/media_integrity/adapters/`.
2. Subclass `_ServarrBaseAdapter` and set the class attributes:
   `name`, `api_version`, `_media_file_endpoint`, `_media_endpoint`,
   `_MEDIA_MANAGEMENT_FIELDS`, `_NAMING_FIELDS`.
3. Implement `list_releases`, `_list_files_for`, `_release_from_raw`,
   `_file_from_raw`.
4. Add to `factory._SERVARR_ADAPTERS`.
5. Extend `test_servarr_policy_contract_ratchet.py::ServarrAdapterFieldMapRatchet`
   with the new class in the `cases` list — the pattern ratchet will
   catch any copy-paste typos in the field map.
6. Add a service contract under `contracts/services/`.

The protocol is runtime-checkable, so `isinstance(adapter, ArrApp)`
verifies shape conformance in tests.
