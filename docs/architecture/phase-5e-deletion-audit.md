# ADR-0003 Phase 5e — deletion audit

**Status:** Audit complete (2026-05-02). Updates the deletion scope
the original ADR-0003 sketched.

## TL;DR

The original ADR-0003 Phase 5e listed six legacy code paths to
delete. Audit finds **five of the six are still load-bearing** for
non-orchestrator flows (bootstrap, manual job invocation, cron, the
fresh-install verifier). Only one (the legacy probe-promises CLI)
is genuinely deletable, and it's blocked by the verify-fresh-install
shell script — unblocked by ADR-0004 (promise-driven verifier).

The orchestrator covers **continuous-mode self-heal**. It does NOT
cover:

- Bootstrap-phase one-shot setup (compose_preflight_handler,
  phase_scripts)
- JobRunner-driven jobs the orchestrator doesn't dispatch (manual,
  cron, bootstrap-only ensurers)
- Pre-controller deploy-time hooks (compose_preflight runs BEFORE
  the controller container is up)

Deleting these without first replicating their behavior in the
orchestrator (or moving the use case INTO the orchestrator) breaks
fresh deploys.

## Per-path findings

### 1. `compose_preflight_handler` field

**Consumers:**
- `adapters/compose/plugin.py:123` — passes handlers to compose runner
- `cli/commands/deploy_stack_config_resolution.py:388` — extracts handler list
- `cli/workflows/deploy_hook_config_resolver.py:209` — reads from contract YAML
- `contracts/services/{qbittorrent,sabnzbd,jellyfin}.yaml` — declare handlers

**What it does:** runs **before the controller container starts**,
during `docker compose up`. Sets up service prerequisites (e.g.,
qBit's WebUI password sync, SAB API key access) that the controller
will later need.

**Orchestrator coverage:** None possible — the orchestrator runs
INSIDE the controller. By the time it could probe, these
prerequisites must already be in place.

**Verdict: KEEP.** Load-bearing for compose deploy. Cannot be
moved into the orchestrator.

### 2. `phase_scripts.media_server_bootstrap` field

**Consumers:**
- `services/controller_component_resolver.py:359` — per-service phase script lookup
- `infrastructure/jobs/bootstrap_config_generator.py:128` — copies into adapter_hooks
- `cli/commands/controller_all_main.py:386` — reads runner_phase_scripts
- `cli/workflows/controller_core_phases_service.py:111` — invokes during bootstrap

**What it does:** declares a phase-specific bootstrap script
(currently used by Jellyfin's `ensure_jellyfin_controller_main`)
that runs once during the media_server phase of bootstrap.

**Orchestrator coverage:** Partial. The orchestrator's
`JellyfinLifecycle.mint_api_key` covers what
`ensure_jellyfin_controller_main` does in continuous mode. But
during the first 60s after a fresh deploy, the orchestrator hasn't
yet ticked, and bootstrap-phase jobs that depend on a minted key
need it to already be there.

**Verdict: KEEP for now.** Could be retired AFTER moving the
orchestrator's first tick into the bootstrap critical path —
that's a real design change, not a delete.

### 3. `_run_preflights` (in `application/jobs/controller_handlers.py`)

**Consumers:**
- `services/apps/core/job_adapters.py:1898` — wraps preflight runs in a job
- `application/jobs/controller_handlers.py:198` — definition

**What it does:** runs `plugin.preflight_handler` declarations
during a job. Per-job preflight (e.g., wait for service to be
reachable before running a config job).

**Orchestrator coverage:** The orchestrator's `<service>-running`
promise covers the same invariant in continuous mode. But
`_run_preflights` is invoked synchronously inside specific jobs
during bootstrap. Replacing it would require those jobs to gate on
orchestrator promise state, which means coupling the bootstrap DAG
to the orchestrator's tick state — a meaningful refactor.

**Verdict: SUBSUMABLE WITH WORK.** Not deletable today; could be
retired as part of a "bootstrap consumes orchestrator state"
follow-up phase.

### 4. `_try_satisfy_prereqs` (in `application/jobs/framework.py`)

**Consumers:**
- `application/jobs/framework.py:466` — called inside JobRunner.run()
- `application/jobs/framework.py:637` — definition

**What it does:** JobRunner's prereq-satisfaction loop. When a Job
has `requires=[...]`, JobRunner waits + retries until the prereqs
are satisfied (e.g., service reachable, API key present).

**Orchestrator coverage:** The orchestrator's `depends_on` graph
covers this in promise-driven flows. But JobRunner is still called
for many non-promise flows: bootstrap (which builds the full job
DAG), manual job invocations from the dashboard, cron-triggered
runs, the auto-heal cycle's per-job hooks (`guardrails:evaluate`,
`jobs:close-stale-runs`).

**Verdict: STILL LOAD-BEARING.** JobRunner needs prereq logic for
non-promise-driven invocations. Cannot delete without moving every
JobRunner caller through the orchestrator.

### 5. `max_attempts` retry loop in `JobRunner.run()`

**Consumers:** every JobRunner invocation that has prereqs.

**What it does:** retries up to `Job.max_attempts` times when
prereqs aren't satisfied. Default 3, configurable per-job in the
contract YAML.

**Orchestrator coverage:** The cooldown tracker provides the
equivalent for promise-driven flows.

**Verdict: STILL LOAD-BEARING.** Same reason as #4 — retire only
after every caller flows through the orchestrator.

### 6. `media-stack-probe-promises` CLI

**Consumers:**
- `bin/test/verify-fresh-install.sh:114` — the only live consumer
- `docs/reference/cli/index.md` — docs reference
- `docs/reference/promises.md` — operator docs
- `docs/architecture/promises-registry.md` — architecture doc

**What it does:** runs every promise's probe from the operator's
host shell, reports pass/fail. The acceptance gate for fresh-deploy
verification.

**Orchestrator coverage:** The orchestrator runs the same probes
every 60s and persists state. But the orchestrator runs INSIDE the
controller, and the verifier CLI runs OUTSIDE — different network
context (host:port mappings vs internal DNS).

**Verdict: DELETED in v1.0.311.** ADR-0004's `FreshInstallVerifier`
+ `media-stack-verify` CLI shipped in v1.0.310; the shell script
swapped to the new CLI; this CLI was truly orphan and was deleted
in v1.0.311 along with its entry-point registration and back-compat
alias.

## Revised Phase 5e scope

The original ADR scope of "delete six legacy paths in ~3 days"
doesn't survive contact with reality. Honest reframing:

| Original 5e item | Revised disposition |
|------------------|---------------------|
| `compose_preflight_handler` | **Keep** — pre-controller; orchestrator can't cover |
| `phase_scripts.media_server_bootstrap` | **Defer** to a future "bootstrap consumes orchestrator state" phase |
| `_run_preflights` | **Defer** — same as above |
| `_try_satisfy_prereqs` | **Keep** — JobRunner needs it for non-promise flows |
| `max_attempts` retry loop | **Keep** — same reason |
| `media-stack-probe-promises` CLI | **Deleted in v1.0.311** (after ADR-0004 6.1-6.4 shipped in v1.0.310) |

Already done in 5e.1: `_evaluate` extracted out of the CLI module
to `infrastructure/promises/assert_eval.py`. That work stands.

## What 5e looks like going forward

**5e.2 — shipped in v1.0.311.** The CLI was deleted; entry-point,
back-compat alias, and the test that pinned the alias all came
out together. The other five originally-scoped paths remain
load-bearing per the per-path verdicts above.

**5e.3+ (deferred to ADR-0005):** "bootstrap consumes
orchestrator state". Goal: make the orchestrator's first tick part
of the bootstrap critical path so `_run_preflights` /
`phase_scripts.media_server_bootstrap` /
`jellyfin:ensure-api-key` (the bootstrap-phase job) can be retired.
Multi-week design + implementation, scoped in ADR-0005.

## What this audit changes about ADR-0003

Phase 5e's "delete legacy preflight code paths" line was based on
a cleaner separation than reality offers. Recommend updating
ADR-0003's Phase 5e description to reflect the audit findings.
The architectural goal (orchestrator owns the heal loop) is
achieved as of Phase 5d; the deletion work is more nuanced than
the original sketch.
