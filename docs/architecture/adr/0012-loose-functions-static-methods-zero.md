# ADR-0012 — Drive LOOSE_FUNCTIONS / MODULES_WITHOUT_CLASS / STATIC_METHOD ratchets to zero, finish cyclic-import floor

**Status:** Proposed (2026-05-08). Co-author with ADR-0011 (import-direction
discipline) — Phase F of this ADR consumes the remaining ADR-0011 work.

Authors: matthew

## Context

A multi-batch parallel refactor session on 2026-05-08 took
``LOOSE_FUNCTIONS_RATCHET`` from 184 → 58 (68% reduction, 126 modules
class-wrapped) and ``MODULES_WITHOUT_CLASS_RATCHET`` from 43 → 6. The
established pattern — extract loose helpers onto a class, expose
module-level singleton + aliases preserving the public import API —
proved scalable to ~30 parallel sub-agent batches.

What remains is the long-tail and the genuinely hard cases:

* ``LOOSE_FUNCTIONS_RATCHET`` floor: 58 modules.
  * 1 BIG (``core/service_registry/registry.py``, 21 helpers) — the
    ADR-0011 magnet, gated on Phase 2 relocation.
  * 1 LARGE (``api/server.py``, 11 helpers) — central HTTP plumbing,
    ``ControllerAPIHandler`` already at the 20-method ceiling and
    521-line god-class threshold.
  * 4 medium (``adapters/jellyfin/lifecycle.py`` 7,
    ``adapters/compose/services/edge_http_smoke.py`` 7,
    ``services/guardrails/evaluation_loop.py`` 3, ``api/services/ops.py`` 3).
  * 8 modules with 2 loose helpers each.
  * 44 modules with 1 loose helper each.
* ``MODULES_WITHOUT_CLASS_RATCHET`` floor: 6 modules.
* ``STATIC_METHOD_RATCHET`` floor: 488. Top offenders:
  ``services/apps/stack/controller_config_policy.py`` (23),
  ``adapters/compose/edge/providers/envoy/dynamic_config.py`` (20),
  ``services/edge/envoy_config_generator.py`` (19),
  ``infrastructure/jellyfin/epg_merge_service.py`` (13),
  ``infrastructure/qbittorrent/compose_preflight.py`` (12),
  ``core/controller_profile/parser.py`` (11), 200+ tail.
* ``CIRCULAR_IMPORT_RISK_RATCHET`` floor: 389 deferred imports.
  Per ADR-0011, ~119 are inverted-direction debt; ~270 are legitimate
  lazy-load / optional-dep / test-hook patterns.

Each ratchet currently allows a non-zero floor. This ADR is the path
to flip them to hard gates.

## Decision

**Five phases (A through G), grouped by risk and dependency.**
Phases A, C, D, E, F are independent and can run in parallel. Phase B
groups carve-outs that need their own designs. Phase G is the final
lock-in.

### Design principles (carry over from the 2026-05-08 session)

1. **Plain instance methods only.** No ``@staticmethod``.
   ``STATIC_METHOD_RATCHET`` burns down at the same time as LOOSE.
2. **Module-level singleton + aliases.** Caller files never need to
   change. Aliases preserve underscore prefix so
   ``mock.patch.object(mod, "_helper", ...)`` keeps intercepting.
3. **Module-level alias dispatch when tests patch.** When a method
   calls another method that tests will mock at module scope, route
   through the module alias rather than ``self.helper()`` (lesson
   from this session's ``run_history._path`` patch).
4. **15-method / 500-line ceiling.** Split into sibling helper classes
   (BazarrAdapter pattern, commit ``a9992829``) when a single class
   would exceed.
5. **Type hints on public methods.** Pre-empt
   ``NO_TYPE_HINTS_PUBLIC_METHODS`` regressions by adding ``-> X``
   annotations on every public method.

### Phase A — long-tail single-helper modules (~50 modules)

Mechanical. Each one-loose / two-loose module gets either:

* **A1** (helper folds onto an existing class) — most modules. Lone
  helper becomes an instance method on the existing class; module-level
  alias preserves the import API.
* **A2** (no class at all — the 6 ``MODULES_WITHOUT_CLASS`` entries) —
  tiny named wrapper class (e.g. ``UrlUtils``,
  ``AssertEvaluator``, ``CloseStaleRunsJob``). One-line aliases keep
  the public function importable.

Targets the ratchet drop:

* ``LOOSE_FUNCTIONS_RATCHET`` 58 → ~14.
* ``MODULES_WITHOUT_CLASS_RATCHET`` 6 → 0.
* ``STATIC_METHOD_RATCHET`` ~−10 (incidental — some helpers became
  instance methods on classes that previously held ``@staticmethod``
  variants).

Execution: 6-agent parallel batches at the proven pattern. ~7-8 batches
× ~5 min wall-clock each. Estimated 1 hour total.

### Phase B — hard-defer modules (5 modules)

Each needs a one-off design, not the generic pattern.

* ``adapters/jellyfin/lifecycle.py`` (7 helpers) — extend the existing
  ``LifecycleApiKeyHelpers`` pattern (commit ``99160ab8``) to a
  ``JellyfinLifecycleApiKeyHelpers`` subclass with the wider helper
  surface (``_api_key_db_path``, ``_config_root``, ``_bool_cfg``,
  ``_coerce_list``, ``_resolve_path``). Inject via ``ClassVar`` like
  the other lifecycles.
* ``adapters/qbittorrent/lifecycle.py`` (2 helpers) — same family;
  same shape as the qbit-only subset that the existing lifecycle
  helpers couldn't host (no ``_config_path``).
* ``adapters/compose/services/edge_http_smoke.py`` (7 helpers) — 6 of
  the 7 helpers shadow names already on
  ``EnvoyTraefikHelpers`` (commit batch 3, this session). Replace
  the local helpers with calls to the existing aliases; net delta −7
  loose helpers + closes a duplicate-code instance.
* ``services/guardrails/evaluation_loop.py`` (3 helpers) — this is a
  shim around the canonical
  ``application/guardrails/evaluation_loop.py``
  (already refactored as ``GuardrailEvaluationLoop`` this session).
  Convert the shim to a star-shim (``from … import *``) or
  ``sys.modules`` alias and let the canonical class do all the work.
* ``api/services/ops.py`` (3 helpers) — central API-services module
  with a 687-line ``OpsService`` class (already a god-class, pinned).
  Fold helpers onto a sibling helper class so the existing class
  doesn't push past 700 lines.

Execution: sequential PRs, one per module.

Targets: ``LOOSE_FUNCTIONS_RATCHET`` 14 → ~2 (one shim consolidation
might go negative).

### Phase C — ``api/server.py`` (11 loose, central)

``ControllerAPIHandler`` is at 521 lines (over the 500 god-class
ceiling, pinned) and 20 methods (over the 15-method ceiling, pinned).
Folding 11 helpers there would push it well past both ceilings.

**Design**: split into 4 sibling helper classes by concern:

* ``_RequestSecurityGate`` (``_is_private_or_loopback``,
  ``_should_reject_for_ip_lockout``, ``_issue_csrf_if_missing``,
  ``_verify_basic_auth``)
* ``_AuditEmitter`` (``_audit_actor_from``, ``_audit_mutation``)
* ``_ActionPriorityResolver`` (``_build_action_priority``)
* ``_AutoHealLoopStarter`` (``_start_auto_heal_loop``)

``ControllerAPIHandler`` constructor-injects these. Module-level
singletons preserve aliases for any test patches.

Execution: single careful PR.

Target: ``LOOSE_FUNCTIONS_RATCHET`` −1 + eases the way for Phase E
(``api/server.py`` has its own static methods to clean too).

### Phase D — ``core/service_registry/registry.py`` (21 loose) coupled to ADR-0011 Phase 2.1+

This module is the single biggest cyclic-import magnet (per ADR-0011:
56 of the 119 inverted-direction imports go through it). ADR-0011
Phase 2.1 already moved it from ``api/services/`` to
``core/service_registry/`` (commit ``652128d5``). The 21 loose helpers
remain.

**Design**: split into 3 classes during the next ADR-0011 increment:

* ``ServiceRegistryLoader`` (``_find_services_dir``,
  ``_find_services_yaml``, ``_parse_service_entry``,
  ``_load_registry`` — pure file I/O)
* ``ServiceLookup`` (``get_service``, ``service_internal_url``,
  ``get_services_with_api_keys``, ``get_services_with_password_api``)
* ``ServiceQueryHelpers`` (smaller utilities)

This is also the natural moment to **drop the deferred-import users**
that exist purely because ``registry.py`` was previously in
``api/services/``. Per ADR-0011 Phase 2: every site that does
``from media_stack.api.services.registry import …`` inside a function
body gets converted to a module-top import from
``media_stack.core.service_registry.registry``. Net: ~56
inverted-direction imports → 0.

Execution: 2-step PR — (1) class split with aliases preserving every
public+underscore name; (2) sweep importer files with sed-style edits
to delete the deferred-import shims.

Target: ``LOOSE_FUNCTIONS_RATCHET`` 14 → ~13 (one module out), but
``CIRCULAR_IMPORT_RISK_RATCHET`` 389 → ~333 — the biggest single hit
available.

### Phase E — ``STATIC_METHOD_RATCHET`` burndown (488 → 0)

Largest counter to drive down. 488 occurrences across ~150 files.
Classes of the shape ``class Foo: @staticmethod\\ndef bar(...)`` should
become ``class Foo: def bar(self, ...)`` — ``self`` is unused but still
counted. No semantic change.

**Phasing within Phase E**:

* **E1 — Hot offenders** (10 files with ≥10 statics each = 130 of the
  488). One file per agent, ~2 batches of 5.
* **E2 — Mid-tier** (~50 files with 3-9 statics each = ~250). 6-agent
  batches; ~8-10 batches.
* **E3 — Long tail** (~80 files with 1-2 statics each = ~110).
  6-agent batches; ~14 batches.

**Watch-outs**:

* A method that's truly stateless and called from outside the class
  (``Foo.bar(arg)`` rather than ``instance.bar(arg)``) needs the call
  sites updated. Not free; agents must check.
* Some ``@staticmethod`` exists because the method is used as a
  class-level constant, e.g. ``default_factory=Foo.bar``. Those need
  a module-level alias to preserve.

Execution: parallel-agent pattern (same as the LOOSE burndown).
Estimated 6-8 hours total over multiple sessions; parallelizable
alongside Phases A, B, C, D, F.

Target: ``STATIC_METHOD_RATCHET`` 488 → 0 over ~25 batches.

### Phase F — Cyclic-import floor (close inverted-direction debt to 0)

ADR-0011 already maps the path. Status as of this writing:

| Sub-phase | Status | Description |
|---|---|---|
| 1 — domain leaf | ✅ done (commit ``45716dea``) | zero inverted-direction out of ``domain/`` |
| 2.1 — registry → core | ✅ done (commit ``652128d5``); deferred-import sweep is Phase D of THIS ADR | |
| 2.2 — config | pending | ``api/services/config.py`` → ``application/service_registry/config.py`` (~10 inverted refs) |
| 2.3 — key_formats | pending | ``api/services/key_formats.py`` → ``core/key_formats/`` (~8 inverted refs) |
| 2.4 — health | pending | ``api/services/health.py`` → ``application/service_health/`` (~8 inverted refs); class refactor (``ContainerListProbe``) already done in this session — relocation is the next step |
| 2.5 — content | pending | ``api/services/content.py`` → ``application/service_registry/content.py`` (~3 inverted refs); class refactor (``ContentHelpers``) already done — relocation is the next step |
| 3 — services/ retire | pending | per-file judgement (large multi-PR effort) |
| 4 — long-tail markers | pending | every remaining deferred import gets a ``# lazy-load:`` / ``# optional-dep:`` / ``# test-hook:`` comment |
| 5 — lock-in | pending | ``test_layer_direction.py`` ratchet replaces the count-based ``CIRCULAR_IMPORT_RISK_RATCHET`` |

Important tie-in to the LOOSE plan: Phases 2.2-2.5 each MOVE a module
that may have had a loose-functions cleanup in the 2026-05-08 session.
The class refactors (``ContentHelpers``, ``ContainerListProbe``,
etc.) actually *enable* the move — the class is the natural relocation
unit. Don't re-do; just relocate.

Target: ``CIRCULAR_IMPORT_RISK_RATCHET`` 389 → ~270 after Phase 4
(the legitimate lazy-load / optional-dep / test-hook count). Phase 5
then converts that to a per-layer marker-aware ratchet.

### Phase G — Final lock-in

After per-ratchet zeros are reached:

1. ``LOOSE_FUNCTIONS_RATCHET`` becomes a hard gate (zero tolerance).
   The pattern ``def foo(): …`` at module top fails CI immediately.
2. ``MODULES_WITHOUT_CLASS_RATCHET = 0`` becomes a hard gate.
3. ``STATIC_METHOD_RATCHET = 0`` becomes a hard gate.
4. ``CIRCULAR_IMPORT_RISK_RATCHET`` is replaced by
   ``test_layer_direction.py`` (per ADR-0011 Phase 5) — every deferred
   import must carry a marker comment, every layer-violating import
   (top-level OR function-body without marker) fails.

## Sequencing & dependencies

```
Phase A ─┐
         ├─→ LOOSE = ~14, MODULES_WITHOUT_CLASS = 0
Phase B ─┘
Phase C ───→ LOOSE = ~13, ControllerAPIHandler eased
Phase D ───→ LOOSE = ~12, CIRCULAR ≈ 333  (sibling to ADR-0011 Phase 2.1 sweep)
Phase F (2.2 → 2.5) ───→ CIRCULAR ≈ 290 (sequential, one PR each)
Phase F (3, services/ retire) ───→ many of the remaining LOOSE 12 disappear (they're shim files)
Phase E (E1 → E2 → E3) ───→ STATIC_METHOD 488 → 0  (parallelizable; can run alongside others)
Phase G ───→ Hard gates active
```

Phases A, B, C, D, E, F-2.x are independent. They can run in parallel
provided they don't touch the same files. Phase G is the very last
step.

## Effort estimate (parallel-agent throughput)

* Phase A: ~1 hour wall-clock (~50 modules at 6/batch ≈ 8 batches).
* Phase B: ~2 hours (5 sequential delicate refactors).
* Phase C: ~30 min (single careful PR).
* Phase D: ~1.5 hours (split + caller sweep).
* Phase E: ~6-8 hours total over multiple sessions (488 statics is
  a marathon).
* Phase F: each sub-phase ~1 hour (already in flight).

**Most-impactful first 4 hours**: Phase A + Phase B + Phase D
(LOOSE → 12, MODULES_WITHOUT_CLASS = 0, CIRCULAR drops by ~56).

## Anti-patterns this ADR forbids

* **``@staticmethod`` decorators.** Already a soft-gated ratchet;
  becomes a hard gate at the end of Phase E.
* **Module-level loose helpers.** Same — soft now, hard at the end of
  Phase A+B+C+D.
* **Helpers calling ``self.foo()`` when tests will patch the alias.**
  Use the module-level alias call from inside class methods so test
  ``mock.patch`` calls keep working. (Lesson from ``run_history._path``
  in the 2026-05-08 session.)
* **Splitting just to dodge the ceiling.** If a class genuinely needs
  to be 16 methods, that's the wrong fix; instead split by cohesion
  (single-responsibility seam) into a sibling helper class
  (BazarrAdapter pattern).

## Risks

* **Phase D is wide-blast-radius.** Updating ~56 importer files in a
  single PR is mechanical (sed) but reviewer fatigue is real.
  Mitigation: PR description lists every importer file with a one-line
  before/after.
* **Phase E test-fragility.** Removing ``@staticmethod`` changes how
  ``Foo.bar(...)`` resolves vs ``instance.bar(...)``. Most call sites
  already use ``self.bar`` or ``foo.bar(...)``, but external
  ``ClassName.bar(...)`` calls need a sweep. Mitigation: each
  E-batch agent does a per-file caller search before editing.
* **Phase F-3 (services/ retire).** Per-file judgement calls; the
  large diff size invites errors. Mitigation: classify in a table at
  the top of each Phase 3 PR; the diff is move-only, no behaviour
  change.
* **Marker discipline drift after Phase G.** Phase 5's
  ``# lazy-load:`` / ``# optional-dep:`` / ``# test-hook:`` markers
  depend on every contributor adding them. Mitigation: the
  ``test_layer_direction.py`` ratchet fails on a deferred import
  without a marker — enforcement, not honour system.

## Consequences

**Positive**:

* The hexagonal layout becomes verifiable end-to-end. The combination
  of ``test_layer_direction.py`` (Phase F-5) + the four hard gates
  (Phase G) means every architectural invariant is enforceable in CI.
* The 2026-05-08 burndown's pattern (class extraction with module-level
  aliases) is canonised. New code starts in the right shape because
  there's no "old shape" left to copy from.
* Test patches keep working through refactors because the module-level
  alias dispatch pattern is the documented norm.

**Negative**:

* Phase E is a marathon. 488 ``@staticmethod`` decorators across 150
  files is ~25 batches of work. Cost is bounded but not small.
* Some classes will gain unused ``self`` parameters. Acceptable —
  consistency beats one-off ``@staticmethod`` exemptions.

**Neutral**:

* The framework's runtime behaviour does not change in any phase.
  These are pure refactors — class-extraction + alias preservation.
  Each phase's PR diff is dominated by mechanical transforms.

## Cross-refs

* ADR-0002 — hexagonal restructure.
* ADR-0011 — import direction discipline + package layout cleanup.
  Phase F of this ADR consumes ADR-0011's remaining sub-phases.
* ``LOOSE_FUNCTIONS_RATCHET``, ``MODULES_WITHOUT_CLASS_RATCHET``,
  ``STATIC_METHOD_RATCHET``, ``CIRCULAR_IMPORT_RISK_RATCHET`` —
  the four counts this ADR drives to zero (or to per-layer
  successor ratchets).
