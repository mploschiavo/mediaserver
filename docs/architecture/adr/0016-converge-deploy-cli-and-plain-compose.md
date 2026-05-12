# ADR-0016 — Converge `media-stack-deploy` and plain `docker compose up -d`

**Status:** Proposed (2026-05-12). Phase 1 backlog drafted; Phase 2
documented but not yet implemented. Builds on the orchestrator
work in ADR-0009 / ADR-0010 / ADR-0013, the controller-boot
reconciliation pattern landed in ADR-0015 Phase 7m, and the
per-arr `<arr>:ensure-url-base` promises shipped 2026-05-12.

Authors: matthew

## Context

The repo ships two deploy entry points that DO NOT produce the
same end-state:

1. **`media-stack-deploy`** (Workflow CLI, `cli/commands/
   deploy_stack_main.py`) — reads the profile YAML, renders
   `deploy/compose/.env`, runs every `compose_preflight_handler`
   declared in `contracts/services/*.yaml`, then calls
   `docker compose up -d`. The canonical first-time-deploy path.
2. **Plain `docker compose up -d`** — what most operators reach
   for. After the `compose.yaml` `include:` stub landed at the
   repo root (2026-05-12), this works from the repo root without
   `-f`. It does NOT run `compose_preflight` handlers or render
   `.env`.

Throughout the 2026-05-12 operator session, the user repeatedly
hit issues that boiled down to "plain compose skipped a
preflight". The orchestrator promise loop has been catching up:
qBittorrent password rotation (ADR-0013 Phase 3), Jellyfin
admin credentials sync (2026-05-12), and per-arr URL-base
reconciliation (2026-05-12) all moved from `compose_preflight`
into orchestrator promises that fire on every controller boot,
regardless of which compose path brought the stack up.

But the gap isn't closed. The remaining `compose_preflight`
handlers and the `.env` rendering step are still deploy-CLI-only.
This ADR pins down which parts of that gap can close and which
parts can't, and proposes a phased migration so plain
`docker compose up -d` becomes safe as the default ops command.

## What plain `docker compose up -d` does and does NOT do, today

After ADR-0013 Phases 1–3b + ADR-0015 Phase 7m + the 2026-05-12
session, plain compose ALREADY handles:

* qBit auth-bypass whitelist + WebUI password rotation
  (`qbittorrent:ensure-credentials` job, `pre_bootstrap` phase,
  priority 5, requires the password promise)
* Jellyfin admin password sync vs. `STACK_ADMIN_PASSWORD`
  (`jellyfin:ensure-credentials` job, `pre_bootstrap` phase,
  priority 6)
* Per-arr `urlBase` reconcile across Radarr / Sonarr / Lidarr /
  Readarr / Prowlarr (five `<arr>:ensure-url-base` jobs)
* Audit-chain verifier (cross-instance lock, file-archive cache
  reset, no-spam alerting)
* Authelia post-up config seed via `BootConfigureAuthService` —
  separate path from the deploy CLI's compose_preflight handler;
  same end state
* Stack-admin weak-password guardrail on every boot (not just
  first seed)

Plain compose still does NOT handle:

| Concern | Why it matters | Convergence category |
|---|---|---|
| Renders `deploy/compose/.env` from `secrets.generated.env` + profile | Without it, compose falls back to image defaults (`STACK_ADMIN_PASSWORD=admin`) | Category 2 (compose-up-time) |
| Bazarr URL-base via `bazarr.compose_preflight:ensure_compose_bazarr_url_base` | Bazarr reverse-proxy routes 404 until reconcile | Category 1 (migrate to promise) |
| Sabnzbd API access seed | Controller can't pre-discover the API key into the secret | Category 1 (migrate to promise) |
| Servarr `config.xml` `AuthenticationMethod` patch | `<arr>:ensure-url-base` already sets urlBase via API but doesn't touch `AuthenticationMethod` (still on the file-level path) | Category 1 (extend the existing promise) |
| `apps.<service>: false` profile toggles → `selected_apps` filter | Compose starts every service in the YAML regardless | Category 2 (compose-up-time) |
| Compose profiles selection (`COMPOSE_PROFILES=optional,plex`) | Required for the `plex` / `optional` overlays | Category 2 (compose-up-time) |

## Decision

We split the remaining gap into two categories with different
futures:

### Category 1 — closable via the orchestrator promise pattern

Every remaining `compose_preflight_handler` entry that's NOT a
compose-up-time concern moves into the orchestrator promise loop,
mirroring the qBit / Jellyfin / *arr URL-base work from this
session.

* **`bazarr:ensure-url-base`** — Bazarr URL-base reconcile via
  the Bazarr settings API (similar shape to the Servarr URL-base
  reconcile, but Bazarr's config is JSON/YAML not XML so the
  endpoint differs).
* **`sabnzbd:ensure-api-key-seeded`** — read Sabnzbd's API key
  from disk (`sabnzbd.ini`), persist into env + k8s secret. Same
  shape as `<arr>:ensure-api-key-discoverable`.
* **Extend `<arr>:ensure-url-base`** to also reconcile
  `AuthenticationMethod=External` + `AuthenticationRequired=
  DisabledForLocalAddresses` via the same `/api/v*/config/host`
  PUT. Retires `servarr.http_preflight.run_preflight`'s file-level
  XML patcher entirely (it's already redundant for the urlBase
  field).
* **Retire** `services/apps/{bazarr,sabnzbd}/compose_preflight.py`
  + the `compose_preflight_handler` contract entries that
  reference them. They become 30-line shims that delegate to the
  lifecycle method, then removed in a later cleanup phase (same
  pattern as ADR-0013 Phase 3b's qBit shim retirement).

After Category 1 lands, plain `docker compose up -d` produces a
fully-reconciled stack on every boot — same end-state as the
deploy CLI's compose_preflight path — modulo the Category 2
items.

### Category 2 — fundamentally compose-up-time

Two items can never move into the orchestrator no matter how many
ADR phases we ship, because they happen BEFORE any controller
container exists:

* **`.env` rendering.** Compose reads env vars at parse time. An
  init container that writes `.env` doesn't help: the parent
  compose has already locked in env values for the rest of the
  project. The first `compose up -d` on a fresh box without
  `.env` falls back to image defaults; the controller can write
  `.env` on its first boot, but the new values only take effect
  on the NEXT `compose up -d`.
* **`apps.<service>: false` profile toggles** — once compose
  starts a container, the controller can't un-start it without
  fighting compose's restart policy. Service filtering happens
  at compose-up time via `COMPOSE_PROFILES` env or the
  `selected_apps` flag passed to `docker compose up -d`.

The fix for these is documentation, not code: spell out the
one-time `.env` bootstrap that an operator should do once, then
let plain compose take over.

## Convergence end-state

After all three phases (Category 1 promise migrations + Phase 3
docs), the two paths converge for the common operator workflow:

| Scenario | Plain `docker compose up -d` | `media-stack-deploy` |
|---|---|---|
| **Fresh box, `.env` exists with strong stack-admin password** | ✅ Works fully | ✅ Works fully |
| **Fresh box, no `.env`** | ⚠️  Falls back to image defaults; controller WARNs (and refuses to boot if `internet_exposed=true`) | ✅ Renders `.env`, then up |
| **Re-up / restart / day-to-day** | ✅ Works fully | overkill |
| **Profile changes** (`apps.sonarr: false`, switch `media_server` from jellyfin to plex) | ❌ Filter not applied | ✅ Re-renders + restarts with filter |

The remaining "deploy CLI only" footprint shrinks to:

* The first-ever `.env` bootstrap on a fresh box.
* Profile changes that toggle service selection.

Both can be one-time operator gestures rather than a "run the
deploy CLI every time" reflex. The doc-level decision: plain
`docker compose up -d` becomes the day-to-day canonical command,
the deploy CLI becomes the bootstrap-once + change-profile tool.

## Phases

| Phase | Status | Notes |
|---|---|---|
| Phase 1 — `bazarr:ensure-url-base` promise | not started | mirror `<arr>:ensure-url-base` (5 commits worth of pattern, half the size on bazarr because the URL-base API is one PUT to `/api/system/settings`) |
| Phase 2 — `sabnzbd:ensure-api-key-seeded` promise | not started | mirror `<arr>:ensure-api-key-discoverable` |
| Phase 3 — fold Servarr `AuthenticationMethod` patch into `<arr>:ensure-url-base` | not started | retires the last server-XML edit in `http_preflight.run_preflight` |
| Phase 4 — retire the now-redundant `compose_preflight` shims | not started | thin delegators left over from Phases 1–3 get deleted; `compose_preflight_handler` contract field becomes optional |
| Phase 5 — doc rewrite: plain compose is canonical | not started | refresh `docs/how-to/deployment.md` to lead with `docker compose up -d` and demote the deploy CLI to "first-time bootstrap + profile changes" |

## What this ADR does NOT propose

* **A second-pass `.env` reload.** Could be implemented (controller
  writes `.env` on first boot, compose re-reads on next up). Decided
  not to: adds a "first run with weak defaults" footgun that the
  weak-password blocklist already mitigates. One-time operator
  gesture is cleaner.
* **Folding compose_preflight handler invocation into a sidecar
  init container in the compose YAML itself.** Considered. Decided
  against because the init container would need privileged docker-
  socket access (it shells out to docker exec for the qBit + arr
  preflight bodies) and the orchestrator promise loop ALREADY does
  this from inside the controller after up — adding a sidecar
  duplicates the responsibility.
* **Retiring the deploy CLI entirely.** It still owns the
  first-time `.env` rendering and the `apps.<svc>: false` →
  `selected_apps` flow. Those are real Category-2 concerns.

## Cross-references

* ADR-0009 — Trigger-driven jobs framework: the foundation the
  Category 1 promises plug into.
* ADR-0010 — Collapse ensurers into jobs: every Category 1
  promise here follows that pattern.
* ADR-0013 — Retire `run-legacy-pipeline`: closed the qBit /
  Servarr legs of the same convergence; this ADR closes Bazarr +
  Sabnzbd and the file-level Servarr XML patcher.
* ADR-0015 Phase 7m — Controller boot reconciliation pattern:
  the same shape every Category 1 promise here will use.
* `docs/how-to/deployment.md` — current "Two ways to deploy
  compose" section spells out the gap from the operator side;
  Phase 5 rewrites it as one path with a one-time setup step.

---

**Project Steward**
Matthew Loschiavo · [matthewloschiavo.com](https://matthewloschiavo.com) · [mploschiavo@gmail.com](mailto:mploschiavo@gmail.com)
