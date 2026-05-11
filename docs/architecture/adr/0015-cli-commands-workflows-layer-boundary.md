# ADR-0015 — CLI layer boundary: commands as entry-points, workflows as services

**Status:** Proposed (2026-05-11). Pre-existing duplication confirmed
by audit; phased consolidation plan accepted; Phase 1 (narrow
exceptions, no behaviour change) safe to land immediately.

Authors: matthew

## Context

The `src/media_stack/cli/` tree is split into two sub-packages:

* **`cli/commands/`** — historically the home for everything CLI-
  related. Today holds 33 files: the `*_main.py` console-script
  entry points (one per `pyproject.toml` `[project.scripts]` entry)
  plus a long tail of non-entry-point modules that grew alongside
  them.
* **`cli/workflows/`** — newer (ADR-0001 Phase 12 area), introduced
  as the "service tier" between entry-point shims and lower-layer
  domain/infrastructure code. Today holds 34 files: dataclass config
  objects, `*_service.py` classes for deploy / release / controller /
  teardown / unit-test / shared interfaces, and the `workflow_*`
  Protocol contracts + composition root.

The architectural intent — clear in the newer files' docstrings — is:

> Commands = entry-points (arg parsing, exit codes, signal handling,
> calling-convention boilerplate). Workflows = composable services
> (config resolution, hook dispatch, orchestration phases, the actual
> work).

The state on disk doesn't match. The audit (2026-05-11) found
duplicated logic between the two sub-packages, particularly in the
deploy domain, and identified ~1200 lines of orchestration code in
commands/ that belongs in workflows/. The deploy CLI
(`media-stack-deploy`) is the most visible symptom — every recent
attempt to fix it surfaces a new bug because the work is split
across two parallel resolvers that don't agree:

| Trigger | Root cause | What surfaces |
|---|---|---|
| `purpose: standard` rejected | Catalog field shape | Bug #1 |
| `Config file not found: src/media_stack/contracts/…` | `parents[2]` from pre-Phase-12 layout | Bug #2 |
| `Config file not found: contracts/media-stack.config.json` | Generator never invoked by deploy flow | Bug #3 |
| `runtime_config_policy_handler` missing | Two parallel `bootstrap_job_hooks` resolvers disagree | Bug #4 |

Each fix moves the failure mode one layer deeper. The deeper one
investigates, the more obvious it becomes that `deploy_stack_main.py`
+ `deploy_stack_config_resolution.py` + `deploy_stack_runner_phases.py`
in commands/ and `deploy_cli_config_service.py` +
`deploy_hook_config_resolver.py` + `deploy_pipeline_service.py` in
workflows/ are doing overlapping work along different ownership
boundaries. The "fix the next bug" mode produces churn; the cleanup
needs to happen at the boundary itself.

### What the 2026-05-11 audit found

**Files to delete (verified duplicates):**

| commands/ file | workflows/ counterpart | Overlap |
|---|---|---|
| `deploy_stack_config_resolution.py` (395 LoC mixin) | `deploy_cli_config_service.py` + `deploy_hook_config_resolver.py` | Both resolve bootstrap profile + hook specs from JSON |
| `deploy_config_resolver.py` (wrapper) | `deploy_hook_config_resolver.py` | Commands version is a thin facade around workflows service |

**Files to move from commands/ → workflows/:**

| commands/ file | New workflows/ home | LoC |
|---|---|---|
| `deploy_stack_runner_phases.py` | `workflows/deploy_orchestration_service.py` | 445 |
| `deploy_stack_runner_services.py` | `workflows/deploy_service_factories.py` | 370 |
| `maintenance.py` | `workflows/maintenance_service.py` (partial extract) | ~150 |

**Files staying in commands/ (legitimate entry-point tier):**

The 18 `*_main.py` console-script shims, plus `controller_serve.py`,
`controller_dispatch.py`, `controller_k8s.py`, `controller_profile.py`
(all entry-point-specific glue for the controller HTTP server),
`deploy_stack_errors.py` (exception types + constants),
`render_promises_reference.py`, `scaffold_job_test.py`,
`verify_fresh_install.py` (each a standalone tool), and
`run_controller_job_priming_mixin.py` (abstract contract kept under
the god-class ratchet's 500-line cap).

**Cross-domain quality observations** (from the audit):

* **Release & Teardown** workflow services are exemplary — single-
  responsibility, dependency-injected, no duplication with commands/,
  Protocol-based dependencies via `workflow_interfaces.py`. The
  consolidation target shape already exists in this domain; the
  deploy domain needs to catch up.
* **Controller** workflow services are nearly as clean: 11 small
  focused services with constructor-injected `KubernetesClient` and
  callback log functions. One `@staticmethod` lingers (`_clean` in
  `controller_secret_priming_service.py`).
* **Generic `except Exception:`** appears in 7 files across release/
  teardown/controller domains where a narrow type
  (`urllib.error.URLError`, `subprocess.CalledProcessError`,
  `KubernetesError`) would be correct. Non-load-bearing but worth
  cleaning while the layer is being reorganised.
* **Script runner duplication**: `deploy_script_runner_service.py`
  and `controller_script_runner_service.py` have the same
  `find_script` + `run_script` body parameterised by different
  config dataclasses.

## Decision

**Establish the layer boundary as documented intent + enforce by
relocation, in 6 phases ordered by risk-vs-value:**

### The boundary contract

* **`cli/commands/`** contains ONLY:
  * `*_main.py` entry points bound to `[project.scripts]` in `pyproject.toml`.
  * Per-CLI argument parsing + exit-code translation + signal handling.
  * Exception types specific to a single CLI's contract (kept small,
    e.g. `deploy_stack_errors.py`).
  * Controller HTTP server glue (`controller_serve.py` and its three
    siblings) — these are entry-point-adjacent because the controller
    pod's container image runs them as PID 1, not as a workflow service
    consumed by other code.
  * Abstract mixin contracts required to keep god-class file sizes under
    the 500-line ratchet (`run_controller_job_priming_mixin.py`).

* **`cli/workflows/`** contains:
  * `*_service.py` classes implementing composable workflow logic.
  * Frozen dataclass configs that those services take via constructor.
  * Models/types used across services (`*_models.py`).
  * Protocols + composition root (`workflow_interfaces.py`,
    `workflow_composition_service.py`).

* **Direction of imports:** commands/ MAY import workflows/. workflows/
  MUST NOT import commands/. This is enforced by a new ratchet test
  (Phase 6 below).

### The six phases

Each phase is independently shippable; later phases assume earlier
phases landed but don't structurally depend on them. The order
minimises blast radius: cheapest+safest first, biggest refactor last.

#### Phase 1 — Narrow exceptions (no behaviour change)

Replace `except Exception:` with the specific raised types in these
7 files:

* `workflows/release_compose_deploy_service.py`
* `workflows/release_kubernetes_deploy_service.py`
* `workflows/teardown_compose_strategy.py`
* `workflows/controller_secret_priming_service.py`
* `workflows/controller_secret_reader_service.py`
* `workflows/controller_job_logs_service.py`
* `workflows/controller_job_wait_service.py` (one site)

Each site already logs and degrades; the change is `except` clause
shape only. Lands as a single commit. No new ratchet entries — the
existing `swallowed_exceptions` ratchet will allow tightening.

**Risk: none.** Internal refactor, no callers affected. **Value:
medium.** Catches stale bugs earlier (e.g. an `ImportError` being
treated as a transient HTTP failure).

#### Phase 2 — Unify the script runners

Extract a single `workflows/script_runner_service.py` with one
class (e.g. `ScriptRunnerService`) parameterised by a frozen
`ScriptRunnerConfig(root_dir: Path, bin_dir: Path | None = None)`.
Migrate the two callers (`deploy_stack_runner_services.py`,
`run_controller_job_main.py`) to use it. Keep
`deploy_script_runner_service.py` + `controller_script_runner_service.py`
as deprecated shims for one release cycle, then delete in Phase 6.

**Risk: medium.** Two callers touched, both have integration tests.
**Value: medium.** Removes ~150 LoC of duplicated bin/ resolution +
PYTHONPATH munging.

#### Phase 3 — Consolidate deploy config resolution

The load-bearing one. The deploy domain has TWO parallel resolvers
that the recent bug parade traced to:

1. `commands/deploy_stack_config_resolution.py` (the
   `ConfigResolutionMixin` consumed by `DeployStackRunner`).
2. `workflows/deploy_cli_config_service.py` +
   `workflows/deploy_hook_config_resolver.py`.

Move all config-resolution to workflows/. Delete
`commands/deploy_stack_config_resolution.py` +
`commands/deploy_config_resolver.py`. The remaining
`commands/deploy_stack_main.py` becomes:

```python
def main(argv):
    cfg = parse_deploy_stack_config(argv, root_dir=REPO_ROOT)  # from workflows
    runner = DeployPipelineRunner(cfg)                          # from workflows
    return runner.run()
```

**Risk: high.** Affects the deploy entry-point shim that operators
invoke. Migration is mechanical (mixin → instance method call) but
the integration test set must pass against both the old + new shape
during the transition.

**Value: high.** Eliminates the bug class that's been chaining
through this session — there's one resolver, so there's one place
new hook fields can be missed. The four open deploy-CLI bugs likely
collapse into one focused fix once the duplication is removed.

#### Phase 4 — Move deploy pipeline orchestration

Migrate `commands/deploy_stack_runner_phases.py` (445 LoC) →
`workflows/deploy_orchestration_service.py` and
`commands/deploy_stack_runner_services.py` (370 LoC) →
`workflows/deploy_service_factories.py`. Update
`commands/deploy_stack_main.py` to compose the runner from the new
workflow services instead of inheriting from the mixins.

Delete the mixin classes; `DeployStackRunner` becomes a thin
orchestrator (or disappears entirely if workflows provides the
right shape).

**Risk: medium-high.** Big LoC move but mostly mechanical (the
imports and class composition shift). The phase classes (`Phase`,
`run_phase()`, etc.) stay intact.

**Value: high.** Brings the deploy domain to parity with
release/teardown (where workflows already owns the orchestration).

#### Phase 5 — Migrate `maintenance.py`

Extract the snapshot / stale-prune logic from
`commands/maintenance.py` into
`workflows/maintenance_service.py`. The entry-point shim (if there
is one) stays in commands/.

**Risk: low.** Single file, ~150 LoC.
**Value: low.** Architectural cleanliness; not on any hot path.

#### Phase 6 — Enforce + clean up

Three sub-tasks:

1. **Add a ratchet test** (`tests/unit/architecture/test_cli_layer_boundary.py`)
   that asserts no file under `cli/workflows/` imports from
   `cli/commands/`. Initial floor: 0 violations (the migrations above
   should leave the tree clean).
2. **Delete the deprecated script-runner shims** from Phase 2 (after
   one release cycle).
3. **Fold the remaining `@staticmethod`** in
   `workflows/controller_secret_priming_service.py::_clean` into an
   instance method or a helper class. Tighten `STATIC_METHOD_RATCHET`
   by one.

**Risk: none.** Cleanup phase.
**Value: medium.** Locks in the architectural decision; future
regressions become test failures, not silent drift.

## Why this order

* **Phase 1** is "free" — no behaviour change, no breaking refactor,
  catches bugs sooner. Doing it first means the rest of the work
  benefits from clearer failure modes.
* **Phase 2** is the lowest-risk consolidation. It validates the
  pattern (extract → shim → delete shim) on a small, isolated
  surface before we apply it to the deploy domain (which has more
  callers and more failure modes).
* **Phase 3** is the highest-value but riskiest. It's last among the
  "behaviour-affecting" phases so Phase 1's narrower exceptions catch
  any regressions cleanly, and Phase 2 has already proved the
  shim-then-delete pattern works.
* **Phase 4** rides on Phase 3 — once the config is single-sourced,
  moving the orchestration code into workflows is a mechanical move
  (the orchestrator no longer needs to consult two config resolvers).
* **Phase 5** is cheap and unblocked.
* **Phase 6** enforces what the previous phases established.

A reasonable shipping cadence is one phase per controller image
bake (the same VERSION-bump cadence the version-pin ratchet
enforces). Phase 1 + 2 together could ship in one bake; Phase 3 +
4 should ship in separate bakes so the rollback radius is
contained.

## What we explicitly are NOT doing

* **Not renaming the directories.** `cli/commands/` and
  `cli/workflows/` are fine names; the issue is what's in them, not
  what they're called.
* **Not unifying the controller / deploy / release / teardown sub-
  hierarchies.** They have legitimately different lifecycles and
  failure modes. Their workflow services co-exist in workflows/
  without coupling.
* **Not changing the contract YAML format.** Service / handler
  declarations stay where they are; this ADR is about how the
  Python code that READS those contracts is organised.
* **Not touching the `*_main.py` console-script names** in
  `pyproject.toml`. Those are operator-facing and stable.

## Consequences

**Positive:**

* The deploy CLI bug chain unwinds — one resolver, one source of
  truth for which hooks are required.
* Future workflow additions have an obvious home (workflows/) and
  an obvious pattern to follow (the existing release / teardown
  services).
* The boundary becomes a ratchet, so drift is caught at PR time
  instead of at the next "fix the deploy CLI" session.
* commands/ shrinks to a coherent ~18-file entry-point tier
  (down from 33). Easier to grok at a glance.

**Negative:**

* Phase 3 + 4 are real refactors that touch many call sites. They
  need integration coverage (the `tests/unit/adapters/
  test_rebuild_and_bootstrap_main.py` + `test_deploy_stack_main.py`
  patches against module-level names will need updates).
* During the migration window (Phases 2-4), readers will see TWO
  shapes coexisting. Mitigation: deprecation comments naming the
  ADR + target Phase per shim, so anyone reading commands/
  deprecated code sees "this is going away in Phase X".
* The ratchet test (Phase 6) will need maintenance: every new
  workflow service is one more chance to accidentally import from
  commands/.

**Neutral:**

* No user-facing change. `media-stack-deploy`, `media-stack-teardown`,
  `bin/install/deploy-stack.sh`, etc. all keep working with their
  current CLI surface throughout the migration.
* No contract YAML change. Service registration via
  `compose_preflight_handler:` and `preflight_handler:` is untouched.
* The image build/push pipeline is unaffected. Each phase ships in
  a normal version-pinned image bake.

## Cross-references

* **ADR-0001 (repo restructure, Phase 12)** — introduced the
  `cli/workflows/` directory and console-script entry points. This
  ADR completes the boundary that Phase 12 started by clarifying
  what STAYS in commands/ and what MOVES to workflows/.
* **ADR-0011 (import direction)** — the ratchet test in Phase 6
  extends ADR-0011's leaf-direction invariant: domain/infrastructure
  shouldn't import application, and now workflows/ shouldn't import
  commands/.
* **ADR-0012 (loose functions / staticmethods → zero)** — Phase 6's
  `_clean` cleanup contributes one ratchet point. Phases 2-4 are also
  expected to drop overall `@staticmethod` count as the orchestration
  code migrates (mixins become instance-methodised services).
* **ADR-0013 (retire `run-legacy-pipeline`)** — orthogonal but
  consistent direction; both ADRs push commands/ toward "entry-point
  shim" and workflows/+adapters/ toward "where the work happens."
* **Audit notes (2026-05-11)** — the file-by-file catalog this ADR
  is built from. The 6 phases trace 1:1 to the audit's "Top 5
  inconsistencies" + the verified duplicates table at the top of
  this document.

## Phase tracking

This ADR is the source of truth for the work plan; mark phases as
they land. Each phase should reference back to this ADR in its
commit body.

| Phase | Status | Landed commit |
|---|---|---|
| Phase 1 — Narrow exceptions | proposed | — |
| Phase 2 — Unify script runners | proposed | — |
| Phase 3 — Deploy config consolidation | proposed | — |
| Phase 4 — Deploy pipeline migration | proposed | — |
| Phase 5 — `maintenance.py` migration | proposed | — |
| Phase 6 — Boundary ratchet + cleanup | proposed | — |

---

**Project Steward**
Matthew Loschiavo · [matthewloschiavo.com](https://matthewloschiavo.com) · [mploschiavo@gmail.com](mailto:mploschiavo@gmail.com)
