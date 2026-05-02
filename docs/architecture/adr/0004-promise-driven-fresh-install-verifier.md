# ADR-0004 — Promise-driven fresh-install verifier

**Status:** Draft (2026-05-02). Builds on ADR-0003. ~1 week of focused
work, or ~2 as a side stream.

## Context

`bin/test/verify-fresh-install.sh` is the operator-facing
acceptance gate for "did this fresh deploy actually work?". Today
it shells out to the legacy `media-stack-probe-promises` CLI which
predates ADR-0003's orchestrator. The CLI:

1. Loads `contracts/promises/promises.yaml` independently
2. Runs probes from OUTSIDE the controller container (operator's
   host shell), so all URLs resolve via published host:port mappings
   instead of internal Docker/cluster DNS
3. Has its own probe-type dispatch table (HTTP / file / k8s_*)
4. Has its own assert-expression evaluator (now extracted to
   `infrastructure.promises.assert_eval` in Phase 5e.1, shared
   with the orchestrator)
5. Has its own k8s-mode handling that runs probes via
   `kubectl exec` into the controller pod

So we have **two implementations of the same conceptual probe loop**
— one in `application/services/orchestrator.py` running every 60s
inside the controller, one in `cli/commands/probe_promises.py`
running on demand from the operator's shell. Phase 5e.1 made them
share the assert-evaluator; everything else is parallel code.

This ADR collapses the two into one runtime + one client.

## Decision

Introduce **`FreshInstallVerifier`** — a Python class that produces
a structured pass/fail report by querying the orchestrator's
already-computed state, instead of independently re-probing the
world.

The verifier is an **API client** of the orchestrator. The
orchestrator runs every 60s and persists per-promise state to
`.controller/promise_state.json`; the verifier reads that file (or
the equivalent HTTP endpoint), summarizes, and reports.

```
┌─────────────────────────────────────────────────────────────┐
│ Operator's host                                              │
│   bin/test/verify-fresh-install.sh                           │
│     ↓ (exec)                                                 │
│   media-stack-verify  ←── new thin CLI                       │
│     ↓ (instantiate + run)                                    │
│   FreshInstallVerifier                                       │
│     ↓ (HTTP)                                                 │
└─────────────────────┼───────────────────────────────────────┘
                       │
┌──────────────────────┼─────────────────────────────────────┐
│ Controller container │                                      │
│                      ↓                                      │
│   GET /api/orchestrator/promises/state                       │
│     ↓ reads                                                  │
│   .controller/promise_state.json                             │
│                                                              │
│   The orchestrator's auto-heal cycle wrote this file on its  │
│   most recent tick (≤60s ago). Returns per-promise           │
│   PromiseAttempt + tick metadata.                            │
└─────────────────────────────────────────────────────────────┘
```

## Class shape

```python
# src/media_stack/application/verifier/fresh_install.py

@dataclass(frozen=True)
class VerificationResult:
    """One verification cycle's full report."""
    started_at: float
    elapsed_seconds: float
    total: int
    passed: int
    failed: list[PromiseAttempt]
    skipped: list[PromiseAttempt]
    unknown: list[PromiseAttempt]
    is_acceptance_pass: bool  # operator-friendly bool
    detail_lines: list[str]   # human-readable summary


class FreshInstallVerifier:
    """Promise-driven verifier for fresh-install acceptance.

    Two modes:

      * ``inside_controller=False`` (default, external client):
        connects to the controller's API, reads the orchestrator's
        latest tick state. Used by ``verify-fresh-install.sh``
        from the operator's host shell.

      * ``inside_controller=True``: instantiates the orchestrator's
        dispatcher locally and runs ``satisfy_promises(dry_run=True)``
        directly. Used by in-controller test fixtures and by the
        orchestrator's own CLI (``bin/ops/orchestrator-eval.sh``).

    Both modes return the same ``VerificationResult`` shape so
    callers don't care which path produced it.
    """

    def __init__(
        self,
        *,
        controller_url: str = "",
        admin_user: str = "",
        admin_pass: str = "",
        platform: str = "compose",
        inside_controller: bool = False,
        require_fresh_tick_within_seconds: float = 90.0,
        timeout_seconds: float = 30.0,
    ) -> None:
        ...

    def verify(self) -> VerificationResult:
        """Run one verification cycle. Always returns a result;
        connection / auth errors land in ``unknown``, not raised."""
        ...

    def wait_for_steady_state(
        self, *, max_wait_seconds: float = 300.0,
    ) -> VerificationResult:
        """Poll the controller until either:
          - all applicable promises are ``ok`` (pass)
          - timeout reached (return last result, fail)
          - any promise reaches ``failed_permanent`` (fail fast)
        Used by CI to give the orchestrator time to converge after
        a fresh deploy."""
        ...
```

## Migration plan

**Phase 6.1** (~half day) — controller-side endpoint:

- Add `GET /api/orchestrator/promises/state` to `api/handlers_get.py`
- Returns the latest tick's `TickSummary` + per-promise `PromiseAttempt`
  list, sourced from `promise_state.json`
- Auth: same admin-basic the existing /api/* endpoints use
- Returns 503 with a `last_tick_age_seconds` field if the file is
  stale (>120s) — protects against showing pre-restart state

**Phase 6.2** (~1 day) — Python class:

- `application/verifier/fresh_install.py::FreshInstallVerifier`
- External-client mode (inside_controller=False) — pure HTTP client
- Tests: mock the API endpoint, pin the result-shape contract
- ~200 LOC + tests

**Phase 6.3** (~half day) — new CLI:

- `bin/test/media-stack-verify` (or similar) — thin wrapper that
  instantiates the verifier and prints results. JSON output via
  `--json` for CI integration.
- Argparse: `--controller-url`, `--admin-user`, `--admin-pass`,
  `--wait`, `--timeout`, `--json`
- Same flag shape as the legacy CLI so `verify-fresh-install.sh`
  swap is a one-line change

**Phase 6.4** (~half day) — switchover:

- Update `verify-fresh-install.sh` to invoke the new CLI
- Test on a real fresh install (compose: `down -v && up`, k8s:
  `apply -k && wait`)
- The legacy `media-stack-probe-promises` CLI stays for one release
  for rollback safety; removed in Phase 6.5

**Phase 6.5** (~1 day, AFTER one stable release of 6.4) — delete
legacy:

- Remove `cli/commands/probe_promises.py`
- Remove its CLI registration
- Update docs (`docs/reference/cli/index.md`,
  `docs/reference/promises.md`, `docs/architecture/promises-registry.md`)
- This unblocks ADR-0003 Phase 5e.2 which was waiting on this work

## What we get

- **Single runtime** — orchestrator and verifier are the same code
  path; an operator running the verifier sees exactly what the
  orchestrator's been doing for the last minute
- **Honest acceptance signal** — verifier is no longer a parallel
  implementation that could pass/fail differently than the live
  pipeline
- **Test-friendly** — `FreshInstallVerifier` is a regular Python
  class; importable from pytest fixtures, mockable, etc. The
  current `media-stack-probe-promises` is invokable only via
  subprocess
- **k8s + compose uniformity** — both modes go through the same
  HTTP endpoint; no `--k8s --unified` mode-switching at the CLI
- **Deletion path** — the only reason `media-stack-probe-promises`
  CLI is still alive is `verify-fresh-install.sh`. Once that
  switches to the new CLI, Phase 5e.2 (delete the legacy CLI)
  becomes a one-commit cleanup

## What we lose

- **CLI-only invocation** — `media-stack-probe-promises` could run
  against a stack the operator hadn't yet enabled the new framework
  on. The new verifier requires the controller to be up + serving
  the API. Mitigation: the verifier's `wait_for_steady_state` polls
  the controller's `/api/health` first; if the controller isn't
  reachable yet, it waits before declaring failure
- **Independent probe code** — if the orchestrator has a probe-
  dispatch bug, the verifier has the same bug. (But that's
  arguably the point — the verifier should agree with the live
  pipeline, not double-check it from a parallel implementation)

## Test plan

- Unit tests: mock the API endpoint, verify `VerificationResult`
  shape on pass / fail / partial / network-error
- Integration test: spin up a controller in test mode with a
  hand-rolled `promise_state.json`, verify the verifier reads it
  correctly
- E2E test: existing `verify-fresh-install.sh` against a fresh
  compose stack — passes today via the legacy CLI, MUST pass via
  the new verifier after Phase 6.4

## Open questions

1. **Where does the API endpoint live?** `api/handlers_get.py`
   (current GET-handler dispatcher) or a new
   `api/handlers/orchestrator.py`? Lean toward existing since the
   pattern is established.

2. **How does the verifier handle a controller that's mid-restart?**
   The orchestrator's tick file might be stale (last write was
   pre-restart). Treat `last_tick_age_seconds > 120` as
   "controller not steady yet" and retry until fresh, not as a
   failure?

3. **Should the verifier run a fresh tick or just read the latest?**
   Reading saves an orchestrator HTTP probe burst; running fresh
   gives a more honest "right now" snapshot. Lean toward "read by
   default, `--force-fresh-tick` to trigger one synchronously".

4. **Backwards compat for the shell script's flag shape.** The
   current script passes `--compose-file`, `--controller-url`,
   `--admin-user`, `--admin-pass`. The new CLI doesn't need
   `--compose-file` (the controller knows). Keep the flag for
   compat, ignore it? Or drop it and update the shell script?

## Stewardship

Same shape as ADR-0003: phased rollout, no cross-phase risk, one
release of soak between Phase 6.4 (switchover) and Phase 6.5
(delete legacy). Reversible at every phase via shell-script revert.
