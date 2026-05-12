# Disk-pressure guardrails — operator guide

Last updated: ADR-0008 Phase 4 (2026-05-05).

This guide explains the **disk-pressure guardrails** the controller
runs on every stack — what they do, when an operator should reach
for the manual surface, and how the lockdown layer interacts with
Maintainerr's library retention.

The architectural background lives in
[ADR-0008](../architecture/adr/0008-disk-pressure-guardrails.md).
This document is the day-to-day reference for **using** what the ADR
shipped.

## What the guardrails do

The disk guardrails operate in two tiers:

* **Cleanup tier** — the legacy `DiskGuardrailsService.enforce()` deletes
  completed qBittorrent torrents whose age + ratio + seeding-time
  thresholds say "safely seeded". Fires automatically when the
  monitored mount crosses `max_used_percent` (default 65%); stops
  when usage drops below `target_used_percent` (default 58%).
* **Lockdown tier** — the new `DownloadLockdownService.engage()` pauses
  every download client (qBittorrent + SABnzbd + Sonarr/Radarr/Lidarr/Readarr
  RSS-sync). Fires when the mount crosses the `lockdown_percent`
  threshold (default 75%); auto-releases when usage drops below
  `release_percent` (default 60%, 15-percentage-point hysteresis to
  prevent flap).

Each tier has both an **AUTO** trigger (the `GuardrailRegistry`'s 60-second
evaluation tick) and a **MANUAL** trigger (operator-clicked from the
UI's Storage card or via `curl`).

## When to engage manual lockdown

The auto-tier handles the common case: disk crosses 75%, lockdown
engages, cleanup fires, disk drops, lockdown releases. Operators
rarely need to do anything.

Reach for the manual surface when:

* **You're about to copy a Blu-ray rip.** Engage `pause-auto?hours=2`
  so the auto-tick won't trip on the temporary spike. Already-paused
  clients stay paused; the bypass just stops the AUTO-side from
  re-engaging.
* **A producer is bypassing the gate.** A manual Sonarr Search → Episode
  ignores download-client pause, so disk keeps climbing even with
  lockdown engaged. Click `Engage lockdown (manual)` to mark the
  state sticky — the auto-loop won't release at 60% because the
  trigger is now `manual`. Cancel the rogue search, then click
  `Release` once the producer is calm.
* **You just freed 50 GB and want lockdown released NOW** instead of
  waiting for the next 60-second tick. Click `Force evaluate` — runs
  one tick immediately, sees disk under `release_percent`, releases.

## How Maintainerr interacts with disk guardrails

The two systems work on different sides of the disk and **don't
fight each other**:

| Maintainerr | Disk guardrails (lockdown) |
|---|---|
| Library-side retention | Download-side admission control |
| Prunes `/media/...` per operator-defined collection rules | Stops `/data/torrents/` from filling up further |
| Fires on a Maintainerr-defined cadence (typically daily) | Fires on the controller's 60-second tick |
| Affects what's already in your library | Affects what's about to arrive |

Lockdown's `release_percent` (default 60%) is well above the typical
breakpoint at which Maintainerr collection rules fire — so even when
a Maintainerr deletion brings disk down past 60%, the lockdown's
release is already triggered by the same disk drop. They run on the
same physical disk usage but on different sides of the inflow/outflow
arrow, so neither is gated on the other.

The Maintainerr integration is wired by the controller at bootstrap
through `adapters/maintainerr/rules_wiring.MaintainerrCollectionsWirer`
(ADR-0005 Phase 3). Operators configure retention rules through
Maintainerr's UI; the controller probes
`/app/maintainerr/api/collections` for `radarrSettingsId` /
`sonarrSettingsId` linkage and delegates the rule sync to
`MaintainerrService.ensure_integrations`.

## The seven manual API endpoints

All POST endpoints require both an `X-CSRF-Token` header echoing the
`media_stack_csrf` cookie AND `controller_admin` role. Read-only GET
admits any authenticated user.

```bash
# Snapshot (read-only): state, thresholds, paused clients, transitions
curl -s -b cookies.txt \
  https://apps.media-stack.local/api/disk-guardrails | jq .

# Run cleanup synchronously, regardless of disk %
curl -s -b cookies.txt \
  -H "X-CSRF-Token: $(grep media_stack_csrf cookies.txt | awk '{print $7}')" \
  -X POST https://apps.media-stack.local/api/disk-guardrails/cleanup \
  -H 'Content-Type: application/json' \
  -d '{"categories": ["tv", "movies"], "max_delete": 25}'

# Engage manual lockdown (sticky — won't auto-release)
curl -s -b cookies.txt \
  -H "X-CSRF-Token: $(grep media_stack_csrf cookies.txt | awk '{print $7}')" \
  -X POST https://apps.media-stack.local/api/disk-guardrails/lockdown

# Release lockdown
curl -s -b cookies.txt \
  -H "X-CSRF-Token: $(grep media_stack_csrf cookies.txt | awk '{print $7}')" \
  -X POST https://apps.media-stack.local/api/disk-guardrails/release

# Pause AUTO-side evaluation for N hours (1-24)
curl -s -b cookies.txt \
  -H "X-CSRF-Token: $(grep media_stack_csrf cookies.txt | awk '{print $7}')" \
  -X POST 'https://apps.media-stack.local/api/disk-guardrails/pause-auto?hours=2'

# Force one immediate guardrail tick (bypasses cadence)
curl -s -b cookies.txt \
  -H "X-CSRF-Token: $(grep media_stack_csrf cookies.txt | awk '{print $7}')" \
  -X POST https://apps.media-stack.local/api/disk-guardrails/evaluate

# Phase 4: persist cleanup-policy overrides
curl -s -b cookies.txt \
  -H "X-CSRF-Token: $(grep media_stack_csrf cookies.txt | awk '{print $7}')" \
  -X POST https://apps.media-stack.local/api/disk-guardrails/cleanup-policy \
  -H 'Content-Type: application/json' \
  -d '{"categories": ["tv", "movies"], "min_completion_age_hours": 24, "min_seeding_time_minutes": 480, "min_ratio": 1.0, "max_delete_per_run": 100, "order_strategy": "largest_first"}'
```

The first request through `curl` needs to authenticate (Basic, session,
or trusted-proxy). The `cookies.txt` file is captured from a
prior login or `curl -c cookies.txt -d 'username=...&password=...'`
through the login endpoint.

## The four cleanup-ordering strategies

The cleanup pass picks torrents to delete in one of four orderings:

| Strategy | Sort key | When to use |
|---|---|---|
| `oldest_first` | `(completion_on, size)` ascending | **Default.** FIFO — oldest seeded torrents go first. Best when storage is plentiful and seed-ratio is the primary care. |
| `largest_first` | `(-size, completion_on)` | Free disk fastest. One delete reclaims the most bytes. Best when emergency cleanup is needed and ratio is secondary. |
| `poor_ratio_first` | `(ratio asc, completion_on asc)` | Delete the worst-seeded first. Best when seeding-ratio compliance is the primary care. |
| `watched_first` | watched-then-oldest | Prefer torrents whose mapped media files Jellyfin shows as played. Best when "I've already watched it" is a sensible signal. Falls back to `oldest_first` if the Jellyfin lookup fails. |

Operators choose the strategy via the UI's Cleanup-policy panel
(Phase 4 made this writable; previous releases were read-only). The
new `POST /api/disk-guardrails/cleanup-policy` endpoint persists
the selection at `/srv-config/.controller/disk-cleanup-policy.json`.

## How to tune thresholds

Three tiers, **in increasing priority order**:

1. **`contracts/defaults/operations.yaml`** — the controller-baked
   defaults. **Do not edit per-install**; this is the upstream
   default file every install ships with.
2. **Profile `disk_guardrails:` block** — per-install operator
   defaults. Lives in your `media-{compose,k8s}-{minimal,standard,full}.yaml`
   profile YAML. Survives reinstalls. Use this for "this server has
   tighter thresholds than upstream" — e.g. a laptop install with
   `max_used_percent: 50` instead of `65`.
3. **UI-saved overrides** — the dashboard's Storage card → Save
   button. Persisted to `/srv-config/.controller/guardrails.json`
   by the `GuardrailRegistry`. Highest priority, takes effect
   immediately, no controller restart.

Most operators stay at tier 3 (UI). Tier 2 is the durable
"baked into the install" answer. Tier 1 is upstream — leave it alone.

## Troubleshooting

### "Lockdown engaged but disk's still climbing"

A producer is bypassing the gate. Common culprits:

* **Manual Sonarr / Radarr search.** RSS-sync is paused on lockdown,
  but a manual `/api/v3/command` POST goes through. Cancel the search
  in the *arr UI.
* **Maintainerr rule fired backwards.** A misconfigured collection
  rule could pull additions instead of deletions; check the
  Maintainerr UI's collection-rule definition.
* **Restored backup mid-lockdown.** A rsync from a snapshot doesn't
  go through any download client. Wait for it to finish, then run
  `POST /api/disk-guardrails/cleanup`.

### "Cleanup never fires"

Check the `monitor_path` resolution. The controller's startup log
emits an `[INFO] Disk guardrails: usage check (path=...)` line on
every cleanup tick. If `path=` shows the wrong filesystem, set
`DISK_GUARDRAILS_MONITOR_PATH` env var or fix the
`disk_guardrails.monitor_path` in your profile YAML. The 9-candidate
fallback chain (env vars + `/srv-stack` + `/srv-stack/media` + ...) is
documented in `services/disk_guardrails_service.py:56-80`.

### "MANUAL_LOCKDOWN stuck after operator left"

Manual lockdown is sticky — auto-release at 60% does NOT clear it.
Either:

* Click `Release` in the dashboard's Storage card.
* `POST /api/disk-guardrails/release` from `curl`.
* Restart the controller — the state file at
  `/srv-config/.controller/disk-lockdown.state.json` is loaded,
  paused clients are re-confirmed (idempotent), but UI shows the
  same state and the operator must still explicitly release.

### "Cleanup-policy save returns 400"

The validator rejects:

* `categories` not a list of strings;
* `min_completion_age_hours` / `min_seeding_time_minutes` /
  `min_ratio` not a positive number;
* `max_delete_per_run` not a positive integer (capped at 1000
  server-side);
* `order_strategy` not one of the four canonical names
  (`oldest_first`, `largest_first`, `poor_ratio_first`,
  `watched_first`).

The 400 response includes a human-readable `error` field with the
specific complaint. Re-submit with the corrected body.

### "Live SSE updates not flowing"

ADR-0008 Phase 4 added EventBus publishers for
`storage.lockdown_engaged`, `storage.lockdown_released`, and
`storage.cleanup_invoked`. The dashboard's `EventStreamProvider`
maps the `storage.*` topic onto the `["storage"]` query key so the
Storage card updates without waiting on its 30-second poll.

If the card seems sluggish, check:

* The browser's `?topics=` query string for the `/api/events` SSE
  endpoint — it should include `storage` (the request shape is
  `topics=jobs,sessions,media_integrity,storage`).
* Network tab for the `event:` line — `storage.lockdown_engaged`
  should appear within a second of clicking `Engage lockdown`.

## See also

* [ADR-0008 disk-pressure guardrails](../architecture/adr/0008-disk-pressure-guardrails.md)
  — architectural background and trigger-interaction rules.
* [ADR-0005 bootstrap consumes orchestrator state](../architecture/adr/0005-bootstrap-consumes-orchestrator-state.md)
  — Maintainerr integration and the `MaintainerrCollectionsWirer`
  (Phase 3).
* [docs/how-to/storage.md](../how-to/storage.md) — older operator
  storage guide; covers the `_PerContentTypeQuota` rule and the
  legacy `media-hygiene` job.
