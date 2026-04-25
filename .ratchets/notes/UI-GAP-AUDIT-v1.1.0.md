# UI gap audit — v1.1.0

Compares three corpora:

1. Prior dashboard ([`src/media_stack/api/dashboard.html`](../../src/media_stack/api/dashboard.html), 5,632 lines) plus six lazy-loaded tab modules under [`src/media_stack/api/static/`](../../src/media_stack/api/static/).
2. New React UI v1.1.0 under [`ui/src/`](../../ui/src/) — Tanstack Router, eleven routes, one fully built feature folder.
3. Controller API surface — 162 paths in [`src/media_stack/api/openapi.yaml`](../../src/media_stack/api/openapi.yaml).

Read-only audit; no source files were modified.

## 1. Executive summary

- Prior dashboard exposed roughly **70 distinct operator capabilities** across eight top-level tabs and six standalone tab modules; together they consumed **~85 of the 162 controller paths**.
- New React UI v1.1.0 ships **one complete feature** (Media Integrity), **six visible-but-skeletal routes** that read live data with stubbed `Promise.resolve(...)` queryFns, and **two Coming-Soon placeholders** (`/profile`, `/settings`). It calls fewer than 10 real endpoints today (see [`ui/src/api/endpoints.ts`](../../ui/src/api/endpoints.ts)).
- Three of the six prior `tab_*.js` security modules (`tab_bans`, `tab_emergency_revoke`, `tab_me_security`, `tab_security`, `tab_sessions`) have **no React equivalent at all** — and the new UI has no nav entries reaching them.
- **~110 controller endpoints have zero UI consumer in v1.1.0**. The largest unconsumed clusters are bans/security (8), users/identity admin (15), config/profile/env (10), TLS + cert install (3), snapshots/backup (5), schedules + jobs (4), download-client + import-list management (10), and the entire stack-upgrade + onboarding + drift surface.
- Net effect: v1.1.0 is a chrome-quality shell with one finished tab. Operators who relied on the old dashboard for ban management, session revocation, password-policy editing, TLS install, snapshot diffing, or guardrail tuning have nowhere to do that work in the new UI.

## 2. Capability matrix

Status legend: **LANDED** = same capability shipped in v1.1.0; **PARTIAL** = visible in new UI but reads stub data or lacks the action; **MISSING** = not in new UI at all; **OBSOLETE** = recommended to leave out.

### Header / global

| Capability | Source | API endpoint(s) | Status in new UI |
|---|---|---|---|
| Identity badge + signed-in user | dashboard.html:816, 4165 | `GET /api/auth/identity` | **MISSING** (no header chip wired) |
| Logout | dashboard.html:842, 5570 | `POST /api/auth/logout` | **MISSING** (no UserMenu sign-out wiring) |
| Theme toggle (light/dark) | dashboard.html:938 | n/a (localStorage) | **LANDED** (ThemeProvider) |
| Branding (logo + name) | dashboard.html:5582 | `GET /api/branding` | **PARTIAL** (hardcoded "Media Stack" in `Sidebar.tsx`) |
| Stack-upgrade banner | dashboard.html:989, 1009, 1021 | `GET/POST /api/stack/update`, `POST /api/stack/upgrade`, `GET /api/stack/upgrade/{task_id}` | **MISSING** |
| CSRF token plumbing | dashboard.html:932 | n/a (cookie) | **LANDED** ([`ui/src/lib/idempotency.ts`](../../ui/src/lib/idempotency.ts) + `client.ts`) |

### Logs tab

| Capability | Source | API endpoint(s) | Status in new UI |
|---|---|---|---|
| Tail controller log stream | dashboard.html:443 (UI), real-time SSE | `GET /api/logs` (controller) | **PARTIAL** — `useLogs` polls `GET /api/logs/{service}` every 5s, no SSE |
| Tail per-service log | dashboard.html:2551 | `GET /api/logs/{service}` | **PARTIAL** — selector exists; no multi-source combining, no fullscreen, no export |
| Filter logs by level (OK/Warn/Err) | dashboard.html:460–463 | client-side | **MISSING** |
| Regex log search | dashboard.html:464 | client-side | **MISSING** |
| Export logs | dashboard.html:468 | client-side | **MISSING** |
| Set runtime log level | dashboard.html:955 | `GET/POST /api/log-level` | **MISSING** |

### Content tab (Library + Indexers)

| Capability | Source | API endpoint(s) | Status in new UI |
|---|---|---|---|
| Library list + edit | dashboard.html:3594, 3633, 4400, 4445 | `GET/POST /api/libraries`, `GET /api/config/libraries` | **MISSING** |
| Recent additions | dashboard.html:1869 | `GET /api/recent` | **PARTIAL** — `useRecentAdditions` is stubbed to `{items:[]}` (`hooks.ts:191`) |
| Library kind counts (movies/TV/tracks/books) | dashboard.html (top stats) | n/a (`/api/library/stats` planned, not in OpenAPI) | **PARTIAL** — `useLibraryStats` returns zeroes |
| Recent activity / download history | dashboard.html:3643 | `GET /api/download-history`, `GET /api/download-analytics` | **MISSING** |
| Quality profiles browse + toggle | dashboard.html:3674, 3725 | `GET /api/quality-profiles/{service}`, `POST /api/quality-profiles/toggle`, `GET /api/quality-presets` | **MISSING** |
| Discovery / import lists | dashboard.html:3737, 3780, 3788 | `GET/POST /api/import-lists`, `GET /api/discovery-lists`, `POST /api/import-lists/{service}/{list_id}/{toggle\|delete}` | **MISSING** |
| Indexers list + toggle + delete | dashboard.html:1881, 1924, 1930 | `GET /api/indexers`, `GET /api/indexer-stats`, `POST /api/indexers/{indexer_id}/toggle`, `DELETE /api/indexers/{indexer_id}` | **MISSING** |

### Routing tab

| Capability | Source | API endpoint(s) | Status in new UI |
|---|---|---|---|
| View routing strategy + per-app URL | dashboard.html:2027, 2070 | `GET /api/routing` | **PARTIAL** — `useRouting` is stubbed (returns subdomain default + empty apps) |
| Edit routing config | dashboard.html:2484 | `POST /api/routing` | **MISSING** |
| Reachability matrix probe | dashboard.html:2008, 2278 | `GET /api/routing-probe`, `GET /api/route-probe` | **MISSING** |
| DNS check | dashboard.html:2453 | `GET /api/dns-check` | **MISSING** |
| Gateway hostnames | dashboard.html:2138 | `GET /api/gateway-hostnames` | **MISSING** |
| TLS cert view | dashboard.html:2207 | `GET /api/tls/certificate` | **MISSING** |
| TLS cert regenerate self-signed | dashboard.html:2223 | `POST /api/tls/certificate/regenerate` | **MISSING** |
| Install custom PEM cert | dashboard.html:2243 | `POST /api/tls/certificate` | **MISSING** |

### Ops tab

| Capability | Source | API endpoint(s) | Status in new UI |
|---|---|---|---|
| System health overview (uptime/containers/disk) | dashboard.html status loop, 1642 | `GET /api/health` | **PARTIAL** — `/ops` shows the card, but `useOpsHealth` returns stub zeros |
| Refresh services | implicit `actions/refresh` | `POST /api/services/refresh` (per `endpoints.ts`) — **not in OpenAPI** | **PARTIAL** (button wired, endpoint isn't defined) |
| Rotate API keys | dashboard.html:2354 | `POST /api/rotate-keys` | **PARTIAL** — `/ops` button calls `POST /api/keys/rotate` (**path mismatch — not in OpenAPI**) |
| Pull/refresh manifests | dashboard.html:4108 | `GET /api/manifests` | **PARTIAL** — `/ops` button calls `POST /api/manifests/pull` (**not in OpenAPI**) |
| Health probe | dashboard.html:1642 | `GET /api/health` | **PARTIAL** — `/ops` button calls `POST /api/health/probe` (**not in OpenAPI**) |
| Action runner / confirmAction | dashboard.html:1216, 5209 | `POST /actions/{name}` | **MISSING** (no actions API surface in new UI) |
| Cancel running action | dashboard.html:1223 | `POST /cancel` | **MISSING** |
| Job tree / running jobs | dashboard.html:921, 5116 | `GET /api/jobs` | **MISSING** |
| Action timeline / Gantt | dashboard.html:574 (ops-timeline) | client-side from job stream | **MISSING** |
| Health stories | dashboard.html:1649 | `GET /api/health/stories` | **MISSING** |
| Failed services panel | implicit | `GET /api/failed-services` | **MISSING** |
| Crashloop detector | implicit | `GET /api/health/crashloops` | **MISSING** |
| Config-integrity health | dashboard.html:1647 | `GET /api/health/config-integrity` | **MISSING** |
| Config drift display | dashboard.html:1720, 3835 | `GET /api/config-drift` | **MISSING** |
| Per-service stats (download/upload/queue) | dashboard.html:1681 | `GET /api/stats` | **MISSING** |
| Disk usage / guardrails | dashboard.html:1733, 1822 | `GET /api/disk`, `POST /api/guardrails`, `GET /api/cleanup-preview` | **MISSING** |
| Containers / namespaces panel | dashboard.html:3878, 5627 | `GET /api/namespaces` | **MISSING** |
| Container image-update detection | dashboard.html:3940 | `GET /api/image-updates` | **MISSING** |
| GPU detection + enable | dashboard.html:3964, 4021 | `GET /api/gpu`, `POST /api/gpu/enable` | **MISSING** |
| Snapshots list | dashboard.html:4043 | `GET /api/snapshots` | **MISSING** |
| Take snapshot now | dashboard.html:4036 | `POST /api/snapshot` | **MISSING** |
| Open snapshot detail | dashboard.html:4064 | `GET /api/snapshots/{filename}` | **MISSING** |
| Snapshot diff (a vs b) | dashboard.html:4080 | `GET /api/snapshot-diff` | **MISSING** |
| Mounts list | dashboard.html:4092 | `GET /api/mounts` | **MISSING** |
| Storage breakdown | dashboard.html:4619 | `GET /api/storage-breakdown` | **MISSING** |
| Bandwidth chart (Envoy) | dashboard.html:3421, 5623 | `GET /api/envoy/stats` | **MISSING** |
| Server-side schedules CRUD | dashboard.html:4636, 4653, 4661 | `GET/POST /api/schedules`, `POST /api/schedules/{schedule_id}/delete` | **MISSING** |
| Restart service | dashboard.html:1596 | `POST /api/restart/{service}` | **MISSING** |
| Batch restart | dashboard.html:4125 | `POST /api/batch-restart` | **MISSING** |
| Hard reset service | dashboard.html:2379 | `POST /api/services/{service_id}/reset` | **MISSING** |
| Media-server reset (Jellyfin) | implicit | `POST /api/jellyfin/reset`, `POST /api/media-server/reset` | **MISSING** |
| Reset all passwords | dashboard.html:2404 | `POST /api/reset-password` | **MISSING** |
| Validate credentials | dashboard.html:1615 | `POST /api/credentials` | **MISSING** |
| Auto-heal status + run | implicit | `GET /api/auto-heal`, `GET /api/auto-heal/enabled`, `POST /api/auto-heal/run` | **MISSING** |
| Backup download | dashboard.html (loadBackup) | `GET /api/backup` | **MISSING** |
| Restore | implicit | `POST /api/restore` | **MISSING** |
| Service versions | dashboard.html:1676 | `GET /api/versions` | **MISSING** |
| API keys export | dashboard.html:2309 | `GET /api/keys` | **MISSING** |
| Stack upgrade flow | dashboard.html:989, 1009, 1021 | `GET /api/stack/update`, `POST /api/stack/upgrade`, `GET /api/stack/upgrade/{task_id}` | **MISSING** |

### Config tab (`tab-profile`)

| Capability | Source | API endpoint(s) | Status in new UI |
|---|---|---|---|
| Display preferences | dashboard.html:4694, 4773 | `GET/POST /api/display-preferences` | **MISSING** |
| Download client settings | dashboard.html:4456, 4529, 4535, 4542 | `GET/POST /api/download-client-settings` | **MISSING** |
| Download categories CRUD | dashboard.html:4564 | `POST /api/download-categories` | **MISSING** |
| Metadata language/country | dashboard.html:4574, 4609 | `GET/POST /api/metadata-settings` | **MISSING** |
| Live TV sources | dashboard.html:4782, 4862 | `GET/POST /api/livetv-sources`, `GET /api/iptv-countries`, `GET /api/epg-providers`, `GET /api/epg-health` | **MISSING** |
| Auth config (mode, OIDC, per-service) | dashboard.html:4885, 5091 | `GET/POST /api/auth/config`, `GET /api/auth/modes`, `GET /api/auth/oidc-providers`, `POST /api/auth/parse-oidc`, `GET /api/auth/service-policies` | **MISSING** |
| Profile YAML editor | dashboard.html:1970, 4135 | `GET/POST /api/profile` | **MISSING** (placeholder route is "Coming soon") |
| Drift view | dashboard.html:3835 | `GET /api/config-drift` | **MISSING** |
| Env vars panel | dashboard.html:4204 | `GET /api/envvars` | **MISSING** |
| Effective env | dashboard.html:1983 | `GET /api/env` | **MISSING** |
| Backup tab | dashboard.html:loadBackup | `GET /api/backup`, `POST /api/restore` | **MISSING** |

### Webhooks / Alerts tab

| Capability | Source | API endpoint(s) | Status in new UI |
|---|---|---|---|
| List configured webhooks | dashboard.html:2501 | `GET /webhooks` (legacy non-`/api` path) | **PARTIAL** — `useWebhooks` is stubbed `{webhooks:[]}` |
| Add webhook | dashboard.html:2501 | `POST /webhooks` | **PARTIAL** — `webhooks.tsx:148` is a literal `TODO(api)` |
| Delete webhook | dashboard.html:2502 | `DELETE /webhooks` | **MISSING** (button exists, no handler) |
| Test all webhooks | dashboard.html:2505 | `POST /webhooks/test` | **MISSING** |
| Arr-app webhook config | dashboard.html:4548 | `GET /api/arr-webhooks` | **MISSING** |
| Alert rules (svc down for N) | dashboard.html:691 (UI) | client-side rules | **MISSING** |

### Users tab + identity admin

| Capability | Source | API endpoint(s) | Status in new UI |
|---|---|---|---|
| Users list | dashboard.html:2799 | `GET /api/users` | **PARTIAL** — `useUsers` returns stub `{users:[]}` |
| Add user | dashboard.html:2880 | `POST /api/users` | **MISSING** (placeholder dropdown items only) |
| Bulk CSV import | dashboard.html:2947 | `POST /api/users-bulk-import` | **MISSING** |
| Edit user | dashboard.html | `PATCH /api/users/{user_id}` | **MISSING** |
| Delete user | dashboard.html:2750, 3197 | `POST /api/users/{user_id}/delete` | **MISSING** |
| Reset user password | dashboard.html:2777, 3150, 5541 | `POST /api/users/{user_id}/reset-password` | **MISSING** |
| Change user role | dashboard.html:3108 | `POST /api/users/{user_id}/role` | **MISSING** |
| Disable / enable / lock user | dashboard.html:3114 | `POST /api/users/{user_id}/state` | **MISSING** |
| Per-user sessions list | dashboard.html:3053 | `GET /api/users/{user_id}/sessions` | **MISSING** |
| Revoke specific user session | tab_sessions.js:142 | `POST /api/users/{user_id}/sessions/{session_id}/revoke` | **MISSING** |
| Revoke all of a user's sessions | dashboard.html:3068 | `POST /api/users/{user_id}/revoke-sessions` | **MISSING** |
| User login history | tab_security.js:22, dashboard.html | `GET /api/users/{user_id}/login-history` | **MISSING** |
| Roles list + edit | dashboard.html:2859, 3270 | `GET /api/roles`, `GET/POST /api/roles/{role_slug}` | **MISSING** |
| Invites list | dashboard.html:2962 | `GET /api/invites` | **MISSING** |
| Create invite | dashboard.html:2918 | `POST /api/invites` | **MISSING** |
| Revoke invite | dashboard.html:2986 | `DELETE /api/invites/{invite_id}` | **MISSING** |
| Accept invite | dashboard.html:3093 | `POST /api/invites/accept` | **MISSING** |
| Provider reconcile (cross-link Authelia/Jellyfin/Jellyseerr) | dashboard.html:3350, 3402, 3412 | `GET /api/users-reconcile`, `POST /api/users-reconcile/import`, `POST /api/users-reconcile/unlink`, `GET /api/user-providers` | **MISSING** |
| Audit log view | dashboard.html:3307 | `GET /api/audit-log`, `GET /api/audit-log/head`, `GET /api/audit-log/verify` | **MISSING** (hook exists in `endpoints.ts`, no consumer) |
| Password policy view + edit | dashboard.html:3125, 3213, 3238 | `GET/POST /api/password-policy` | **MISSING** |
| Password reset tickets | implicit | `GET /api/password-tickets/{ticket_id}` | **MISSING** |
| Password propagation | implicit | `POST /api/password-propagation` | **MISSING** |
| Access URLs panel | dashboard.html:3024 | `GET /api/access-urls` | **MISSING** |

### My Profile (`/me`)

| Capability | Source | API endpoint(s) | Status in new UI |
|---|---|---|---|
| Avatar + display name + email | dashboard.html:5462, tab_me_security.js | `GET /api/me` | **PARTIAL** — `useMeProfile` returns stub `{username:"you"}` |
| Sessions card | tab_me_security.js:19 | `GET /api/me/sessions` | **PARTIAL** — visible card, no data, no per-row revoke |
| API tokens card | tab_me_security.js:20 | `GET /api/me/tokens`, plus `GET/POST /api/tokens`, `POST /api/tokens/refresh`, `POST /api/tokens/revoke-family`, `DELETE /api/tokens/{token_id}` | **PARTIAL** — visible card, "Generate new token" button does nothing |
| Two-factor / MFA state | tab_me_security.js:21 | `GET /api/me/mfa-state` | **PARTIAL** — visible badge, "Manage" button does nothing |
| Sign out everywhere else | tab_me_security.js:23 | `POST /api/me/revoke-others` | **MISSING** (button exists, no handler) |
| Login history (last 100) | tab_me_security.js:21 | `GET /api/me/login-history` | **MISSING** |
| "This wasn't me" report | tab_me_security.js:24 | `POST /api/me/this-wasnt-me` | **MISSING** |

### Security / abuse signals

| Capability | Source | API endpoint(s) | Status in new UI |
|---|---|---|---|
| Active sessions across all providers | tab_sessions.js:32 | `GET /api/sessions/active` | **MISSING** |
| Revoke session by id | tab_sessions.js:142 | `POST /api/users/{user}/sessions/{id}/revoke` | **MISSING** |
| Failed-login clusters | tab_security.js:19 | `GET /api/security/failed-logins` | **MISSING** |
| New-location alerts | tab_security.js:20 | `GET /api/security/new-locations` | **MISSING** |
| Concurrent-session spikes | tab_security.js:21 | `GET /api/security/concurrent` | **MISSING** |
| User bans list + add + remove | tab_bans.js:18 | `GET/POST /api/bans/users`, `POST /api/bans/users/{username}/remove` | **MISSING** |
| IP/CIDR bans list + add + remove | tab_bans.js:19 | `GET/POST /api/bans/ips`, `POST /api/bans/ips/{cidr}/remove` | **MISSING** |
| Emergency revoke-all | tab_emergency_revoke.js:22 | `POST /api/emergency-revoke-all` | **MISSING** |

### Media Integrity (showcase)

| Capability | Source | API endpoint(s) | Status in new UI |
|---|---|---|---|
| Status panel + adapter table | tab_media_integrity.js:23 | `GET /api/media-integrity/status` | **LANDED** ([`features/media-integrity/StatusOverview.tsx`](../../ui/src/features/media-integrity/StatusOverview.tsx), `AdapterTable.tsx`) |
| Live progress while a pass runs | tab_media_integrity.js:24 | `GET /api/media-integrity/progress` | **LANDED** ([`ProgressBar.tsx`](../../ui/src/features/media-integrity/ProgressBar.tsx)) |
| Reconcile (with dry-run) | tab_media_integrity.js:25 | `POST /api/media-integrity/reconcile` | **LANDED** ([`ReconcileButton.tsx`](../../ui/src/features/media-integrity/ReconcileButton.tsx)) — but the dry-run toggle from the old UI is **MISSING** |
| Enforce config | tab_media_integrity.js:26 | `POST /api/media-integrity/enforce-config` | **LANDED** ([`EnforceButton.tsx`](../../ui/src/features/media-integrity/EnforceButton.tsx)) |
| Resolve "needs review" candidate | tab_media_integrity.js:27 | `POST /api/media-integrity/resolve-review` | **LANDED** ([`NeedsReviewPanel.tsx`](../../ui/src/features/media-integrity/NeedsReviewPanel.tsx)) |
| Missing-API-key warning banner | tab_media_integrity.js:183 | derived from `/status` payload | **MISSING** in new `StatusOverview` |

### Onboarding / setup

| Capability | Source | API endpoint(s) | Status in new UI |
|---|---|---|---|
| Onboarding wizard state | dashboard.html (wizard refs) | `GET /api/onboarding` | **MISSING** |
| Validate migration | implicit | `GET /api/validate-migration` | **MISSING** |
| Custom service definition | implicit | `POST /api/custom-service` | **MISSING** |
| Custom-format import | implicit | `POST /api/custom-formats/import`, `GET /api/custom-formats/{service}` | **MISSING** |
| Discovery: popular TV | implicit | `GET /api/discovery/popular-tv` | **MISSING** |
| Telemetry consent | implicit | `GET /api/telemetry` | **MISSING** |

## 3. New UI inventory

What is actually rendered today, route by route. Anything tagged "stub data" reads from the placeholder hooks in [`ui/src/api/hooks.ts:166-269`](../../ui/src/api/hooks.ts) which return `Promise.resolve(...)` and are explicitly marked `TODO(api)`.

- **`/`** — redirects to `/media-integrity` ([`routes/index.tsx`](../../ui/src/routes/index.tsx)).
- **`/media-integrity`** — fully wired. Calls `GET /api/media-integrity/{status,progress}`, `POST /api/media-integrity/{reconcile,enforce-config,resolve-review}`. The only route that consumes real backend data end-to-end. Composes `StatusOverview`, `AdapterTable`, `NeedsReviewPanel`, `ProgressBar`, `EnforceButton`, `ReconcileButton`.
- **`/content`** — visible Cards: 4 stat tiles (movies/TV/tracks/books) + recent additions list. Both queries are stubbed (zeroes / empty array). No indexers, quality, discovery-list, or download-history surfaces.
- **`/logs`** — source `<select>`, polling viewer (5s tick) for `GET /api/logs/{service}`. No level filter, no regex search, no export, no log-level setter, no controller-stream tail.
- **`/routing`** — strategy card + apps table, both reading `useRouting`'s stub. No edit form, no probe, no DNS check, no TLS surface.
- **`/ops`** — six action tiles + a 4-tile health card. Three of the six action tiles call **paths that are not in `openapi.yaml`** (`POST /api/services/refresh`, `POST /api/keys/rotate`, `POST /api/manifests/pull`, `POST /api/health/probe`); they will 404 against the controller until the routes are added or the call paths are corrected.
- **`/users`** — table with stub data. Row-level dropdown items ("Reset password", "Disable", "Edit role") are decorative — no `onClick`/mutation behind them.
- **`/me`** — three cards: profile header, sessions, tokens, MFA. All buttons (`signout-everywhere`, `generate-token`, `manage-mfa`) are placeholders without handlers.
- **`/webhooks`** — list view (stub) + add-webhook form. Submit handler is literally `// TODO(api): POST /api/webhooks { url, event_type }` ([`routes/webhooks.tsx:148`](../../ui/src/routes/webhooks.tsx)).
- **`/profile`** — `EmptyState` "Coming soon" only.
- **`/settings`** — `EmptyState` "Coming soon" only.

The new UI ships a strong foundation: `AppShell`, `Sidebar`, `TopBar`, `BottomNav` (mobile), `CommandPalette`, `Breadcrumb`, `ResponsiveTable`, `ErrorBoundary`, `PullToRefresh`, theme provider, idempotency-key helper, manifest contract test, accessibility tests for every surface. The **chrome is production-grade**; the **content is mostly stubbed**.

## 4. Backend endpoints with no UI consumer

Grouped by resource family. Counts include all OpenAPI methods not consumed anywhere in `ui/src/`. Verbatim paths from `openapi.yaml`.

### Authentication & identity (10 paths)
`/api/auth/config`, `/api/auth/login`, `/api/auth/modes`, `/api/auth/oidc-providers`, `/api/auth/parse-oidc`, `/api/auth/service-policies`, `/api/me`, `/api/me/login-history`, `/api/me/mfa-state`, `/api/me/this-wasnt-me`, `/api/me/revoke-others`, `/api/me/sessions`, `/api/me/tokens`. *Lets an operator manage the auth mode (Authelia/OIDC/native), enrol MFA, audit their own login history, and self-revoke compromised sessions.*

### User administration (15 paths)
`/api/users` (POST/PATCH), `/api/users-bulk-import`, `/api/users-reconcile`, `/api/users-reconcile/import`, `/api/users-reconcile/unlink`, `/api/user-providers`, `/api/users/{user_id}` (PATCH/DELETE), `/api/users/{user_id}/delete`, `/api/users/{user_id}/login-history`, `/api/users/{user_id}/reset-password`, `/api/users/{user_id}/revoke-sessions`, `/api/users/{user_id}/role`, `/api/users/{user_id}/sessions`, `/api/users/{user_id}/sessions/{session_id}/revoke`, `/api/users/{user_id}/state`, `/api/roles`, `/api/roles/{role_slug}`, `/api/invites`, `/api/invites/accept`, `/api/invites/{invite_id}`. *Add/disable/delete users, run CSV imports, reconcile cross-provider identities, manage roles + invites.*

### Tokens & sessions (5 paths)
`/api/tokens`, `/api/tokens/refresh`, `/api/tokens/revoke-family`, `/api/tokens/{token_id}`, `/api/sessions/active`. *Issue/revoke long-lived API tokens, view every live session across every provider.*

### Bans & abuse defence (8 paths)
`/api/bans/ips`, `/api/bans/ips/{cidr}/remove`, `/api/bans/users`, `/api/bans/users/{username}/remove`, `/api/emergency-revoke-all`, `/api/security/concurrent`, `/api/security/failed-logins`, `/api/security/new-locations`. *Ban a user/IP, lift a ban, see credential-stuffing clusters, run the break-glass full-revoke when a credential leak is in progress.*

### Audit & compliance (3 paths)
`/api/audit-log`, `/api/audit-log/head`, `/api/audit-log/verify`. *Read the tamper-evident audit chain and verify its head.*

### Password / credential policy (4 paths)
`/api/password-policy`, `/api/password-tickets/{ticket_id}`, `/api/password-propagation`, `/api/credentials`. *Set min length / classes / no-reuse window, mint reset tickets, validate stored credentials.*

### TLS & cert install (3 paths)
`/api/tls/certificate`, `/api/tls/certificate/download`, `/api/tls/certificate/regenerate`. *View the live edge cert, download it for archival, regenerate the self-signed fallback, install a real PEM bundle.*

### Routing & reachability (5 paths)
`/api/routing-probe`, `/api/route-probe`, `/api/dns-check`, `/api/gateway-hostnames`, `/api/access-urls`. *Run the reachability matrix, check DNS, see every public URL in one place.*

### Stack lifecycle (4 paths)
`/api/stack/update`, `/api/stack/upgrade`, `/api/stack/upgrade/{task_id}`, `/api/validate-migration`. *Notify operators a new stack version is available, run the upgrade with progress, sanity-check post-upgrade.*

### Snapshots & backup (5 paths)
`/api/snapshot`, `/api/snapshots`, `/api/snapshots/{filename}`, `/api/snapshot-diff`, `/api/backup`, `/api/restore`. *Hourly config snapshots with redaction; diff two snapshots; download a full backup; restore a stack from one.*

### Health & ops detail (10 paths)
`/api/health-history`, `/api/health/config-integrity`, `/api/health/crashloops`, `/api/health/stories`, `/api/auto-heal`, `/api/auto-heal/enabled`, `/api/auto-heal/run`, `/api/failed-services`, `/api/jobs`, `/api/manifests`. *Long health timeline, narrative health stories ("why is Sonarr orange?"), auto-heal control, currently-running jobs.*

### Containers / infra (4 paths)
`/api/namespaces`, `/api/image-updates`, `/api/gpu`, `/api/gpu/enable`, `/api/mounts`, `/api/envoy/stats`. *Per-pod resource view, container-image freshness, GPU detection + enable for Jellyfin transcode, mount table, edge bandwidth chart.*

### Service control (5 paths)
`/api/restart/{service}`, `/api/batch-restart`, `/api/services/{serviceId}/api-key`, `/api/services/{service_id}/reset`, `/api/jellyfin/reset`, `/api/media-server/reset`, `/api/rotate-keys`, `/api/reset-password`. *Targeted restart, hard reset of a misbehaving service, full-stack key rotation.*

### Library & content (10 paths)
`/api/libraries`, `/api/recent`, `/api/quality-profiles`, `/api/quality-profiles/{service}`, `/api/quality-profiles/toggle`, `/api/quality-presets`, `/api/import-lists`, `/api/import-lists-all`, `/api/discovery-lists`, `/api/discovery/popular-tv`, `/api/import-lists/{service}/{list_id}/delete`, `/api/import-lists/{service}/{list_id}/toggle`. *Define libraries, browse what's been added, toggle quality profiles, manage discovery sources.*

### Indexers (3 paths)
`/api/indexers`, `/api/indexer-stats`, `/api/indexers/{indexer_id}`, `/api/indexers/{indexer_id}/toggle`. *List Prowlarr indexers, see grab/RSS rates, enable/disable/delete one.*

### Downloads (5 paths)
`/api/downloads`, `/api/download-history`, `/api/download-analytics`, `/api/download-categories`, `/api/download-client-settings`. *Active queue, history, per-category routing, qBittorrent settings.*

### Storage & disk (5 paths)
`/api/disk`, `/api/cleanup-preview`, `/api/guardrails`, `/api/storage-breakdown`, `/api/feed.xml`. *Free-space + threshold guardrails with a "cleanup preview" before action.*

### Configuration surfaces (10 paths)
`/api/profile`, `/api/env`, `/api/envvars`, `/api/config-drift`, `/api/config/libraries`, `/api/custom-service`, `/api/custom-formats/import`, `/api/custom-formats/{service}`, `/api/display-preferences`, `/api/metadata-settings`, `/api/livetv-sources`, `/api/iptv-countries`, `/api/epg-providers`, `/api/epg-health`, `/api/log-level`, `/api/onboarding`, `/api/telemetry`. *Edit the YAML profile, set display preferences, configure IPTV/EPG, change runtime log level, run onboarding.*

### Schedules & jobs (3 paths)
`/api/schedules`, `/api/schedules/{schedule_id}/delete`, `/api/jobs`. *Server-side recurring jobs (reconcile every 6h, etc.).*

### Webhooks (1 path)
`/api/arr-webhooks`. *(Note: the OpenAPI does not currently define `/api/webhooks` — the prior dashboard called the legacy non-`/api` `/webhooks` and `/webhooks/test` paths. This is a **contract gap** the new UI will hit when the form is wired.)*

### Misc / static (3 paths)
`/api/feed.xml`, `/api/grafana.json`, `/api/openapi.json`, `/api/openapi.yaml`, `/api/docs`, `/api/static/{asset}`. *Most are doc/feed surfaces and don't need first-class UI.*

## 5. Recommended restoration order

Ranked by operational criticality. **Security/auth surfaces beat everything else** — losing the ability to revoke a session during an incident is much worse than losing the ability to tweak a quality profile.

### v1.1.1 — security-critical break-glass (1 sprint)

1. **Sessions tab** — `GET /api/sessions/active` + `POST /api/users/{user}/sessions/{id}/revoke`. Re-implement [`tab_sessions.js`](../../src/media_stack/api/static/tab_sessions.js) under `ui/src/features/sessions/`. Without this, an operator who sees a suspicious login has no UI path to kick it.
2. **Bans tab** — `GET/POST /api/bans/{users,ips}` + the two `/remove` endpoints. Direct re-implementation of [`tab_bans.js`](../../src/media_stack/api/static/tab_bans.js).
3. **Emergency revoke** — `POST /api/emergency-revoke-all` with the two-step confirm dialog from [`tab_emergency_revoke.js`](../../src/media_stack/api/static/tab_emergency_revoke.js). One button, one phrase, hides behind `/users` or a new `/security` route.
4. **/me real data** — wire `useMeProfile` to `GET /api/me`, `GET /api/me/sessions`, `GET /api/me/tokens`, `GET /api/me/mfa-state`, and the three actions (`revoke-others`, generate-token, `this-wasnt-me`). The cards already exist; only the queryFns and four mutation hooks are missing.
5. **TopBar identity + logout** — `GET /api/auth/identity` + `POST /api/auth/logout`. The shell currently has no signed-in indicator and no way to sign out cleanly.

### v1.1.2 — observability + audit (1 sprint)

6. **Audit log** — `GET /api/audit-log`, `/audit-log/head`, `/audit-log/verify`. Hook already exists ([`useAuditLog`](../../ui/src/api/hooks.ts) line 145); needs a route + table.
7. **Security signals** — `GET /api/security/{failed-logins,new-locations,concurrent}`. Direct port of [`tab_security.js`](../../src/media_stack/api/static/tab_security.js).
8. **Real ops endpoints** — fix the four made-up paths in `endpoints.ts` (`/api/services/refresh`, `/api/keys/rotate`, `/api/manifests/pull`, `/api/health/probe`). Either add them to OpenAPI or repoint to the real paths (`/actions/refresh`, `/api/rotate-keys`, etc.).
9. **Health stories + crashloops** — `GET /api/health/stories`, `/api/health/crashloops`, `/api/failed-services`, `/api/health-history`. Make `/ops` actually useful.
10. **Jobs panel** — `GET /api/jobs`. Drives "what's running right now" everywhere.

### v1.2.0 — admin & content surfaces (2 sprints)

11. **Users CRUD** — full surface (POST, role, state, reset-password, sessions, login-history, bulk import, invites, password policy).
12. **Webhooks** — wire the form to whatever the controller actually exposes; add `/webhooks` and `/webhooks/test` to OpenAPI if not already there, or move the controller endpoints under `/api/webhooks`.
13. **Routing** — real `GET /api/routing` data + edit form + probe + TLS install (the highest-stakes config screen).
14. **Library surfaces** — libraries, recent additions, indexers, quality profiles, discovery lists. Replace the four stub hooks.
15. **Snapshots + backup + restore** — operational safety net.
16. **Schedules** — server-side recurring jobs.

### v1.2.1+ — long tail

17. Display preferences, metadata settings, Live TV / IPTV.
18. Stack-upgrade flow + onboarding wizard.
19. Drift, env-vars editor, profile-YAML editor.
20. GPU enable, mounts, namespaces, image-update detection, bandwidth chart, storage breakdown.

## 6. Things deliberately not restored

The new shell is a chance to leave behind some old UX warts.

- **Inline `<button onclick="...">` everywhere.** The old `dashboard.html` has hundreds. The new UI's typed mutation hooks + `data-testid` discipline are strictly better. Don't port the inline-handler pattern.
- **Browser-side schedules** ([`dashboard.html:621`](../../src/media_stack/api/dashboard.html)). They only run while the dashboard tab is open, which is a footgun. Server-side schedules (`/api/schedules`) are the real product; cut the browser variant.
- **Per-card `<details><summary>` collapse on every panel.** The new shell uses `Card`s with `CardHeader` + responsive table; the dense collapsed-by-default pattern was a workaround for "5,632 lines of HTML on one page". The new layout doesn't need it.
- **`/api/static/swagger-ui-bundle.js` (1.4 MB).** The `/api/docs` route can ship as a separate tiny SPA or a redirect; don't bundle Swagger into the operator UI.
- **Two parallel logout paths** ([`dashboard.html:829`](../../src/media_stack/api/dashboard.html)). The new TopBar should call `POST /api/auth/logout` and let the controller decide whether to redirect to Authelia. Don't re-export the if-behind-gateway-then-redirect logic to client code.
- **Inline CSS in HTML.** The new app's design tokens + Tailwind layer is the system. Resist any temptation to port the old `style="..."` strings.
- **`localStorage`-driven feature flags** (e.g. `ms-tail-pinned`). Use Tanstack Query's persisted state or a real settings endpoint.
- **The "Hard Reset Service" flow** ([`dashboard.html:567`](../../src/media_stack/api/dashboard.html)) is OK to restore *after* the audit log is wired — never before. A destructive button without a tamper-evident audit trail in the same UI is a regression.
- **The `/api/jellyfin/reset` shortcut.** The general `/api/services/{service_id}/reset` covers it; one less special case.

---

## What I'd ship in v1.1.1

Six tiny PRs, scoped to ~1 week of one engineer: (1) wire `GET /api/auth/identity` + `POST /api/auth/logout` into `TopBar`/`UserMenu`; (2) port `tab_sessions.js` into `ui/src/features/sessions/` + add a `/sessions` route; (3) port `tab_bans.js` into `ui/src/features/bans/` + add a `/bans` route; (4) port `tab_emergency_revoke.js` as a destructive `Dialog` accessible from `/sessions`; (5) replace every stub queryFn in `/me` with the real `GET /api/me/{sessions,tokens,mfa-state}` calls and wire the three `/api/me/*` mutations; (6) audit `endpoints.ts` against `openapi.yaml` and either add the four missing ops routes (`/api/services/refresh`, `/api/keys/rotate`, `/api/manifests/pull`, `/api/health/probe`) to the contract or repoint the existing client to `/api/rotate-keys` and the equivalent real paths. That set restores break-glass incident response in the new UI without sliding the schedule on the bigger admin/observability work, and it leaves Media Integrity untouched.
