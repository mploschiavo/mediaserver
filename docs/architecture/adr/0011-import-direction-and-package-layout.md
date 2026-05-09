# ADR-0011 — Import direction discipline + package-layout cleanup

**Status:** Proposed (2026-05-08). Successor to the implicit assumptions
in ADR-0002 (hexagonal restructure). Targets the
``CIRCULAR_IMPORT_RISK_RATCHET`` count (391 at this writing) and,
more importantly, the **inverted-direction subset (119)** —
deferred imports that exist solely to paper over a layering
violation.

Authors: matthew

## Context

The codebase is structured around a hexagonal layout:
``domain → application → adapters/infrastructure → api/cli``. The
package directory tree mirrors the layers:

```
src/media_stack/
  domain/           pure value objects, business rules, Protocols
  core/             cross-cutting primitives (logging, ids)
  application/      use cases (orchestrator, jobs framework, etc.)
  infrastructure/   technical concerns (HTTP clients, registries)
  services/         legacy "shim" layer; mixed application/adapter content
  adapters/         per-service IO classes (Wirers, Lifecycles)
  api/              HTTP routes + DI wiring
  cli/              command-line entry points + boot wiring
```

The ``CIRCULAR_IMPORT_RISK_RATCHET`` (currently 391) counts every
function whose body contains a ``from media_stack.X import Y``
statement. The framing — "circular import risk" — is approximately
right but conflates four distinct patterns:

1. **Genuine circular-import workaround.** Module A and module B
   each want to import from the other; one of them has to defer.
2. **Lazy-load optimisation.** A heavy dependency (Docker SDK,
   k8s client) is only needed on a hot path; deferring keeps
   import-time fast.
3. **Optional-dependency guard.** ``import argon2`` works in
   prod but is missing in some test environments; deferring lets
   the module load even where the dep is absent.
4. **Test-time hook.** Defer to allow ``patch(...)`` to swap the
   target before the function runs.

Only (1) is architectural debt. (2)–(4) are legitimate.

A static audit (May 2026) classified the 391 by direction:

| Direction | Count | Meaning |
|---|---|---|
| Inverted (inner layer reaches outward) | **119** | Cycle workaround — debt |
| Same-layer or outer→inner | 272 | Lazy-load / optional / test hook |

So the actual debt is **119**, and a single module —
``api/services/registry.py`` — accounts for **56** of those
(deferred-imported by ~half the inverted call sites). The rest of
the 119 is a long tail across ``api/services/{config, key_formats,
health, content, ...}`` and a smaller cluster of ``services/ →
api/`` plus ``adapters/ → api/`` cases.

The single-module concentration is the key architectural insight:
this isn't 119 separate cycles. It's one (or a handful of)
misplaced module(s) being deferred-imported from every layer that
legitimately needs it. **Move the modules into the layer that
matches their actual responsibility, and most of the count
disappears in one diff.**

### Why "just defer the import" is the wrong fix

Every deferred import is a TODO comment that compiles. Python's
import system happily loads it; pytest happily collects the test;
the deployment runs. But the architectural shape — "X depends on
Y" — gets hidden, and the dependency direction becomes invisible
to the type checker, the linter, and any human reading the file.

When that pattern propagates:

* Type-checking misses cycles (mypy/pyright don't follow
  function-body imports for cycle detection).
* Refactor tools can't see the dependency.
* Boot-order bugs surface only at runtime, often only in
  production-shaped paths (the test fixture imports things in a
  different order than ``cli/serve``).
* New contributors see "the team uses deferred imports" and
  reach for the same hammer instead of asking why the module is
  in the wrong place.

This ADR is the inflection point. The intent is not to ban
deferred imports — they're the right tool for buckets (2)–(4).
The intent is to keep bucket (1) at zero and to put the layout
work in writing so future changes don't drift back.

### Concrete violations at the time of writing

**Domain-layer violations (must be zero in a hexagonal layout):**

```
src/media_stack/domain/jobs/types.py::run()
  → from media_stack.services import runtime_platform
src/media_stack/domain/media_integrity/secret_scrub.py::_structural_message()
  → from media_stack.services.media_integrity.adapters._servarr_base
```

**Top "magnet" modules** (deferred-imported from outside their layer):

| Magnet module | Inverted-deferred imports |
|---|---|
| ``api.services.registry`` | 56 |
| ``api.services.config`` | 10 |
| ``api.services.key_formats`` | 8 |
| ``api.services.health`` | 8 |
| ``api.services`` (package) | 4 |
| ``api.services.content`` | 3 |
| ``services.jobs.framework`` | 3 |
| Other ``api.services.*`` long tail | ~7 |
| Non-``api.services`` (mixed) | ~20 |

The ``api.services.registry`` module is a service catalog loaded
from ``contracts/services.yaml`` at import time. It is referenced
by adapters, infrastructure, and the api itself. It is NOT
api-specific in any meaningful sense; it's the canonical place to
ask "what services does this deployment manage?". Its location
under ``api/services/`` is historical — it predates the current
layer convention — and is the single biggest reason the
inverted-direction count is what it is.

## Decision

**One layout invariant, three rules, four phases.**

### The invariant

Every Python module in ``src/media_stack/`` belongs to exactly
one layer. Layers form a strict directed acyclic graph; module-top
imports may only go inward (toward ``domain``). Any
``from media_stack.X import Y`` *inside a function body* must be
documented (one-line comment) as one of:

* **lazy-load** (heavy dep deferred until the hot path needs it)
* **optional-dep** (dep may be absent at import time)
* **test-hook** (deferred so ``patch`` can substitute)

If none of those apply, the import is bucket (1) — a cycle
workaround — and the module placement is wrong. Fix the placement;
don't add the deferred import.

### The three rules

1. **Domain is a leaf.** ``domain/`` and ``core/`` have **zero**
   outbound dependencies on any other ``media_stack`` layer.
   Ratchet: count of inverted-direction imports out of these
   layers MUST be zero.

2. **The magnet test.** If a module in layer L is
   deferred-imported by ≥3 distinct functions in layers below L,
   the module is in the wrong layer. Move it down to the layer
   where its actual users live.

3. **No re-export shims to break cycles.** Adding a new module
   that re-exports from another module purely to dodge a cycle is
   forbidden. Move the original; don't multiply directories.

### The shape we're moving toward

```
domain/                   leaf — pure value objects, Protocols
  ├── services/           ServiceLifecycle, Outcome, ProbeResult,
  │                       LifecycleHandlerAdapter, Promise types
  └── jobs/               Job, JobContext, RunRecord types

core/                     cross-cutting primitives (no media_stack
                          deps); already mostly leaf today
  ├── logging_utils.py
  ├── runtime_platform.py     ← MOVED here from services/
  ├── service_registry/       ← MOVED here from api/services/
  │                           registry.py (the 56-magnet module —
  │                           pure data, no application-layer deps,
  │                           so core/ avoids cascading layer-rule
  │                           breaks; see Phase 2 note)
  ├── key_formats/            ← MOVED here from api/services/
  │                           key_formats.py (same rationale)
  └── auth/                   csrf, etc.

application/              use cases (depends on domain + core)
  ├── jobs/                   framework, runner, dispatcher,
  │                           trigger engine
  ├── orchestrator/           promise orchestrator
  ├── service_health/         ← MOVED here from api/services/health
  └── service_config/         ← MOVED here from api/services/config

infrastructure/           technical concerns (depends on domain +
                          application)
  ├── promises/               registry loader, dispatcher
  ├── http_client/            HTTP plumbing
  └── persistence/            disk + k8s secret IO

adapters/                 per-service IO (depends on domain +
                          application + infrastructure)
  ├── _shared/                LifecycleWirerBase
  ├── jellyfin/               libraries_wiring, lifecycle
  ├── servarr/                per-arr wirers
  └── ... (one dir per service)

api/                      HTTP route handlers + DI wiring
                          (depends on everything below)
  ├── routes/                 each route module
  └── server.py

cli/                      command-line entry points
                          (depends on everything below)
  └── commands/serve, build_image, etc.
```

The ``services/`` directory at the top level — the legacy shim
layer — is **deleted in this redesign**. Its contents move to the
layer they actually belong in. (See Phase 3.)

## Phases

Each phase is small enough to land in one PR. Each ends with a
ratchet that pins the improvement so future changes can't undo
it.

### Phase 1 — Domain leaf restoration (must-do; ~30 min)

**Goal**: zero inverted-direction imports out of ``domain/``.

**Files (2):**

* ``domain/jobs/types.py::run()`` —
  ``from media_stack.services import runtime_platform``.
  ``runtime_platform.log`` is the only call. Inject the logger
  into ``Job.run`` via constructor (or class-level factory),
  removing the deferred import. Domain stays leaf.
* ``domain/media_integrity/secret_scrub.py::_structural_message()``
  — ``from media_stack.services.media_integrity.adapters._servarr_base``.
  Either move the helper into domain (if pure) or invert the
  control: have the adapter pass the helper into the scrubber.

**Ratchet additions:**

* ``test_no_inverted_imports_out_of_domain.py`` — scans
  ``domain/`` and ``core/`` for any function-body
  ``from media_stack.<other-layer> import …``. Pin to zero;
  fail-on-introduction.

**Estimated count delta**: 119 → **117** inverted (tiny, but the
domain leaf is non-negotiable).

### Phase 2 — Service registry relocation (biggest single win; ~1 day)

**Goal**: move the four shared "service catalog" modules from
``api/services/`` to ``application/`` (or where they actually
belong). This single phase eliminates **~80 of the 119 inverted
violations**.

**Modules to relocate:**

| Current location | New location | Inverted refs removed |
|---|---|---|
| ``api/services/registry.py`` | ``core/service_registry/registry.py`` | 56 |
| ``api/services/config.py`` | ``application/service_registry/config.py`` | 10 |
| ``api/services/key_formats.py`` | ``core/key_formats/`` | 8 |
| ``api/services/health.py`` | ``application/service_health/`` | 8 |
| ``api/services/content.py`` | ``application/service_registry/content.py`` | 3 |

**Note on ``registry.py`` and ``key_formats.py`` landing in ``core/``
instead of ``application/``:** the registry is pure data (a frozen
``ServiceDef`` list loaded from YAML at import time, plus a key-reader
helper) with **no application-layer dependencies**. ``key_formats``
is a registry of file-format readers — also pure-data + small pure
functions. Putting either in ``application/`` would turn the
~60-importer-each module into a cascade of layer-rule violations:
``adapters/`` and ``infrastructure/`` are forbidden from importing
``application/`` (the existing
``test_architecture_layering_ratchet.py`` enforces this), so every
adapter/infra importer would land in
``KNOWN_ADAPTERS_UPWARD_VIOLATIONS`` /
``KNOWN_INFRASTRUCTURE_UPWARD_VIOLATIONS``. ``core/`` sits below
every other layer, so adapters/infra/application/api can all import
from it without violating the hexagon. Same shape as
``core/controller_profile/catalog_loader.py`` — pure data, loaded
from YAML, depended on by everyone.

**Migration steps for each module:**

1. Move the file to the new location.
2. Update all importers — every existing ``from
   media_stack.api.services.registry import …`` becomes
   ``from media_stack.core.service_registry.registry import …``
   (or ``application.service_registry.config`` for the use-case
   modules).
3. Delete deferred imports — convert each to module-top.
4. The route modules under ``api/routes/`` that previously
   reached into ``api/services/registry`` now reach into
   ``core/service_registry/`` (api → core, direction is correct).

**No re-export shims.** The old import path is gone; old
references break loud, get fixed in the same PR. The temptation
to leave ``api/services/registry.py`` as a one-liner ``from
media_stack.core.service_registry.registry import *``
is forbidden — that would trip Rule 3 (no re-export shims).

**Ratchet additions:**

* ``test_no_inverted_imports_to_api_services.py`` — scans for
  any ``from media_stack.api.services.X`` outside ``api/``.
  Pin to zero after Phase 2.
* ``CIRCULAR_IMPORT_RISK_RATCHET`` tightened from 391 to ~310
  (the ~80 deferred imports become module-top; their
  function-body counterparts are gone).

**Estimated count delta**: 119 → ~37 inverted. ~75% of the debt
gone in one phase.

### Phase 3 — services/ directory cleanup (~3 days)

**Goal**: retire the top-level ``services/`` package. Every file
inside it goes to the layer that matches its actual responsibility.

**Why ``services/`` is the wrong shape:** it conflates two
distinct concerns under one umbrella:

* **Application services** — cross-cutting helpers like
  ``runtime_platform.log``, ``runtime_platform.current_action_tag``,
  ``services.jobs.framework`` (a shim around
  ``application.jobs.framework``).
* **Per-app services** — ``services/apps/<svc>/service.py`` —
  technology-specific business logic (e.g.,
  ``QbittorrentService``).

The two have completely different layer placements. Bundling them
under one directory is the historical accident driving most of
the remaining 37 inverted imports.

**Sub-phases:**

* **3.1** — Move ``services/runtime_platform.py`` to
  ``core/runtime_platform.py``. Cross-cutting, no
  ``media_stack`` deps; belongs in core. Update ~60 importers.
* **3.2** — Delete ``services/jobs/framework.py`` shim. Every
  caller imports through it as
  ``from media_stack.services.jobs.framework import …``. The real
  module is ``application/jobs/framework.py``. Update importers
  to point directly at ``application``.
* **3.3** — Move ``services/apps/<svc>/`` modules. Each gets
  classified:
  * Talks-to-external-service code → ``adapters/<svc>/``.
  * Business logic that doesn't talk to anything specific →
    ``application/<domain>/``.
  * Pure value objects → ``domain/<domain>/``.
* **3.4** — Delete the empty ``services/`` directory.

**Ratchet additions:**

* ``test_services_directory_is_empty.py`` — pin the absence of
  ``src/media_stack/services/``. Fail loudly if a future PR
  re-creates it.

**Estimated count delta**: 119 → ~10. Almost all remaining
deferred imports after this phase are legitimate (lazy-load,
optional-dep, test-hook).

### Phase 4 — Long-tail cleanup + per-layer ratchets (~2 days)

**Goal**: drive the inverted-direction count to zero; install
ratchets that prevent regression at each layer boundary.

**Remaining work after Phase 3:**

* `~9` adapters → api violations (per-adapter; tackle one at a
  time).
* `~7` infrastructure → api violations.
* `~5` core → api / cli violations (likely test-hooks; verify and
  document in a one-line comment per occurrence).

**For each remaining violation, three options in priority order:**

1. **Move the imported symbol** — if it's a magnet still in the
   wrong place, relocate.
2. **Invert the dependency** — pass the helper in via constructor
   or method argument instead of importing.
3. **Document and accept** — if the import is genuinely a
   lazy-load / optional-dep / test-hook, add a one-line comment
   that says so. The architecture ratchet (Phase 5) reads these
   comments and exempts the import from the violation count.

**Ratchet additions:**

* ``test_no_inverted_imports_to_api.py`` — scans for any
  ``from media_stack.api.X`` outside ``api/`` and ``cli/``.
* ``test_no_inverted_imports_per_layer.py`` — generic version
  enforcing the layer DAG.
* Each deferred import that survives must carry one of three
  one-line comments: ``# lazy-load: …``, ``# optional-dep: …``,
  or ``# test-hook: …``. The ratchet skips imports with these
  markers.

**Estimated count delta**: 119 → 0 inverted-direction
imports. The
``CIRCULAR_IMPORT_RISK_RATCHET`` floor drops to whatever the
legitimate (non-inverted) deferred-import count happens to be —
likely ~270, all marked with the appropriate one-line comment.

### Phase 5 — Lock it in (~half day)

**Goal**: write the rules into ``AGENTS.md`` + the
``test_layer_direction.py`` ratchet so this never drifts back.

**Deliverables:**

* ``docs/architecture/layer-direction.md`` — the one-page
  written contract: the layer DAG, the three rules, the comment
  markers. Linked from ``AGENTS.md``.
* ``test_layer_direction.py`` — single ratchet that combines:
  * Module-top import direction enforcement (mypy-like).
  * Function-body deferred-import classification (the comment
    markers).
  * Each layer's outbound layer set, hardcoded against the DAG.
* CI runs the ratchet as a hard gate.

**Ratchet shape:**

```python
LAYER_DAG = {
    "domain":   set(),                # leaf
    "core":     set(),                # leaf
    "application": {"domain", "core"},
    "infrastructure": {"domain", "core", "application"},
    "adapters": {"domain", "core", "application", "infrastructure"},
    "api":      {"domain", "core", "application",
                 "infrastructure", "adapters"},
    "cli":      {"domain", "core", "application",
                 "infrastructure", "adapters", "api"},
}
```

A module in layer L may import from any layer in
``LAYER_DAG[L]``, plus its own layer. Anything else fails the
ratchet — at module-top OR in a function body, unless the
function-body import carries an exemption marker.

## Anti-patterns this ADR forbids

* **"Just defer the import."** Function-body imports without an
  exemption marker are forbidden (after Phase 4). The deferred
  form hides the dependency from type-checkers and refactor
  tools; it's a TODO comment that compiles.
* **"Add a re-export shim."** Creating
  ``api/services/registry.py = from
  core.service_registry.registry import *`` to keep old
  imports working is forbidden. Move the real module; update
  call sites loud.
* **"Add a top-level package."** Inventing
  ``src/media_stack/shared/`` or ``src/media_stack/utils/`` to
  dodge layer placement is forbidden. Cross-cutting primitives
  go in ``core/``; everything else has a real home in the DAG.
* **"Wrap with a Protocol."** Protocols are useful for
  *runtime* substitution (DI). They don't fix import direction —
  the Protocol still has to be defined somewhere, and that
  somewhere has to be importable from the side that needs it.
  Use Protocols for what they're good at; don't reach for them
  to dodge layering.
* **"It's just for tests."** A function-body import added to
  satisfy a test should carry the ``# test-hook:`` marker AND be
  reviewable as a real test-hook (the test substitutes the
  imported symbol). If the test doesn't substitute it, the
  marker is wrong.

## Risks

* **Phase 2 is high-blast-radius.** Moving
  ``api.services.registry`` updates ~60 importers in one diff.
  Mitigation: a single PR that does the move + import updates
  atomically; CI runs the full unit suite before merge. The PR
  is mostly mechanical (a sed) so review focuses on the few
  hand-edited deferred-import deletions.
* **Phase 3.3 requires per-file judgment.** Each
  ``services/apps/<svc>/<file>.py`` needs a classification call
  (adapters / application / domain). Mitigation: classify in a
  table at the top of the Phase 3.3 PR; the diff is move-only,
  no behaviour change.
* **Marker discipline drift.** Phase 5's
  ``# lazy-load:`` / ``# optional-dep:`` / ``# test-hook:``
  markers depend on every contributor adding them. Mitigation:
  the ratchet fails on a deferred import without a marker —
  enforcement, not honour system.

## Consequences

**Positive:**

* The hexagonal layout becomes verifiable instead of aspirational.
  The ``test_layer_direction.py`` ratchet is the source of truth.
* Type-checkers and refactor tools see the real dependency graph.
* New contributors get a one-page reference (``layer-direction.md``)
  instead of inferring the rules from existing patterns.
* The ``CIRCULAR_IMPORT_RISK_RATCHET`` becomes meaningful — the
  remaining count is genuine lazy-load / optional-dep / test-hook,
  each of which is documented.

**Negative:**

* Phase 2's ~60-importer update is a wide diff. Reviewers need to
  spot-check that the file moves are mechanical.
* Phase 3.3's per-file classifications introduce judgment calls
  that can drift over time. The table at the top of each PR is
  the mitigation.
* Marker comments on legitimate deferred imports are a small
  ongoing cost (one line per import).

**Neutral:**

* The framework's runtime behaviour does not change in any phase.
  These are pure refactors — file moves + import-path updates —
  with no semantic changes. Each phase's PR diff is dominated by
  ``-from X.Y import Z`` / ``+from A.B import Z`` lines.

## Cross-refs

* ADR-0002 — hexagonal restructure (this ADR is the import-time
  enforcement layer that ADR-0002 didn't pin).
* ADR-0009 / ADR-0010 — Phase 6 + 7 work that uncovered the
  layering debt.
* ``CIRCULAR_IMPORT_RISK_RATCHET`` — the count that prompted
  this ADR. Will be retitled / replaced by the per-layer ratchets
  in Phase 5.
