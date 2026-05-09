# ADR-0009 — Trigger-driven Jobs framework: every action is a contract

**Status:** Accepted (2026-05-08). Phase 6 of the orchestration
unification effort. Builds on ADR-0003 (lifecycle/promise framework)
and ADR-0005 (bootstrap consumes orchestrator state — closed Phase 5
at v1.0.325 with bootstrap running through
``JobRunner.run("bootstrap-deployment")``). Sister effort to
ADR-0007 (OpenAPI-driven routing — same "contract is source of
truth" principle on the HTTP surface). Companion to ADR-0010
(Phase 7 — primitive-layer pluggability), written in parallel.

## Context

Phase 5 closed the bootstrap snowflake but left a surrounding ring
of hardcoded action-name branches in the controller's main loop and
in three API services. Each is a small site; the pattern is the
problem. The framework knows how to run a Job; it does not know how
to wire two Jobs together except by hand-editing the controller.

Audit of ``src/media_stack/`` after v1.0.325 — six call sites in
``cli/commands/controller_serve.py`` plus three in API services
still branch on hardcoded action names or call hand-rolled
side-effects gated on specific completions:

| File | Line | Hardcoded shape |
|---|---|---|
| ``src/media_stack/cli/commands/controller_serve.py`` | 576 | Auto-runs bootstrap on controller startup if ``deployment_state.initial_bootstrap_done`` is false. Hardcodes the action name and the start-time gating. |
| ``src/media_stack/cli/commands/controller_serve.py`` | 730 | Recovery cascade triggered when ``action_name == "bootstrap-deployment"`` completes. Calls a hardcoded sequence. |
| ``src/media_stack/cli/commands/controller_serve.py`` | 735 | Hardcoded recovery list inside the cascade. |
| ``src/media_stack/cli/commands/controller_serve.py`` | 758 | Heal-timer trigger when ``action_name == "reconcile"`` completes. |
| ``src/media_stack/cli/commands/controller_serve.py`` | 772 | Hardcoded heal sweep. |
| ``src/media_stack/cli/commands/controller_serve.py`` | 786 | ``mark_initial_bootstrap_done()`` gated on ``action_name == "bootstrap-deployment"`` and orchestrator state. |
| ``src/media_stack/api/services/auth_config.py`` | 342 | ``action_trigger("bootstrap")`` (hardcoded name, legacy shim). |
| ``src/media_stack/api/services/post_config_writes.py`` | 407 | Same shim. |
| ``src/media_stack/api/services/auto_heal.py`` | 467 | References ``"orchestrator:satisfy-shadow"`` in a bespoke poller. |

The shape is consistent: framework drives Jobs; controller drives
the *ordering* between Jobs by branching on names. That branching is
the snowflake. Adding a new "after bootstrap, also do X" requires
editing ``controller_serve.py`` and shipping a controller release;
the contract YAMLs that describe every other aspect of a Job
(prerequisites, steps, schedule) have no say.

## Decision

**Everything is a Job. The contract is the source of truth.
``JobRunner.run`` is the only entry point. Triggers are declarative.**

Job contracts gain a ``triggers:`` block. Five trigger kinds total,
fixed and framework-managed:

```yaml
name: post-bootstrap-recovery
triggers:
  - event: job.completed
    job: bootstrap-deployment
prerequisites: [...]
steps: [...]
```

The trigger key is ``event:`` (not ``on:``) because PyYAML parses
the bare key ``on:`` as the boolean ``True`` under YAML 1.1's
deprecated ``y/yes/n/no/on/off`` alias set. Renaming avoids
silent breakage and the brittleness of having to quote every
contract.

Trigger kinds:

* ``manual`` — UI Run-Now (default if no ``triggers`` block).
* ``schedule`` — cron-style; registers with the existing scheduler
  (reuse, not parallel).
* ``job.completed`` / ``job.failed`` — fires when a named Job emits
  the lifecycle event.
* ``promise.satisfied`` / ``promise.violated`` — fires on
  orchestrator scope state change.
* ``controller.started`` — one-shot at controller boot.

A ``when:`` clause on a trigger adds a state predicate from a
**closed registry**. The registry is small, named, and
framework-managed — NOT arbitrary expressions. Examples:
``initial_bootstrap_pending``, ``disk_lockdown_active``. Plugins can
register new predicates the same way they register Jobs (named entry
in a contract YAML; framework loads + validates).

A new ``TriggerEngine`` (~150 LoC) is a framework-internal helper:

* At boot, reads all loaded contracts and builds the static
  ``event → [job-name]`` index.
* Performs cycle detection on the static graph; fails boot if a
  cycle exists.
* JobRunner's existing lifecycle hooks (``start`` / ``complete`` /
  ``fail``) call ``TriggerEngine.dispatch(event)``.
* Orchestrator's ``satisfy_scope`` calls
  ``TriggerEngine.dispatch(promise.satisfied|violated)``.
* ``TriggerEngine`` looks up matches and enqueues them via
  ``JobRunner.run``.
* For ``event: schedule`` triggers, the engine registers entries with
  the existing scheduler (not a new one).

No event bus. No subscriber API. No listener files. The
``TriggerEngine`` is private to the framework. Plugins extend by
shipping contract YAMLs that the existing contract loader already
picks up.

## How each snowflake collapses

| Today | After Phase 6 |
|---|---|
| ``controller_serve.py:576`` auto-run | ``bootstrap-deployment.yaml`` gains ``triggers: [event: controller.started, when: initial_bootstrap_pending]`` |
| ``controller_serve.py:730`` recovery cascade | New ``post-bootstrap-recovery.yaml`` with ``triggers: [event: job.completed, job: bootstrap-deployment]`` |
| ``controller_serve.py:758`` post-reconcile heal | New ``heal-sweep.yaml`` with ``triggers: [event: job.completed, job: reconcile]`` |
| ``controller_serve.py:786`` mark-initial-bootstrap-done | New ``mark-initial-bootstrap-done.yaml`` with ``triggers: [event: promise.satisfied, scope: initial-bootstrap]`` |
| ``auth_config.py:342``, ``post_config_writes.py:407`` ``action_trigger("bootstrap")`` | Plain ``JobRunner.run("bootstrap-deployment")``. ``action_trigger`` shim deleted. |
| ``auto_heal.py:467`` ``"orchestrator:satisfy-shadow"`` | New ``shadow-satisfaction.yaml`` with ``triggers: [event: schedule, every: 30s]``. Bespoke poller deleted. |

After this lands, **zero** call sites in ``src/media_stack/`` branch
on ``action_name``. The framework drives behavior; names are pure
identifiers for logs and UI.

## Schema

```yaml
name: post-bootstrap-recovery
triggers:
  - event: job.completed
    job: bootstrap-deployment
  - event: promise.satisfied
    scope: initial-bootstrap
    when: initial_bootstrap_pending
  - event: schedule
    every: 30s
  - event: controller.started
    when: initial_bootstrap_pending
prerequisites:
  - bootstrap-deployment.completed
steps:
  - ...
```

Loader rules:

* Unknown ``event:`` kinds rejected at parse.
* Unknown ``when:`` predicates rejected at parse.
* Missing required field for a kind rejected at parse
  (``job.completed`` requires ``job:``;
  ``promise.satisfied`` requires ``scope:``;
  ``schedule`` requires ``every:`` or ``cron:``).
* Absent ``triggers:`` block defaults to a single ``manual`` entry.

The Pydantic model lives alongside the existing Job-contract model
at ``contracts/services/_schema.py``; ``triggers`` is an optional
field with a discriminated union over ``event``.

## Phases

### Phase 6.1 — Schema extension

Add ``triggers:`` and ``when:`` blocks to the Job-contract Pydantic
model. Loader rejects unknown trigger kinds and unknown ``when:``
predicates. Tests on parse + validate cover every trigger kind, the
default-when-absent behavior, and the rejection paths.

**Deliverables**: schema additions, loader hooks, ~25 unit tests.
No runtime behavior change — contracts can declare ``triggers``;
nothing acts on them yet.

### Phase 6.2 — ``TriggerEngine`` class

New ``application/jobs/triggers/engine.py`` (~150 LoC). Builds the
static ``event → [job-name]`` index at boot from loaded contracts.
Performs cycle detection on the static graph; fails boot if a cycle
exists. For ``event: schedule``, registers entries with the existing
scheduler (the same one ``operations.yaml`` schedule entries flow
through — no parallel scheduler).

The engine is framework-internal. No public API beyond
``dispatch(event)`` (called only by JobRunner + Orchestrator) and
``register_predicate(name, callable)`` (called only by the
framework's own predicate registry).

**Deliverables**: 1 new module, ~30 unit tests covering index
construction, cycle detection (linear, self-loop, mutual,
three-way), schedule-trigger registration, predicate-evaluation
short-circuiting.

### Phase 6.3 — Wire lifecycle hooks

JobRunner's existing ``start`` / ``complete`` / ``fail`` lifecycle
hooks gain a ``TriggerEngine.dispatch`` call. Orchestrator's
``satisfy_scope`` gains the same for ``promise.satisfied`` /
``promise.violated``. Controller boot fires ``controller.started``
once.

These are framework-internal lifecycle hooks. No new public events
surface. The existing event stream (``/api/jobs/running``,
``/api/jobs?history``) continues to be the operator-visible record;
``TriggerEngine`` is below it.

**Deliverables**: 4 hook sites, ~15 integration tests covering
each event kind firing on the correct lifecycle state.

### Phase 6.4 — New contracts + delete the 6 hardcoded branches

Author 5 new Job contracts:

* ``post-bootstrap-recovery.yaml`` — triggers on
  ``job.completed: bootstrap-deployment``. Steps mirror the current
  cascade body.
* ``mark-initial-bootstrap-done.yaml`` — triggers on
  ``promise.satisfied: initial-bootstrap``. Single step writes the
  flag.
* ``heal-sweep.yaml`` — triggers on
  ``job.completed: reconcile``. Steps mirror the current sweep
  body.
* ``shadow-satisfaction.yaml`` — triggers on
  ``schedule: every: 30s``. Replaces the bespoke poller.
* ``bootstrap-deployment.yaml`` — gains
  ``triggers: [event: controller.started, when: initial_bootstrap_pending]``.

Delete the 6 hardcoded branches in
``src/media_stack/cli/commands/controller_serve.py`` (lines 576,
730, 735, 758, 772, 786 in the audit table above).

**Deliverables**: 4 new YAMLs, 1 YAML edit, ~120 LoC removed from
``controller_serve.py``. Existing integration tests for bootstrap +
reconcile + auto-heal continue to pass against the contract-driven
shape.

### Phase 6.5 — Migrate API-service shims

Migrate ``src/media_stack/api/services/auth_config.py:342`` and
``src/media_stack/api/services/post_config_writes.py:407`` from
``action_trigger("bootstrap")`` to plain
``JobRunner.run("bootstrap-deployment")``. Delete the
``action_trigger`` shim (the legacy facade is unused after this).

Migrate ``src/media_stack/api/services/auto_heal.py:467``
to fire by virtue of the ``shadow-satisfaction`` contract's
schedule trigger; delete the bespoke poller.

**Deliverables**: 3 call-site edits, 1 shim module deleted,
~60 LoC net reduction.

### Phase 6.6 — Architecture ratchet

New test
``tests/unit/architecture/test_no_hardcoded_action_names_in_runtime.py``.
Greps ``src/media_stack/`` for ``action_name ==``,
``name == "bootstrap"``, ``name == "reconcile"``, and similar
shapes, outside ``application/jobs/contracts/`` (the YAMLs
themselves are allowed to name jobs) and the ``TriggerEngine``
module (which is the one place that maps names to dispatch).
Fail-on-introduction.

**Deliverables**: 1 ratchet test pinning the invariant going
forward. Bypasses follow the standard ratchet-discipline rule — no
silent allowlist bumps.

### Phase 6.7 — Bake/soak (deferred)

Phase 6 ships bundled with Phase 7 in ADR-0010. Single bake; single
soak; both layers cut over together. The bake/soak entry is here
only to record that the live-validation step is acknowledged but
deferred to the companion ADR's release.

## Caveats

Three items this ADR explicitly does NOT solve:

1. **``when:`` is constrained.** Predicates come from a closed
   registry, not arbitrary expressions. Avoids contracts becoming a
   programming language. New predicates land via the same
   contract-loader extension path the framework uses for Job kinds.
2. **Schedule triggers reuse the existing scheduler**, never build
   a parallel one. Same principle as the rest of the design.
3. **Ensurer-level pluggability is Phase 7** (ADR-0010). Phase 6
   unifies the orchestration layer; Phase 7 unifies the primitive
   layer underneath. Both worth doing; not the same effort.

## Alternatives considered

### Event bus + subscriber API

A first-class event bus (``EventBus.subscribe(event, handler)``)
with handler files registering at startup. Rejected: introduces a
runtime registration surface alongside the existing contract loader,
two ways to wire a Job. The contract YAML is already the source of
truth for everything else about a Job (prerequisites, steps,
schedule); triggers belong there too. The event bus would be a
parallel system.

### Free-form ``when:`` expressions

A small expression language (``when: "disk.used_pct > 80 and
hour < 6"``). Rejected: contracts become a programming language;
debugging shifts from "read the YAML" to "evaluate the expression";
plugin authors get a foot-cannon. The closed predicate registry
gives the same coverage at a tenth of the surface area.

### Keep snowflakes; codify the pattern

Document "the controller branches on action names" as the official
pattern and call it done. Rejected: the snowflakes already bypass
the contract layer that everything else respects. Codifying the
bypass institutionalizes the gap. The framework's value is "one way
to wire a Job"; six exceptions is too many.

### Replace the scheduler too

The existing scheduler reads ``operations.yaml`` cron entries.
Tempting to fold ``schedule`` triggers into a unified scheduler
written from scratch. Rejected: the existing scheduler works; a
rewrite buys nothing and risks subtle behavior drift. Phase 6.2
registers ``schedule`` triggers with it directly.

## Consequences

### Positive

* One way to wire a Job. The contract YAML is the source of truth
  for *what* it does, *when* it runs, and *who* triggers it.
* Adding "after X, also do Y" is a YAML edit, not a controller
  release.
* ``controller_serve.py`` shrinks by ~120 LoC of hand-rolled
  branching. The main loop becomes "boot the framework" — full
  stop.
* The architecture ratchet pins the invariant going forward; new
  branches can't sneak in without a ratchet bump and a justification.
* Plugin authors ship contract YAMLs without touching framework
  internals.

### Negative

* Cycle detection at boot adds a small startup cost (~ms) and a new
  failure mode ("contract YAMLs declare a cycle"). Operator-facing
  message has to be clear; bake/soak step (deferred to ADR-0010)
  validates the wording.
* The ``when:`` predicate registry is one more thing to maintain.
  Mitigated by keeping the registry small and named — same shape as
  the existing ``ensured_by`` ``{type: lifecycle, ...}`` registry.
* Five new YAMLs to keep coherent with the corresponding deletions
  in ``controller_serve.py``. The migration commit (Phase 6.4) is
  the one place tearing is possible; CI catches it via the
  integration tests.

### Neutral

* Existing Job contracts unchanged unless they want triggers. The
  default ``manual`` (no ``triggers:`` block) preserves today's
  shape for every contract that doesn't opt in.
* ``JobRunner.run`` signature unchanged. ``TriggerEngine`` calls it
  the same way operator-UI and CLI do.
* OpenAPI surface unchanged. ``TriggerEngine`` is framework-private;
  no new endpoints.

## Stewardship

Owner: orchestration / Jobs subgraph. Reviewed alongside the
existing ``JobRunner``, ``Orchestrator.satisfy_scope``, and the
contract-loader infrastructure. The ratchet pinning the invariant
(``test_no_hardcoded_action_names_in_runtime.py``) is the durable
defense; new branches require an explicit ratchet entry with
justification.

Rollback: each phase is its own commit. 6.1 (schema) reverts
cleanly with no runtime impact. 6.2 (engine) reverts to a
no-trigger state. 6.3 (hooks) reverts the dispatch calls. 6.4 + 6.5
(YAMLs + call-site migrations) revert as a pair — the new YAMLs
delete; the deleted ``controller_serve.py`` branches restore. 6.6
(ratchet) reverts the test. None of these are destructive; the
intermediate states all run.

## Relationship to other ADRs

* **ADR-0003** (lifecycle/promise framework foundation): Phase 6's
  ``TriggerEngine`` is wired through the existing JobRunner +
  Orchestrator lifecycle hooks. No new framework primitive — uses
  what ADR-0003 already shipped.
* **ADR-0005** (bootstrap consumes orchestrator state): Phase 5
  closure (bootstrap is a Job) is the prerequisite for Phase 6. The
  ``bootstrap-deployment`` contract gaining ``triggers:`` is only
  meaningful because Phase 5 made bootstrap a Job in the first
  place.
* **ADR-0007** (OpenAPI-driven routing): sister "contract is source
  of truth" effort on the HTTP surface. Same principle; different
  layer.
* **ADR-0010** (Phase 7 — primitive-layer pluggability): companion
  ADR written in parallel. Phase 6 unifies the orchestration layer
  (Jobs + their triggers); Phase 7 unifies the primitive layer
  (ensurers + their wiring). Bake/soak is shared between the two.
