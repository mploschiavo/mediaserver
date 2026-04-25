# ADR-0002 — Hexagonal restructure of `src/media_stack/`

**Status:** Proposed (2026-04-25). Multi-week migration. Awaiting
steward approval to begin.

**Related:** ADR-0001 Phase 16 references this. ADR-0001 explicitly
punted the hexagonal restructure as "too expensive for the
cosmetic win" without architectural commitment. This ADR is that
commitment, with a phased plan that keeps tests green throughout.

## Context

The current `src/media_stack/` layout has organically grown into
mostly-good but layering-confused shape:

```
src/media_stack/
├── adapters/    5 files
├── api/        61 files                # handlers + services + openapi mixed
├── cli/        60 files                # *_main.py command modules
├── contracts/   0 files                # EMPTY — to be deleted in Phase 13
├── core/      152 files
│   ├── auth/                59 files / 9,656 LOC   # actually a DOMAIN
│   ├── platforms/           49 files / 8,391 LOC   # actually INFRASTRUCTURE
│   ├── controller_profile/   5 files
│   ├── notifications/  events/  observability/
│   └── edge/                10 files (folded into platforms/api in Phase 13)
└── services/  292 files
    ├── apps/                184 files / 34,469 LOC  # per-tech adapters
    ├── media_integrity/      19 files / 4,820 LOC   # domain
    ├── guardrails/           14 files / 2,067 LOC   # domain
    ├── *_adapters/           17 files               # adapter framework
    └── ...
```

Two things hide:

- **`core/auth/`** is a 9.6k-LOC domain (users, sessions, MFA, OIDC,
  providers). Calling it "core" obscures its standing as the
  largest single domain in the codebase.
- **`core/platforms/`** is 8.4k LOC of compose-vs-k8s deployment
  glue. That's *infrastructure*, not core.

Plus three "adapters" homes (`adapters/`, `services/*_adapters/`,
`services/apps/`) — operator confusion when something needs to
move.

## Decision

Restructure to a hexagonal / ports-and-adapters layout aligned with
domain-driven design:

```
src/media_stack/
├── __init__.py  __main__.py  version.py    # ALREADY EXISTS post-Phase-12
│
├── domain/                                  # PURE — no I/O, no frameworks
│   ├── auth/                                # was core/auth/
│   ├── jobs/                                # was scattered through services/
│   ├── media_integrity/                     # was services/media_integrity/
│   ├── guardrails/                          # was services/guardrails/
│   ├── promises/                            # was scattered
│   ├── routing/
│   └── content/                             # libraries, recent, etc.
│
├── application/                             # USE CASES — orchestrate domain + ports
│   ├── auth/                                # login, mfa enroll, session mgmt
│   ├── bootstrap/                           # was services/bootstrap_*
│   ├── jobs/                                # job runner orchestration
│   ├── media_integrity/                     # enforce + reconcile use cases
│   └── ...
│
├── adapters/                                # IN (driving) and OUT (driven)
│   ├── http/                                # was api/handlers_*.py
│   │   ├── handlers/  middleware/  routes/
│   ├── cli/                                 # was cli/
│   ├── k8s/                                 # was core/platforms/k8s/
│   ├── compose/                             # was core/platforms/compose/
│   ├── jellyfin/                            # was services/apps/jellyfin/
│   ├── sonarr/  radarr/  lidarr/  readarr/  # was services/apps/<tech>/
│   ├── bazarr/  prowlarr/  qbittorrent/
│   ├── jellyseerr/  sabnzbd/  authelia/  authentik/
│   └── envoy/                               # gateway-config rendering
│
├── infrastructure/                          # CROSS-CUTTING
│   ├── logging/                             # was core/observability/logging
│   ├── observability/                       # was core/observability/
│   ├── events/                              # was core/events/
│   ├── notifications/                       # was core/notifications/
│   ├── secrets/                             # KMS / env / file abstraction
│   └── persistence/                         # JSON/SQLite stores
│
└── interfaces/                              # PORTS — Protocol/ABC declarations
    ├── adapter.py                           # base adapter contract
    ├── job.py                               # job interface
    ├── media_server.py                      # media-server port
    ├── arr.py                               # arr-app port
    ├── notification.py                      # notification port
    └── store.py                             # persistence port
```

## Layering rules (enforced by ratchet)

```
domain          ← imports nothing from media_stack except interfaces
application     ← imports domain + interfaces
adapters        ← imports interfaces (NOT domain or application)
infrastructure  ← imports interfaces (NOT domain or application)
```

The ratchet (`tests/unit/ratchets/test_layering.py`) walks AST imports
and asserts these constraints. New violations fail CI.

## Honest cost-benefit

- **571 files affected.**
- **~2,000 import statements to update.**
- **Every test breaks until paths re-resolve.**
- **AGENTS.md "Application Code Goes In Apps Tree" rule needs
  rewriting** (the contract that says app code lives at
  `src/media_stack/services/apps/<tech>/...`).
- **External pin pain:** any third party that pinned
  `from media_stack.services.apps.jellyfin.X import Y` breaks at
  the import line. Mitigated by `entry_points` (Phase 12) — the
  adapters are now discovered via the entry-point group, not
  directly imported.
- **~2 weeks of focused work + 1 week of test stabilization** if
  done as a single sprint. Lower per-week effort if phased.

### Why do it anyway

1. **Layering rules become enforceable.** The test ratchet means a
   PR that puts a Kubernetes import in `domain/auth/` fails before
   merge. Today's "core / services" split is convention only; the
   compiler can't help.

2. **Plugin model becomes real.** `adapters/jellyfin/` having a
   single, narrow port contract means a third-party
   `pip install media-stack-emby-plus` adapter is a peer of the
   in-tree one. Currently the in-tree apps lean on internal
   helpers that aren't part of any port.

3. **Test boundaries become natural.** Unit-test a domain by
   mocking its declared ports; nothing else moves. Today, unit
   tests for `core/auth/configure_auth_job.py` need to mock
   `services.runtime_platform`, the file-system, the YAML loader,
   and several arr-side adapters because the function isn't
   layered.

4. **The "first-class cutting-edge" claim becomes true.** A
   reviewer opening `src/media_stack/` sees the architecture
   immediately: domain, application, adapters, infrastructure,
   interfaces. No "how is this organized?" archaeology.

## Migration phases

Each bounded context migrates independently. **Tests stay green
throughout** because we use a temporary
`from media_stack.<old-path> import *` shim during each phase.

### Phase 16-A: Scaffolding

Create empty `domain/`, `application/`, `adapters/`,
`infrastructure/`, `interfaces/` directories. Add layering ratchet
(initially asserts these dirs are empty — no false positives until
first migration lands).

Define base port interfaces in `interfaces/`:

- `interfaces/adapter.py` — base `Adapter(Protocol)` with
  `health()`, `name`, `lifecycle` hooks.
- `interfaces/job.py` — `Job(Protocol)` with `run(ctx) -> JobResult`.
- `interfaces/media_server.py`, `interfaces/arr.py`,
  `interfaces/notification.py`, `interfaces/store.py`.

Each adapter migration in subsequent phases declares which port
it implements.

**Effort:** 1 day. **Risk:** zero. **Verify:** test suite still
green; layering ratchet recognizes new dirs.

### Phase 16-B: Migrate `auth/` first (proof of concept)

`core/auth/` is the largest domain. Migrating it first proves the
pattern.

1. Move pure-domain files to `domain/auth/`:
   - User / Role / Session / MFA / OIDC types (dataclasses, value
     objects).
2. Move use-case orchestration to `application/auth/`:
   - `configure_auth_job.py` → `application/auth/configure.py`
   - `authelia_oidc_crypto.py` → `application/auth/oidc_crypto.py`
3. Move infrastructure to `infrastructure/persistence/auth_store.py`
   etc.
4. Adapter shims: `adapters/authelia/`, `adapters/authentik/` (was
   `services/apps/authelia` etc.).
5. Add `from media_stack.core.auth import *` shim at the OLD path
   so existing imports keep working until a follow-up sweep.
6. Update tests one bounded context at a time:
   `tests/unit/auth/` → mirror new layout (works with Phase 14
   already done).

**Effort:** 3-4 days. **Risk:** medium — auth is the most-imported
domain. Mitigated by the import shim. **Verify:** layering ratchet
permits `domain/auth/` empty no-imports rule.

### Phase 16-C: Migrate platforms (compose, k8s)

`core/platforms/` is infrastructure. Move to `adapters/{compose,k8s}/`.
This is mostly a relocation — these were never domain.

**Effort:** 2 days. **Risk:** medium-high — touches deploy
codegen. **Verify:** envoy-config + k8s-manifest generation tests
still green.

### Phase 16-D: Migrate per-tech adapters in batches

`services/apps/<tech>/` → `adapters/<tech>/` in 4 batches:

1. Media servers: jellyfin, plex, emby, mythtv (+ tests).
2. *arr: sonarr, radarr, lidarr, readarr, bazarr (+ tests).
3. Indexers + downloaders: prowlarr, qbittorrent, sabnzbd, nzbget,
   jdownloader, grabit (+ tests).
4. Misc: jellyseerr, maintainerr, tautulli, homepage, flaresolverr,
   unpackerr (+ tests).

Each batch: move + import shim + test sweep + commit. Independent
PRs.

**Effort per batch:** 2 days. **Total:** 8 days. **Risk per
batch:** low (one tech at a time).

### Phase 16-E: Migrate cross-cutting + remaining domain

- `core/observability/`, `core/notifications/`, `core/events/` →
  `infrastructure/`.
- `services/media_integrity/`, `services/guardrails/` →
  `domain/{media_integrity,guardrails}/` + `application/...`.
- `services/runtime_factory/`, `services/runtime_*` → consolidate
  into `application/bootstrap/`.

**Effort:** 4 days. **Risk:** medium.

### Phase 16-F: Strict-mode the ratchet + remove shims

- Layering ratchet flips to STRICT mode (no exemptions).
- Remove all `from media_stack.<old-path> import *` shims.
- Final sweep: any remaining import of an old path fails CI.

**Effort:** 2 days. **Risk:** low (most work is in earlier
phases).

### Phase 16-G: Public-API surface

Declare `media_stack.public` module that re-exports the stable API
surface. Out-of-tree consumers (third-party adapters) should import
from `media_stack.public`, not from internal paths. The internal
layout can then evolve without breaking plugin authors.

**Effort:** 2 days. **Risk:** low — declarative.

### Total

**~3 weeks of focused work** spread across ~12 PRs. Each PR is
independently revertable. Tests stay green throughout via the shim
strategy.

## What stays unchanged

- `bin/`, `tests/`, `docs/`, `contracts/`, `k8s/`, `docker/` (and
  whatever those become after ADR-0001 phases).
- The pip-installable package + entry-point structure (Phase 12).
- The layering ratchet IS this ADR's enforcement mechanism — it
  exists from Phase 16-A and tightens in Phase 16-F.
- Test discovery (`tests/` already mirrors source per Phase 14).

## Consequences

**Positive:**

- Architecture is self-documenting — open `src/media_stack/`,
  immediately see the layers.
- Layering rules are enforceable — CI catches violations.
- Adapter pattern + plugin entry-points (Phase 12) makes the
  "swappable apps" story end-to-end real.
- Domain unit tests become trivially fast (no infrastructure to
  mock).
- ADR-0001's claim of "first-class cutting-edge" is structurally
  honest.

**Negative:**

- **3 weeks of focused work + 1 week stabilization.** This is the
  most expensive item in either ADR.
- AGENTS.md path conventions need rewriting in Phase 16-D and
  again at Phase 16-F.
- External pinning pain — though Phase 12's entry-point group
  mostly absorbs this.
- IDE find-usages will be loud during the transition (every move
  shows ~10 callers; cumulative noise).

## Decision rationale

The user explicitly chose to commit to the cost. The architectural
payoff (enforced layering, real plugin model, fast domain tests,
self-documenting structure) is the difference between
"good in-house tool" and "first-class product an external evaluator
takes seriously."

This ADR commits to executing the plan. ADR-0001 stays as the
prerequisite work; this one runs in parallel after Phase 16-A
lands, at its own cadence.

## Status / next steps

ADR proposed. **Phase 16-A (scaffolding + base port interfaces)
can start as soon as ADR-0001 Phase 12 stabilizes.** Subsequent
phases are independent PRs.
