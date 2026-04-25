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

- **Naming consistency** — `media-stack-probe-promises` (35KB Python in
  a shell-wrapper directory) should move under
  `src/media_stack/cli/commands/`; same for `media-stack-scaffold-job-test`
  and `media-stack-render-promises`.
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

---

## Amendment 1 (2026-04-25) — Phases 12-15 added

Phase 12 — pip-installable package + console-script entry points
[SCOPE: medium-large, RISK: medium, PAYOFF: very high]

The original ADR left `bin/` as the home for both shell wrappers AND
several large Python files (1,157 LOC: `_probe_promises.py` 821 LOC,
`render-promises-reference.py` 191 LOC, `scaffold_job_test.py` 140
LOC, `controller.py` 5-line wrapper). That violates the
"`bin/` = operator shell scripts only" rule. The deeper miss: the
package isn't pip-installable — `pyproject.toml` had no `[project]`
block, so every Python invocation needs PYTHONPATH gymnastics or a
file-path wrapper.

### Steps

A. **`[project]` + `[project.scripts]` + `[build-system]` in
   `pyproject.toml`** (DONE, this commit).
   * Hatchling backend, single-source version via
     `src/media_stack/version.py`.
   * 21 existing CLIs published as `media-stack-*` console-scripts.
   * Reserved `media_stack.adapters` entry-point group for out-of-tree
     plugins (the packaging-layer expression of "pluggable apps").
   * Verified `pip install -e .` succeeds + binaries on `$PATH`.

B. **Migrate three exiles** from `bin/` to `src/media_stack/cli/commands/`:
   * `media-stack-probe-promises` → `src/media_stack/cli/commands/probe_promises.py`
   * `media-stack-render-promises` → `src/media_stack/cli/commands/render_promises_reference.py`
   * `media-stack-scaffold-job-test` → `src/media_stack/cli/commands/scaffold_job_test.py`
   * Update `[project.scripts]` (entries already reserved as comments).
   * Update referrers (e.g. `bin/verify-fresh-install.sh:114`) to call
     the console-script names.

C. **Container ENTRYPOINT cutover.**
   * Delete `bin/controller.py` (5-line wrapper).
   * `services/controller/Dockerfile` adds `RUN pip install --no-cache-dir .`
     and sets `ENTRYPOINT ["media-stack-controller"]`.
   * `k8s/controller.yaml` + `docker/docker-compose.yml`: change
     `command:` to `[media-stack-controller, --serve]`.

D. **Migrate `bin/lib/run-python-cli.sh` consumers in two waves:**
   * Wave 1 — `run-python-cli.sh` switches its internal
     `python <file>` invocation to `python -m
     media_stack.cli.commands.<module>`. Existing `.sh` wrappers
     unchanged on the outside.
   * Wave 2 — `.sh` wrappers become `exec media-stack-<name> "$@"`.
     `run-python-cli.sh` retired in Phase 13.

E. **Wheel-based image build.** `bin/release.sh` builds a wheel once
   (`pip wheel . -w dist-wheels/`), every Dockerfile installs from
   that wheel. Smaller image diff per release; reusable across
   controller / dev / telemetry.

F. **CI gates.** Add a test that asserts `pip install -e .` succeeds
   and every declared console-script imports + responds to `--help`.

### What stays unchanged

- Source tree under `src/media_stack/` — same imports, same layout.
  The hexagonal restructure (ADR-0002) is a separate, much larger
  effort.
- Test commands (`pytest` still works). `pythonpath = ["src", "."]`
  stays as a transitional aid; Phase 12-F drops it after the
  editable install lands in CI.
- `contracts/` + `k8s/` paths.

### Risks

- Dockerfile size + build-time changes. Mitigation: time the
  difference, accept ~15s delta.
- Wheels with non-Python data files (`config/defaults/`,
  `contracts/`) need explicit declarations. Mechanical.
- Operator muscle-memory for `media-stack-probe-promises`.
  Mitigation: leave a 2-line shim at `media-stack-probe-promises` for
  one release that prints a deprecation note and execs
  `media-stack-probe-promises`. Delete in Phase 13.

---

Phase 13 — Layering integrity + minor cleanup
[SCOPE: small, RISK: low, PAYOFF: high]

Three small wins on `src/media_stack/`:

A. **Delete the empty `src/media_stack/contracts/` package** (zero
   .py files; dead since some prior cleanup).

B. **Fold `src/media_stack/core/edge/`** (10 files / 423 LOC) into
   either `core/platforms/` (if it's compose-edge plumbing) or `api/`
   (if it's gateway-adapter shape). Too thin to justify a top-level.

C. **Layering ratchet** as a unit test:
   ```python
   # tests/unit/test_architecture_layering.py
   def test_core_does_not_import_services():
       """core/ is platform/infrastructure; services/ is the domain
       layer. Domain depends on platform; never the reverse."""
       for path in (ROOT / "src/media_stack/core").rglob("*.py"):
           src = path.read_text()
           assert "from media_stack.services" not in src
   ```
   Document the rules in `docs/architecture/repo-layout.md`. Locks
   the convention without moving any files.

D. **`bin/` cleanup** (after Phase 12 lands):
   - Group .sh files by concern: `release/`, `build/`, `deploy/`,
     `verify/`, `ops/`.
   - Keep `bin/lib/run-python-cli.sh` (LOAD-BEARING dispatcher) and
     `bin/controller.py` becomes obsolete (Phase 12-C deletes).

---

Phase 14 — Tests mirror source taxonomy
[SCOPE: medium, RISK: low, PAYOFF: very high]

Today **429 of 430 unit tests live FLAT** at `tests/unit/test_*.py`.
Plus 11 top-level test homes with overlapping scope (`tests/api/`,
`tests/jobs/`, `tests/services/`, `tests/media_integrity/`,
`tests/guardrails/` are all "unit-ish" but separated; `tests/e2e/`,
`tests/integration/`, `tests/smoke/` are all "integration-ish").

### Target

```
tests/
├── unit/
│   ├── auth/               # was tests/unit/test_auth_*.py + test_authelia_*.py
│   ├── jobs/               # was tests/unit/test_*job*.py + tests/jobs/
│   ├── media_integrity/    # was tests/unit/test_media_integrity_* + tests/media_integrity/
│   ├── guardrails/         # was tests/unit/test_guardrails_* + tests/guardrails/
│   ├── apps/
│   │   ├── jellyfin/       # 20 tests
│   │   ├── jellyseerr/     # 6 tests
│   │   └── ...
│   ├── api/                # was tests/unit/test_api_*.py + tests/api/
│   ├── core/
│   ├── adapters/
│   ├── ratchets/           # all *_ratchet.py tests centralized
│   └── conftest.py
├── integration/            # unchanged
├── contract/               # was tests/api/ — folded
├── smoke/  e2e/  security/ # unchanged
└── browser/                # was tests/e2e/playwright/ — Playwright project
```

### Migration

~430 `git mv` operations in one atomic commit. Pytest discovery is
path-agnostic; `testpaths = ["tests"]` keeps working. Test IDs
change in CI logs (acceptable).

### Payoff

- `pytest tests/unit/auth/` — runs auth tests only. Today: manually
  enumerate 9+ patterns.
- Coverage by domain via `--include="src/media_stack/core/auth/*"`
  paired with the matching test dir.
- Per-domain `conftest.py` — fixtures only auth tests need don't
  pollute global namespace.

---

Phase 15 — Diataxis docs split + mkdocs site
[SCOPE: medium, RISK: low, PAYOFF: very high]

Today: 15 `.md` files at top of `docs/` + 6 sub-directories with
overlapping scope (`docs/architecture/` 1 file vs
`docs/architecture/overview.md` is a separate doc).

### Target

Diataxis framework — separate docs by what the reader needs:

```
docs/
├── tutorials/              # Learning — first-time walkthroughs
├── how-to/                 # Goal-oriented — operator runbooks
│   ├── operations/   auth/   storage/   networking/   ...
├── reference/              # Information — facts, configs, schemas
│   ├── api/                # auto-generated from contracts/api/openapi.yaml
│   ├── promises.md         # auto-generated from contracts/promises.yaml
│   ├── cli/                # auto-generated from --help
│   └── ui-design-system.md
├── architecture/           # Explanation — why
│   ├── overview.md         # was docs/architecture/overview.md
│   ├── *.md                # consolidated from internals/
│   └── adr/                # decision records
├── diagrams/  screenshots/

mkdocs.yml                  # NEW — drives a deployable doc site
```

### What this enables

1. mkdocs-material renders as a real navigable site, deployable to
   GitHub Pages.
2. CI link-checker (lychee or markdown-link-check) — broken cross-
   doc links fail PR.
3. Auto-generated reference:
   - OpenAPI → `docs/reference/api/` (redoc-cli or similar)
   - `contracts/promises.yaml` → `docs/reference/promises.md`
     (`media-stack-render-promises` already does this — point
     at the new path).
   - `--help` of every console-script → `docs/reference/cli/`
4. ADRs as first-class — directory pattern already established
   with this file (ADR-0001).

### Migration

Mostly mechanical `git mv` (Phase 3-extended in the original ADR
table now has these specifics). ~30 file moves + mkdocs.yml +
CI workflow step.

---

## Sequencing summary

Recommended execution order across all 15 phases:

1. **Low-risk batch:** 0, 1, 2, 13. Stop, verify CI green.
2. **First-class signals (each commit-isolated):** 12-A (DONE),
   12-B, 12-D-Wave-1.
3. **Docs upgrade:** 15 (additive — old paths can stay as redirects
   during transition).
4. **Tests upgrade:** 14 (atomic 430-mv commit).
5. **K8s reorganization in isolation:** 5.
6. **Contracts/api/ in isolation:** 4.
7. **Deploy + services unification together:** 6 + 7.
8. **Container ENTRYPOINT cutover:** 12-C, 12-D-Wave-2, 12-E, 12-F.
9. **Final cleanup:** 8, 9, 10.

Phase 16 (full hexagonal restructure) is documented in **ADR-0002**
as a multi-week parallel effort that runs at its own cadence.
