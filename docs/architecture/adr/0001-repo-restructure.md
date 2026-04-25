# ADR-0001 — Repository restructure plan

**Status:** Proposed (2026-04-25). Awaiting steward approval to begin Phase 0.

## Context

The repo has organically grown a layout that confuses three distinct
concerns:

1. **Build matrix** — three images (controller, UI, telemetry-server)
   produced from one tree, with Dockerfiles spread across `docker/`
   and `tools/telemetry-server/server.py` shipped as if it were a
   one-off script.
2. **Deploy artifacts** — k8s manifests at `k8s/`, compose at
   `docker/`, single-file release snapshots at `dist/`, examples at
   `examples/`. Four roots, no obvious ownership.
3. **Ratchets** — the contract regime, OpenAPI drift, fixture pool,
   stub-hook checks, and UI ratchet system live in 4-5 different
   places. Operators don't know where to add the next one.

Plus near-empty stubs (`apps/`, `platform/`), a typo dir
(`media/media/`), and stale shadows (`docker/examples/`).

## Decision

Adopt the structure proposed below in 11 phases (0–10), executed in
the order listed. Each phase is independently committable and
revertable.

The goals, in priority order, are:

1. **Make the build matrix legible** — every Dockerfile says what it
   builds and what context it needs.
2. **Stop pretending `apps/` / `platform/` are scaffolding** — either
   populate or delete.
3. **Make ratchets a first-class concept** — promote `.ratchets/` to
   the index for the contract regime / drift checks / coverage rules.

## What stays put (load-bearing)

- `bin/release.sh` and the `dist/`-rooted release one-liners
  operators have used for months.
- `VERSION` and `VERSION-UI` at root (read by Dockerfiles).
- `AGENTS.md`, `CLAUDE.md`, `.claude/` (load-bearing for the agent
  harness).
- `.github/` workflow + template paths.
- `contracts/` source-of-truth tree.
- `bin/controller.py` composition root.
- `src/media_stack/` package layout — moving Python sources is too
  expensive for the cosmetic win in this refactor.
- Bind-mount target dirs at root (`config/<service>/`, `data/`,
  `media/`).

## Target tree (abridged — see full plan)

```
.
├── .github/  .ratchets/  .claude/
├── AGENTS.md  CLAUDE.md  README.md  CHANGELOG.md  CONTRIBUTING.md
├── VERSION  VERSION-UI
├── pyproject.toml  mypy.ini
│
├── services/
│   ├── controller/    Dockerfile + Dockerfile.dev (was docker/*)
│   ├── ui/            full UI workspace (was ui/)
│   └── telemetry/     Dockerfile + src/server.py (was tools/telemetry-server/)
│
├── contracts/
│   ├── api/           openapi.yaml + fixtures/ (was src/.../api/openapi.yaml + tests/fixtures/api_responses/)
│   └── (existing services/, defaults/, auth/, ui_defaults/, etc.)
│
├── deploy/
│   ├── compose/       docker-compose.yml + .env.example (was docker/)
│   ├── k8s/           base/{controller,ui,edge,auth,storage,apps}/ + profiles/ (was k8s/)
│   ├── dist/          single-file snapshots (was dist/)
│   └── examples/      bootstrap-profiles/, environments/ (was examples/)
│
├── bin/               unchanged structure + bin/ops/ (was tools/hotfix_*.py)
├── tests/             Python suites + tests/browser/ (was tests/e2e/playwright/)
├── docs/              architecture/, operations/, reference/, diagrams/, screenshots/
└── src/media_stack/   unchanged
```

## Phases

| # | Goal | Scope | Risk |
|---|---|---|---|
| 0 | Add ADR + deploy/README + .ratchets/README (additive) | small | none |
| 1 | Delete dead/garbage paths (apps/, platform/, docker/docker, docker/examples, test-results) | small | low |
| 2 | Flatten `media/media/` | small | medium-low |
| 3 | Consolidate docs into operations/ + architecture/ | medium | medium |
| 4 | Move openapi.yaml + fixtures into contracts/api/ | medium-large | high |
| 5 | Group k8s/ by concern under k8s/base/ | medium | medium-high |
| 6 | Unify into deploy/{compose,k8s,dist,examples} | medium | high |
| 7 | services/{controller,ui,telemetry} top-level | medium | high |
| 8 | Merge/rename Playwright suites; tools/ → bin/ops + .ratchets | small-medium | medium |
| 9 | Promote contracts/promises.yaml to .ratchets/promises/ | small | low-medium |
| 10 | Final cleanup | small | low |

**Execution batches** to stay safe:

- 0, 1, 2, 3 — low-risk, run first.
- 9, 10 — small, run after 0–3.
- 5 — k8s, in isolation.
- 4 — contracts/api/, in isolation (don't combine with 6).
- 6 + 7 — together (the Dockerfile moves are coupled).
- 8 — last.

## Other refactoring opportunities (separate ADRs when promoted)

- **Naming consistency** — `bin/_probe_promises.py` (35KB Python in
  a shell-wrapper directory) should move under
  `src/media_stack/cli/commands/`; same for `bin/scaffold_job_test.py`
  and `bin/render-promises-reference.py`.
- **Dead code sweep** — `bin/__pycache__`, `tools/__pycache__`, root
  `package.json` placeholders, four virtualenvs at root.
- **Configuration sprawl** — `config/defaults/` (image-baked
  templates) shares a parent with operator runtime state
  (`config/jellyfin/`, etc.). Split into `templates/` vs `config/`.
- **Test infrastructure duplication** — two Playwright projects
  (root vs ui workspace), two fixture systems, three smoke test
  homes.

## Consequences

**Positive:**
- One canonical place for each kind of artifact. Operator can find
  any image's Dockerfile without grep.
- Contract regime becomes a first-class concept; ratchets accrete in
  one place.
- `apps/` / `platform/` stubs gone — no more "future scaffolding"
  excuse; we either build it or don't claim it.

**Negative:**
- Phase 4 + 6 + 7 each touch the release pipeline. Wrong move at any
  phase → broken `dist/` install one-liners or Dockerfile build
  context. Mitigated by isolating each phase as its own commit and
  running `bin/release.sh --dry-run` after.
- Doc-link churn in Phase 3. ~30 cross-doc references will need
  rewriting.
- `git bisect` blame will jump phases — coordinate phases as named
  commits (`Phase N: <goal>`) so blame stays readable.

## Status / next steps

This ADR is the agreement to do the work. The actual phases land as
separate commits, each described in its own short ADR or PR
description that references this one.
