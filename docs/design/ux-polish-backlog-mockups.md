# UX polish backlog — design mockups

Visual designs for the items in [memory/project_ux_polish_backlog.md].
Each section: the operator question, the current gap, the design,
implementation outline.

ASCII layout diagrams approximate the rendered card; real
implementation uses tailwind/responsive grid, colour-tinted badges
matching the existing tone tokens (success / info / warning / danger).

---

## 1. EPG providers — full CRUD

**Operator question:** "How do I add a new XMLTV / Schedules Direct
provider? Can I see which 2 are passing and which 2 are failing?"

**Current gap:** `EpgProvidersCard` lists providers but the "Open"
button is non-functional; no add/edit/remove. `EpgHealthCard` says
"2/4 probes ok" without identifying which.

### Design

```
┌────────────────────────────────────────────────────────────────────────────┐
│ EPG providers                                              [+ Add provider]│
│ Guide data sources for Live TV. The TV-grid in Jellyfin pulls from these.  │
├────────────────────────────────────────────────────────────────────────────┤
│ Status    Name                  Type        Channels    Last fetch        │
│ ✓ pass    schedules-direct      SD          1,247       2m ago    [✏] [×]│
│ ✓ pass    xmltv-eu              xmltv-url    312         8m ago    [✏] [×]│
│ ✗ fail    iptv-org-fr           xmltv-url    0           14m ago   [✏] [×]│
│   └─ HTTP 503 from upstream — last 4 fetches failed                       │
│ ⚠ stale   plextv-au             plex-tv     45          3h ago    [✏] [×]│
│   └─ Last successful fetch >2h ago; auto-retry every 5min                  │
└────────────────────────────────────────────────────────────────────────────┘
```

Add modal:

```
┌────────────────────────────────────────────────┐
│ Add EPG provider                               │
├────────────────────────────────────────────────┤
│ Name        [my-eu-guide                    ]  │
│ Type        [xmltv-url ▼]                       │
│ URL         [https://iptv-org.github.io/…   ]  │
│ Auth (opt)  [username] [password]               │
│ Refresh     [every 6h ▼]                        │
│                                                 │
│ Test connection: [Probe] → ✓ 312 channels       │
│                                                 │
│                          [Cancel] [Save & Test] │
└────────────────────────────────────────────────┘
```

### Backend
- `POST /api/livetv-sources` — already accepts the array;
  add per-provider validation
- `POST /api/livetv-sources/{id}/probe` — NEW; fetches a single
  provider, returns `{ok, channels_seen, sample_titles, error}`
- `DELETE /api/livetv-sources/{id}` — NEW; removes from profile YAML

### UI
- Replace the read-only table with a Data Table per row
- "Add provider" modal (re-use Dialog primitive)
- Edit-in-drawer pattern (re-use HostEditDrawer pattern)
- Probe button on each row + on the add modal — calls the new endpoint

**Estimate:** 1 PR, ~6 hours.

---

## 2. Audit log retention + pagination

**Operator question:** "How long is the audit log retained? What
happens after 90 days / 6 months / 5 years?"

**Current gap:** No retention indicator; controller writes
unbounded JSON lines to disk; no UI for retention setting.

### Design

```
┌────────────────────────────────────────────────────────────────────────────┐
│ Audit log                                                  [⋯ Settings]   │
│ Tamper-evident record of every operator action.                            │
├────────────────────────────────────────────────────────────────────────────┤
│ Retention: 365 days (24,718 entries · 18.4 MB on disk · 12.4d capacity)   │
│ Oldest: 2025-04-26  Newest: 2026-04-26  Compaction: nightly @ 03:00 UTC   │
│                                                                            │
│ Filter [all kinds ▼] [all actors ▼] [last 7 days ▼] [search: ___]         │
│                                                                            │
│ When               Actor       Action                Detail               │
│ 14:32 today        admin       routing.update         hosts[+1]: jf.iom… │
│ 14:31 today        admin       service-policies       jellyfin → 2FA    │
│ 13:50 today        system      job.complete           media-integrity   │
│ ... [10 more]                                                              │
│                                                                            │
│                                       Page 1 of 247 [< prev] [next >]    │
└────────────────────────────────────────────────────────────────────────────┘
```

Settings modal:

```
┌────────────────────────────────────────────────┐
│ Audit log retention                            │
├────────────────────────────────────────────────┤
│ Keep entries for [365] days                    │
│   Estimated max size: 540 MB                   │
│                                                │
│ Compaction window  [03:00 UTC ▼]               │
│ Compaction format  ◉ gzip   ○ none             │
│ Archive location   [/srv/audit/archive/   ]    │
│                                                │
│ ⚠ Reducing retention will delete entries older │
│   than the new threshold on the next compact.  │
│                                                │
│                            [Cancel] [Save]     │
└────────────────────────────────────────────────┘
```

### Backend
- `GET /api/audit-log/stats` — NEW: returns `{retention_days,
  entry_count, disk_bytes, oldest_ts, newest_ts, next_compaction}`
- `POST /api/audit-log/retention` — NEW: writes to controller config
- `GET /api/audit-log?cursor=<ts>&limit=N` — extend existing for
  cursor-based pagination (current limit-only paging breaks past
  ~10k entries)
- Nightly compaction job: reads JSON-lines file, drops older than
  retention, gzips overflow into dated archive files.

### Ratchet
Add `tests/unit/ratchets/test_audit_log_retention_documented.py`
that asserts every persistent log surface has:
1. A documented retention policy in the surface's docstring
2. A compaction or rotation strategy in code

### UI
- Retention banner above the table
- Settings modal accessible from the ⋯ menu
- Cursor-based pagination (next/prev buttons) with infinite-scroll
  fallback for keyboard users

**Estimate:** 2 PRs, ~10 hours.

---

## 3. Jobs page polish (multi-PR)

**Operator question:** "Bootstrap is running according to the banner
— which step? Can I cancel it? How do I add or remove a job? What's
queued next?"

**Current gap:** Recent batches columns overflow; no step
visualization for multi-step jobs; no cancel/queue UI; jobs are
YAML-only to add/remove.

### Design — overall layout

```
┌─────────────────────────────────────────────────────────────────────────────┐
│ Jobs                                            [+ Schedule] [Run now]      │
│ Filter: [all ▼] [media-integrity ▼] [last 24h ▼]                             │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│ ┌─ Currently running ────────────────────────────────────────────────┐     │
│ │ ▶ bootstrap (4m32s elapsed · started 14:32 today)         [Cancel] │     │
│ │   └─ ▶ discover-api-keys                  ✓ done (1m 12s)          │     │
│ │   └─ ▶ media-integrity-scan               ▶ running (2m 45s)        │     │
│ │   └─   reconcile-arr-apps                 — pending                 │     │
│ │   └─   refresh-discovery-lists            — pending                 │     │
│ └────────────────────────────────────────────────────────────────────┘     │
│                                                                             │
│ ┌─ Queue (3) ────────────────────────────────────────────────────────┐     │
│ │ # 1  refresh-iptv-channels      scheduled, 14:45 today  [↑][↓][×] │     │
│ │ # 2  envoy-config-rebuild       triggered by config save  [↑][↓][×]│     │
│ │ # 3  trakt-watchlist-sync       scheduled, 15:00 today  [↑][↓][×] │     │
│ └────────────────────────────────────────────────────────────────────┘     │
│                                                                             │
│ ┌─ Schedules (catalog) ──────────────────────────────────────────────┐     │
│ │ Group: Media Integrity                                              │     │
│ │   ☑ media-integrity-scan        every 6h                  [✏]      │     │
│ │   ☑ jellyfin-prewarm            daily at 03:00            [✏]      │     │
│ │ Group: Content                                                      │     │
│ │   ☑ refresh-discovery-lists     every 30min               [✏]      │     │
│ │   ☐ trakt-watchlist-sync        every 6h         (paused) [✏]      │     │
│ │ Group: Ops                                                          │     │
│ │   ☑ guardrail-evaluate          every 5min                [✏]      │     │
│ │   ☑ probe-services              every 30s                 [✏]      │     │
│ └────────────────────────────────────────────────────────────────────┘     │
│                                                                             │
│ ┌─ Recent batches ──────────────────────────────────── [×] hide ────┐     │
│ │ ✓ 14:00  bootstrap         4m32s  3 jobs · all ok                  │     │
│ │ ✓ 13:30  refresh-discovery 12s    1 job  · all ok                  │     │
│ │ ✗ 13:00  media-integrity   8m23s  2 jobs · 1 failed: scan_orphans │     │
│ │   └─ click to inspect failure                                      │     │
│ └────────────────────────────────────────────────────────────────────┘     │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Tree-view for multi-step bootstrap
- Backend job framework already records sub-jobs in `tree`
- Surface as expandable tree with current-step highlight
- ⏵ for queued, ▶ for running, ✓ for done, ✗ for failed
- Per-step elapsed time

### Cancel button
- Calls existing `POST /api/jobs/{id}/cancel`
- Confirmation dialog ("This will stop bootstrap mid-flight; safe to
  re-run, but partial work may need cleanup")

### Queue management
- Drag-and-drop OR ↑/↓ arrows to reorder
- × removes a queued job (doesn't cancel running ones)

### Schedule editor
- "+ Schedule" → modal with fields:
  - Job (dropdown of registered jobs from `discover_jobs_from_contracts`)
  - Cadence (cron expression OR `every Nm/h/d` shortcut)
  - Enabled / paused toggle
  - Run now button

**Estimate:** 4-5 PRs spread over a week.

---

## 4. Charts everywhere

Pages with read-only metric data that should get visualizations like
Edge Gateway → Live:

### Library page — additions over time + size growth + quality mix

```
┌──────────────────────── Library overview ──────────────────────────────┐
│ KPI row:                                                                │
│ Total titles  Total bytes  Recently added (7d)  Quality mix            │
│   12,847       4.7 TB           +127             [pie: 4K 22% / HD …]  │
│                                                                         │
│ Additions over time (7d):                                               │
│ ▁▁▂▃▅▇▆▄▃▂▁▂▃▅▇█▇▆▄▃▂▁▂▃▅▇▆▄▃ (line chart, daily buckets)              │
│                                                                         │
│ Library size growth (90d):                                              │
│ ┌──────────────────────────────────────────────────────────────────┐  │
│ │                                                          ▄▄▄▄▄▄▄│  │
│ │                                          ▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄        │  │
│ │                          ▄▄▄▄▄▄▄▄▄▄▄▄▄▄                          │  │
│ │  ▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄                                                │  │
│ └──────────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────────┘
```

### Downloads — throughput over time, queue depth heatmap
- Stacked area: download throughput by client (qbittorrent / sabnzbd)
- Heatmap: queue depth × hour-of-day (when do my downloads pile up?)

### Sessions — concurrent over time, geo distribution
- Line chart of active sessions (use the rolling buffer from
  /api/envoy/timeseries)
- World map (when GeoIP wired) showing recent client IPs as dots

### Audit log — events-per-hour + actor distribution
- Bar chart: events/hour for last 24h
- Pie: events split by actor (admin vs system vs external)

### Indexers — grabs/RSS over time
- Line chart per indexer for grabs + RSS-queries (already in
  Prowlarr's stats API; just render them)

### Health history — per-service mini-charts
- Currently a single sparkline for total/healthy
- Expand: collapsible per-service rows, each with its own sparkline

### Ratchet
`tests/unit/ratchets/test_pages_with_metric_data_have_visualizations.py`
— scans every route under `ui/src/routes/`, counts cards with
read-only number data ≥3, asserts at least one chart component is
mounted on the same route.

**Estimate:** 1 PR per page = 6 PRs, ~3 hours each.

---

## 5. Page-by-page audit framework

**Operator's framework:** "What questions does a user ask on this
page? Do we answer them? Any CRUD / list / report / security / audit
gaps?"

### Design — `/admin/page-audit` internal tool

```
┌──────────────────────── Page audit ───────────────────────────────────┐
│ Walks every /pages route, scores each on the operator-question         │
│ framework. Output: a markdown report with green/amber/red per page.    │
│                                                                        │
│ [Run audit] (last run: 2026-04-26 09:14 — 14 routes scanned)          │
├────────────────────────────────────────────────────────────────────────┤
│ Route          Questions  CRUD       Charts    Errors    Score        │
│ /              4/4 ✓     n/a        2/3 ⚠   1/1 ✓     85%          │
│ /ops           7/8 ⚠     read       3/4 ⚠   3/3 ✓     78%          │
│ /content       6/9 ⚠     read+      2/5 ✗   2/3 ⚠     55% ⚠       │
│ /jobs          4/9 ✗     read       0/3 ✗   1/3 ✗     22% ✗        │
│ /audit-log     5/6 ⚠     read       0/3 ✗   2/2 ✓     50% ⚠       │
│ /routing       9/10 ✓    crud       6/6 ✓   3/3 ✓     95% ✓        │
│ /me/profile    3/5 ⚠     read+      0/2 ✗   1/2 ⚠     45% ⚠       │
│ ...                                                                    │
│                                                                        │
│ Click any row for the detailed gap list.                              │
└────────────────────────────────────────────────────────────────────────┘
```

### Scoring rubric (per page)

For each route, hand-author or auto-derive:

```yaml
# ui/src/routes/<route>.audit.yaml
questions:
  - "What's currently happening?"
  - "What changed in the last hour?"
  - "Why did X fail?"
crud:
  list: required
  detail: required
  create: required
  update: required
  delete: optional
charts:
  required: 1
  recommended: 3
errors:
  loading: required
  empty: required
  error_401: required
```

### Backend
- New `/api/admin/page-audit` endpoint that walks the `.audit.yaml`
  files + cross-references actual rendered content
- Run quarterly via a CronJob; results posted to `/admin/page-audit`

### Ratchet
- Every route has an `<route>.audit.yaml` next to its `.tsx`
- Score floor: ≥75% per route (red entries block CI)

**Estimate:** 1 PR for the framework + per-page YAML files (~1 hour
each), ~6 hours upfront + quarterly maintenance.

---

## 6. Retention strategies (architectural)

### Logs (controller, services)
- Today: stdout → captured by docker / kubectl → no rotation
- Plan: bundle Loki + Promtail → all stdout flows to Loki with a
  90-day retention; Loki's per-stream retention is configurable
  per service so Jellyfin transcode logs (high volume) can have
  shorter retention than controller logs (low volume, high value)
- Operator UI: a "Logs settings" card in /settings showing per-
  service retention with editable knobs

### Audit log
- See section 2 above

### Media-integrity history
- Already capped (deque)
- Surface the cap in the UI: "Retains last 1000 reports"

### Job history
- Already capped
- Surface cap

### Disk-exhaustion ratchet
`tests/unit/ratchets/test_persisted_data_has_retention.py`:
- Scans every place that writes to disk in `src/media_stack/`
- Asserts there's a documented retention policy AND a code-level
  cap (cron, deque, file-rotation)
- Burn-down list for legacy unbounded writes

---

# CLEAN-INSTALL DESIGN

(Separate ask, but related to first-time UX)

## Operator question
"On a clean docker-compose install, can I bring the stack up with
zero pre-existing config? Does bootstrap auto-run? When I land on the
UI, is it clear what's happening?"

## Current state
- `config/` directories pre-populated with example configs that
  shadow whatever bootstrap would generate
- `secrets.generated.env` lives in the repo (probably committed
  accidentally for dev convenience)
- Bootstrap runs but state is ambiguous; operator can't tell whether
  the config they see is "my setup" or "the stub"
- First-time UI shows the regular `/ops` dashboard with empty/
  failing tiles instead of a proper onboarding flow

## Design

### 1. `deploy/dist/docker-compose.yml` clean install

```bash
# Operator runs:
git clone <repo>
cd <repo>
./deploy-compose.sh init         # NEW: pre-flight checks + first-time setup
./deploy-compose.sh up
```

`deploy-compose.sh init` (NEW):
- Verify docker is running, docker compose is v2+
- Verify port 80, 443 not bound by another process
- Verify CONFIG_ROOT env (default `./compose-data/config`)
- Generate `.env` from `.env.example` if not present
- Generate `secrets.generated.env` with random secrets
- Refuse to run if `./compose-data/config/` already exists
  (prevents clobbering an existing install)

`deploy-compose.sh up`:
- Runs the existing compose up
- Waits for controller health check
- Posts `bootstrap` action (already exists)
- Streams bootstrap progress to terminal

### 2. Repo cleanup
- Move `config/` → `config/defaults/compose/` (already there for
  some files); the existing `config/authelia/` etc. should be
  gitignored or moved into defaults so they don't shadow runtime
- Add `secrets.generated.env` + any local-dev configs to `.gitignore`
  (anchored)
- Add `tests/unit/ratchets/test_no_runtime_config_in_repo.py` —
  asserts the repo has no `config/<service>/<runtime-file>` paths
  (only `config/defaults/`)

### 3. First-time UI messaging

When `/api/onboarding` reports `setup_in_progress: true` (NEW field),
the SPA renders a dedicated full-page setup wizard instead of the
regular dashboard:

```
┌──────────────────────────────────────────────────────────────────────┐
│                                                                      │
│              [iomio logo]   Media Stack — first-time setup           │
│                                                                      │
│                                                                      │
│  Step 1 of 4 — Bootstrap (running)                                   │
│  ▰▰▰▰▰▰▰▱▱▱▱▱▱▱  47%                                                │
│                                                                      │
│  Currently: Configuring Sonarr quality profiles                      │
│                                                                      │
│  ✓ Discover service API keys (12s)                                   │
│  ✓ Generate Envoy config (3s)                                        │
│  ✓ Apply Authelia user-db                                            │
│  ▶ Configuring Sonarr quality profiles (running 18s)                 │
│  ⏵ Configure Radarr quality profiles                                 │
│  ⏵ Wire indexers from Prowlarr                                       │
│  ⏵ ... 7 more                                                        │
│                                                                      │
│  Step 2 of 4 — Initial admin user (not started)                      │
│  Step 3 of 4 — Connect indexers (not started)                        │
│  Step 4 of 4 — Set language preferences (not started)                │
│                                                                      │
│  Bootstrap is automatic — no action required. The dashboard          │
│  unlocks once Step 1 finishes (estimate: 2-4 minutes).               │
│                                                                      │
│  [Tail logs ↗]    [Skip to dashboard (advanced)]                     │
│                                                                      │
└──────────────────────────────────────────────────────────────────────┘
```

When bootstrap finishes Step 1, advance the wizard to Step 2 (admin
user creation), Step 3 (indexer connection — re-uses
DiscoveryListsCard's "Add source" pattern), Step 4 (language
preferences — re-uses MetadataPreferencesCard).

When all steps complete, redirect to `/ops` with a "Welcome aboard"
toast.

### 4. Onboarding state machine

Backend `/api/onboarding` returns:

```json
{
  "setup_in_progress": true,
  "steps": [
    {"id": "bootstrap", "status": "running", "progress": 0.47,
     "current_substep": "Configuring Sonarr"},
    {"id": "admin_user", "status": "pending"},
    {"id": "indexers", "status": "pending"},
    {"id": "language", "status": "pending"}
  ],
  "estimated_remaining_seconds": 120
}
```

Frontend polls every 2s during onboarding (faster than the regular
30s cadence so the progress bar feels responsive).

### 5. Tests + ratchets

- `tests/unit/ratchets/test_clean_install_path.py` — runs
  `./deploy-compose.sh init` in a sandboxed temp dir, asserts no
  pre-existing config is required
- E2E test (Playwright): clean compose deploy → wait for onboarding
  step 1 to finish → verify dashboard renders

**Estimate:** 1 PR for clean-install scripts + repo cleanup, 1 PR
for onboarding wizard UI, 1 PR for the state machine endpoint =
~3 PRs total.

---

# Recommended PR order

If we ship one item per session, this is the dependency-respecting
order:

1. **Clean-install path** — biggest blast radius, easier to test now
   while the rest of the codebase is stable
2. **First-time onboarding wizard** — depends on (1)
3. **Audit log retention + pagination** — independent, foundational
4. **Charts everywhere** (Library first, then Sessions, Downloads,
   etc.) — independent, parallelizable
5. **Jobs page polish** — multi-PR series; cancel button first
   (highest leverage, smallest scope)
6. **EPG providers CRUD** — independent
7. **Page-by-page audit framework** — last, because it depends on
   the other surfaces being more complete

The polish backlog in memory references this doc by path so a future
agent picking up any of these reads the design first instead of
inventing one.
