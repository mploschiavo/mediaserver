# ADR-0007 — OpenAPI-driven API request routing

**Status:** ✅ **Implemented (2026-05-04).** Phase 1 (router foundation)
landed at `e9b3a595`; Phase 2 (domain migration) closed at `26f3e638`
after 8 waves. The legacy `handlers_get.py` + `handlers_post.py`
chain (~5,360 LoC) is deleted; all 240 routes flow through the
OpenAPI Router. See the [Status as of 2026-05-04](#status-as-of-2026-05-04)
section for the as-shipped record.

Builds on the existing ``contracts/api/openapi.yaml`` spec (already
canonical, ratcheted, drift-checked). The maintainability /
correctness improvement promised at draft time has been realized —
spec-vs-handler drift now fails at startup via
`Router.assert_full_spec_coverage()` rather than living undetected
in elif chains.

## Status as of 2026-05-04

**Phase 2 closed.** Final tally:

- 240 routes registered across 41 `RouteModule` classes
- 0 unmigrated routes (5 infrastructure GETs allowlisted in
  `Router._INFRASTRUCTURE_ALLOWLIST`: `/`, `/dashboard`, `/api/docs`,
  `/api/static/{asset}`, `/metrics` — served by `server.py` directly,
  not RouteModules)
- `handlers_get.py` (2,572 LoC) + `handlers_post.py` (2,788 LoC)
  deleted; helpers extracted into 9 service modules under
  `src/media_stack/api/services/` (actor, csrf_exempt_paths,
  known_actions, logs_handlers, media_integrity_dispatch, openapi,
  rate_limiters, routing_probes) + `application/auth/users/bulk_ops.py`
- All six pre-cleanup gates passing:
  1. ✅ Every spec path has a registered handler (or is allowlisted)
  2. ✅ Zero duplicate path registrations
  3. ✅ `tests/unit/api/test_router_spec_parity.py` strict mode
  4. ✅ `OpenApiHandlerParity` ratchet upgraded literal-anchor → structural
  5. ✅ Full pytest green (7835 passed / 54 skipped / 0 failed; ratchet 370 passed)
  6. ⏸ Live soak — operational, awaiting next image bake + deploy

### Phase 2 commit history

| Commit | Wave | Routes added | New modules |
|---|---|---|---|
| `10dd1f0d` | 1 | first proof + 1 domain | health |
| `5552f5c2` | 2 | 20 routes / 6 domains | indexers_quality + 5 |
| `9f24f947` | 3+4 | ~55 routes / 14 domains | 13 parallel-agent modules (auth, downloads, epg, envoy, logs, routing_admin, probes_dns_tls, system_diag, content_lists, config, branding_user, security_audit, ops, stack_backup, jobs, misc_legacy) |
| `8c791162` | 5 | 67 routes / 8 domains | 8 modules |
| `5771e333` | 5+ | re-home /api/schedules | schedules.py |
| `c8fd226b` | 6 | 20 routes / 4 modules | post_jobs_queue, post_user_resources, post_content_config, webhooks_and_deferred |
| `0e930d93` | (B) | snake_case path-params + 2 ratchets | (no new routes; case-normalization sweep) |
| `9c8bf00f` | 7 | 4 routes | snapshots, auth_password_tickets + 2 routes folded into ops.py |
| `9cd9c6af` | 8 | 40 routes / 11 modules | post_users, post_user_sessions, post_roles, post_tokens, post_me, post_schedules_crud, post_bans, post_media_integrity, post_indexers_import_lists, post_misc + Router infra-allowlist |
| `597841c9` | (D) | parity flip + structural ratchet upgrade | (gate work) |
| `26f3e638` | (E) | **cleanup** — delete legacy files, lift helpers, retighten ratchets | 9 service modules |

### Net diff: -3,913 LoC

The migration deleted ~5,360 LoC of legacy handler code and
introduced ~1,447 LoC of class-based replacements (route modules +
service helpers + tests). The line-count reduction is incidental;
the structural improvement is the real win:

- Spec → handler drift fails at startup, not at runtime.
- Adding a new route is a new file under `api/routes/` (no
  central registration list to merge).
- Each route module is a class with constructor-injected
  collaborators — 168 wave-6/7/8 unit tests assert behavior
  in isolation without monkey-patching.

### Conventions established along the way

- **snake_case wire format** for path params + JSON body keys
  (carve-out for upstream-API passthrough fields like arr
  `eventType`, `upgradeAllowed`). Enforced by 2 new ratchets:
  `RegisteredPathParamsAreSnakeCase`,
  `BodyJsonKeysAreSnakeCase` (with allowlist).
- **Infrastructure-GET allowlist** — non-API spec paths served
  directly by server.py are exempt from
  `assert_full_spec_coverage()`. The list is intentionally tight
  (5 entries); any addition requires deliberate intent.
- **OpenApiHandlerParity** ratchet now structural — calls
  `Router.assert_full_spec_coverage()` instead of grepping
  source for literal anchors. The Router's startup check is the
  authoritative source; the ratchet pins it from the outside.

### Outstanding follow-ups (not blocking)

- **Gate 6 (live soak)** — bootstrap cycle on compose + k8s
  through the router-only path. Operational; needs an image
  bake + deploy.
- **NewType refactor** — `string-typed-ids` ratchet picked up
  ~22 new offenders during the case-normalization sweep
  (`run_id: str`, `user_id: str`, `service_id: str`, etc.).
  Convert to `RunId = NewType("RunId", str)` per identity domain
  in a single-pass refactor when ready.
- **`handle_action` cancel alias** — `post_misc.handle_action`
  special-cases `name=="cancel"` to delegate to `handle_cancel`
  (preserves the legacy `/actions/cancel` operator-script alias).
  A future cleanup could unify `KNOWN_ACTIONS` membership with
  the named-handler set.

## Context

The controller's HTTP API entry-points are concentrated in three
files:

| File | LoC | Pattern |
|---|---|---|
| ``api/server.py`` | 1218 | ``ControllerAPIHandler(BaseHTTPRequestHandler)`` + class-structured ``_AuthPolicy`` / ``_ControllerRBAC`` / ``_SudoGate`` middleware. Healthy. |
| ``api/handlers_post.py`` | 2788 | Early-return per-route + 7 domain-grouped helper classes (``_TlsCertHandler``, ``_SessionLoginHelper``, ``_UserMgmtPostHelper``, ``_security_post_handlers``, ``_media_integrity_handlers``, etc.). Cleaner. |
| ``api/handlers_get.py`` | 2567 | **Long ``if/elif`` chain** with 122 exact-path + 23 prefix branches in one method. Only one helper class extracted. ``# noqa: C901`` to suppress complexity warning. |

The asymmetry is the architectural smell. POST has shown the
team-internal "good pattern" — early-return + domain helpers —
and GET hasn't caught up.

A reachability scan on ``handlers_get.GetRequestHandler.handle()``
caught a real bug class enabled by the long chain:

```text
line 478:  elif path in (..., "/metrics", ...): self._handle_user_mgmt(...)
line 1273: elif path == "/metrics": handler._raw_response(... metrics_svc.get_prometheus_metrics(api_cache) ...)
```

Both branches implement ``/metrics``. Line 478 fires first
(earlier in the chain, ``in`` tuple match), so line 1273 is dead
code — the simpler ``api_cache``-only Prometheus output is never
emitted. Nobody noticed because nobody can scan a 1850-line
``handle()`` method visually. Same risk applies to every later
branch in the chain.

### What we already have

The infrastructure for an OpenAPI-driven API was largely built
already. We just don't route through it:

- **``contracts/api/openapi.yaml``** — 10,395 lines, **191 paths**,
  **219 verb-routes**. Single canonical contract.
- **``api/contract_validator.py``** — schema-driven response shape
  validator, drives the ``test_api_response_contract`` family.
- **``test_openapi_drift_ratchet.py``** — flags spec drift.
- **``OpenApiHandlerParity`` ratchet** in
  ``test_static_invariants_ratchets.py`` — enforces every spec path
  has a backend handler reference somewhere in
  handlers_get / handlers_post / server.py. Today's enforcement is
  literal-anchor: a string match in the source. It catches missing
  handlers but not drift between spec method/parameters and handler
  semantics.
- ``_UserMgmtGetHelper`` + 7 POST domain helpers — proof that the
  team-internal pattern of domain-grouped sub-routers is workable.

### What we don't have

- A request-time path-→-handler dispatch table. Every route is
  resolved by linear ``elif`` walk.
- Path-parameter parsing. ``/api/users/{user_id}`` is matched by
  ``path.startswith("/api/users/")`` followed by manual
  ``path.split("/")`` extraction inside each handler.
- Request-body validation against the spec. Today only response
  bodies get validated, and only in tests.
- Spec-driven 405 responses. A POST to a GET-only path goes through
  authentication, then falls into a generic 404 — operator can't
  tell whether the path doesn't exist or just doesn't accept POST.

## Decision

Two-phase migration, going **directly to the OpenAPI-driven router**
(no helper-class detour). Each phase is independently shippable;
Phase 1 is the foundation + first proof; Phase 2 is the
domain-by-domain migration over many small commits.

The earlier draft of this ADR proposed a three-phase plan with an
intermediate "extract domain helpers in the POST style" step. It's
been dropped — those helper classes do the same dispatch work as the
final router but with a different registration mechanism, so they'd
be partial throwaway. Per-domain migration directly to the router
gives the same incremental rollout safety without the rewrite step.

### Phase 1 — Router foundation + first domain proof (1 day)

**Designed for Phase 2 parallel-agent friendliness.** Four design
choices make Phase 2 a "copy this template, ship one new file" job
per domain, with zero shared-file contention:

1. **Auto-discovery routing** — Router imports every module under
   ``api/routes/`` at startup (``pkgutil.iter_modules``). Decorator
   side-effects do registration. No central registry list to merge.
2. **Router-first dispatch with legacy fall-through** — ``server.py``
   consults the router first; on no match, falls through to the
   existing ``handlers_get.handle()`` / ``handlers_post.handle()``
   chains unchanged. Phase 2 agents add NEW route modules but DO
   NOT delete legacy ``elif`` branches. The chain stays alive as
   a safety net until a final cleanup commit (after every domain
   has migrated).
3. **Startup-time drift check** — Router validates every
   registration against the OpenAPI spec at startup. Bad work
   (missing spec entry, duplicate registration, signature/spec
   path-parameter mismatch) raises ``RouterMisconfigured`` before
   the server binds. Surfaces drift fast, not silently at
   dashboard-render time.
4. **Shared test scaffolding** — ``tests/unit/api/routes/_helpers.py``
   ships ``MockHandler`` + ``dispatch_route``. Each Phase 2 agent
   imports the same helper; their tests look identical.

Phase 1 deliverables, single commit:

#### A. Router infrastructure

   - ``api/routing/router.py`` — ``Router`` class. Reads
     ``contracts/api/openapi.yaml`` at module load, compiles every
     declared path (handles ``{user_id}``-style parameters via
     spec ``parameters: [{in: path, ...}]`` declarations). Exact
     paths get a flat dict; parameterized paths get a compiled-
     regex list. Lookup is O(1) for exact, O(P) for parameterized.
     Auto-discovers route modules via ``pkgutil.iter_modules`` over
     ``api/routes/``.
   - ``api/routing/registration.py`` — ``@get(path)`` /
     ``@post(path)`` / ``@delete(path)`` / ``@put(path)`` /
     ``@patch(path)`` decorators. Each captures (verb, path,
     function) into a module-level registry; on import, validates
     the path exists in the spec.
   - ``api/routing/dispatch.py`` — entry-point invoked by
     ``server.py``: looks up the route, parses path-params,
     validates the request body via ``contract_validator``,
     invokes the handler. Returns **405** when the path exists in
     the spec but the verb doesn't (today: 404). Returns **404**
     with a ``no_matching_path`` body when the path isn't in the
     spec at all. Returns **None** (no match) when the path isn't
     registered with the router — caller falls through to legacy
     chain.
   - ``api/routing/exceptions.py`` — ``RouterMisconfigured`` raised
     at startup for drift / duplicates / bad signatures.

#### B. First migrated domain (proof + Phase 2 template)

   - ``api/routes/__init__.py`` — empty; auto-discovery target.
   - ``api/routes/health.py`` — every health-domain route
     (``/api/health``, ``/api/health-history``,
     ``/api/health/config-integrity``, ``/api/health/crashloops``,
     ``/api/health/stories``, ``/healthz``, ``/readyz``,
     ``/api/ops/health``) registered as ``@get``-decorated
     functions. Handler bodies lifted verbatim from the legacy
     chain. Phase 2 agents copy this file's structure verbatim
     for their domain.

#### C. Server integration

   - ``server.py`` ``do_GET`` / ``do_POST`` consult the router
     first; on no match, fall through to ``handlers_get.handle()``
     / ``handlers_post.handle()``. **The legacy chain is unchanged
     during migration.** No ``elif`` branch deletions in this
     phase.
   - ``handlers_get.py:1273`` ``/metrics`` shadow fix shipped in
     this commit (one-time fix; not part of the router infra).

#### D. Test scaffolding

   - ``tests/unit/api/routes/_helpers.py`` — ``MockHandler``
     stand-in + ``dispatch_route(verb, path, body, headers)``
     fixture. Each Phase 2 agent imports these.
   - ``tests/unit/api/routes/test_health.py`` — example tests
     covering each health route. Phase 2 agents copy this file's
     shape.
   - ``tests/unit/api/test_router_spec_parity.py`` — parameterized
     over every spec path; permissive during migration (logs missing
     handlers as expected fall-through), strict at Phase 2 end
     (fails any unregistered spec path).
   - ``tests/unit/api/test_router_basics.py`` — unit tests for
     ``Router`` itself (path compilation, lookup, dispatch,
     drift-check error modes).
   - ``tests/unit/contracts/test_router_route_burndown.py`` — new
     burndown ratchet: count of ``elif path`` branches in
     ``handlers_{get,post}.handle()`` only goes DOWN. Phase 1 ships
     the baseline. Each Phase 2 commit lowers it.

#### E. Documentation

   - This ADR's "Phase 2 agent-brief template" section (below)
     gets populated so spawning agents is plug-and-play.

### Phase 2 agent-brief template

Once Phase 1 ships, every Phase 2 domain migration follows this
brief. Agents customize only the domain name + route list +
legacy-handler line numbers:

```text
You are migrating the <domain> domain to the Router (ADR-0007 Phase 2).

Reference: api/routes/health.py is the template. Mirror its shape exactly.

Scope: <N> routes, listed below with their existing locations:
  - GET /api/X — handlers_get.py:LINE
  - POST /api/Y — handlers_post.py:LINE
  - ...

Steps:
  1. Create api/routes/<domain>.py.
  2. For each route, write a @get(path)/@post(path)-decorated function.
     Lift the body from the legacy handler verbatim.
     If the spec declares path parameters, declare them as kwargs
     with the same names as the spec's ``parameters: [{in: path, name: ...}]``.
  3. Create tests/unit/api/routes/test_<domain>.py modeled on
     test_health.py. Use MockHandler + dispatch_route from _helpers.
  4. Validate: .venv/bin/python -m pytest tests/unit/api/routes/test_<domain>.py

DO NOT touch:
  - api/routing/* (router infrastructure — owned by parent)
  - api/handlers_get.py / handlers_post.py (legacy chain stays as fallback)
  - server.py (router integration is owned by parent)
  - Other route modules (each agent owns one file)

Constraints: OO discipline (memory rule), topic-descriptive names
(no phase numbers / batch suffixes), no commits/pushes — parent
integrates.
```

Each Phase 2 agent's commit:
- TWO new files: ``api/routes/<domain>.py`` + ``tests/unit/api/routes/test_<domain>.py``
- ZERO edits to existing files
- ZERO shared-file contention with other agents

### Phase 2 — Domain-by-domain migration (1–2 weeks, one commit per domain)

Each domain's commit:

1. Creates ``api/routes/<domain>.py``. Each route in that domain
   becomes a ``@get(path)`` / ``@post(path)``-decorated function.
   Handler body lifted verbatim from the legacy chain — the only
   change is registration mechanism.
2. Path parameters declared by the spec become typed kwargs:

   ```python
   # spec: /api/users/{user_id}: get: parameters: [in: path, name: user_id, type: string]
   @get("/api/users/{user_id}")
   def get_user(handler: ControllerAPIHandler, user_id: str) -> None:
       svc = build_default_service()
       user = svc.get_user(user_id)
       handler._json_response(HTTPStatus.OK, user)
   ```

   The router parses ``user_id`` from the path; the handler signature
   declares it. Drift between spec parameter names and handler
   kwargs fails at startup.
3. Removes the corresponding ``elif`` branches from
   ``handlers_get.GetRequestHandler.handle()`` /
   ``handlers_post.PostRequestHandler.handle()``.
4. Domain-level test file lives next to the route module
   (``tests/unit/api/routes/test_<domain>.py``), exercising each
   handler against a spec-conformant fixture.

Domain rollout order (smaller → larger, mostly to minimize blast
radius and keep PRs reviewable):

| Order | Domain | Approx routes |
|---|---|---|
| 1 | Probes (``/healthz``, ``/readyz``, ``/status``) | 3 |
| 2 | Brand / discovery / services-registry | 7 |
| 3 | Disk + keys + dashboard | 7 |
| 4 | SSE + log-stream | 2 |
| 5 | Auto-heal + failed-services | 2 |
| 6 | Guardrails | 5 |
| 7 | Stack-update / versions / downloads / stats | 6 |
| 8 | Indexers + arr-webhooks + quality-presets | 6 |
| 9 | Routing / DNS / TLS / hostnames | ~15 |
| 10 | Metrics + envoy-stats | 6 |
| 11 | Auth + users + invites + tokens | ~30 |
| 12 | Ops | ~10 |
| 13 | Content | ~18 |
| 14 | Config | ~30 |
| 15 | Webhooks (POST) | 5 |
| 16 | Reset / restart actions (POST) | ~15 |
| 17 | Media-integrity / orchestrator (POST) | ~10 |
| 18 | Static / dashboard (anything left) | residual |

**End state**: ``handlers_get.GetRequestHandler.handle()`` and
``handlers_post.PostRequestHandler.handle()`` are gone.
``server.py`` calls ``router.dispatch(verb, path, body, headers,
handler)`` directly. Old ``api/handlers_get.py`` and
``api/handlers_post.py`` are deleted.

The ``OpenApiHandlerParity`` ratchet upgrades from literal-anchor
(``grep`` for the path string somewhere) to **structural** (every
spec path has a router-registered handler whose signature matches
the spec parameters). Drift is mechanically impossible.

## Alternatives considered

### Full migration to FastAPI / Starlette / Flask

**Rejected.** Reasons:

- The whole controller deployment surface assumes
  ``BaseHTTPRequestHandler`` + ``ThreadingHTTPServer``. uvicorn
  changes the runtime: signal handling, port-binding semantics,
  thread / async model, hot-reload story, log-shape, all the
  observability that's built around the current loop.
- 270+ routes need rewriting. The 6800+ tests need updating to
  match new request/response semantics (FastAPI's
  ``TestClient`` differs from
  ``http.client.HTTPConnection``).
- The existing CSRF / RBAC / sudo gate / audit / rate-limit
  middleware is deeply integrated with the current handler — a
  port to FastAPI's ``Depends(...)`` model is a partial rewrite,
  not a translation.
- Marginal benefit: a custom 200-line router that uses the
  existing OpenAPI spec gives us 80% of FastAPI's value (typed
  routes, schema validation, automatic docs) without paying any
  of the migration cost.

### Pure dispatch-dict (no OpenAPI)

```python
_GET_ROUTES: dict[str, Callable] = {"/healthz": handle_healthz, ...}
```

**Rejected.** Closer to the destination than the existing chain,
but doesn't solve the spec/handler-drift problem. The OpenAPI
spec is already canonical and ratcheted; not driving routing from
it perpetuates the same parallel-truth problem ADR-0006 solved for
promises.

### Helper-class extraction without router (the original draft of this ADR)

**Rejected.** Earlier draft of this ADR proposed extracting each
GET domain into a ``XGetRoutes`` helper class (mirroring the POST
file's structure) before introducing the router. Dropped because
the helper classes do the same dispatch work as the router but
with a different registration mechanism — they'd be partial
throwaway when Phase 2 lands. Going directly to the router gives
the same incremental rollout (registered routes win, unregistered
fall through) without the rewrite step.

### Accept the chain, document it

**Rejected.** The ``/metrics`` shadow bug shows the chain is
already costing us. 270+ routes is too many to scan visually. A
linter rule ("no ``elif path == ...`` after position N") is
brittle and doesn't address the parallel-truth issue with the
OpenAPI spec.

## Consequences

### Positive

- **Eliminates the shadow-bug class structurally.** Phase 1 ships
  the router, which can only route paths declared in the spec —
  duplicate registration is a startup error, not a silent shadow.
- **Single source of truth** for the API surface — the spec.
  Every route exists in one place; handler-vs-spec drift surfaces
  at startup, not at the dashboard.
- **Path-parameter parsing happens once**, in the router, not in
  every handler.
- **5xx surface shrinks**: spec-validated request bodies catch
  malformed input at the router level instead of inside the
  handler.
- **OpenAPI contract evolves into a contract test** — adding a
  new endpoint requires both spec entry AND handler registration;
  CI catches the drift on PR submit instead of in production.
- **Dashboard developers benefit immediately**: the spec is
  generated, typed, drift-free; UI ``openapi-typescript`` codegen
  becomes load-bearing.
- **Test surface contracts**: a single spec-driven contract test
  parameterized over every spec path covers the whole API instead
  of one hand-written test per route.

### Negative

- **270+ routes need re-registration over Phase 2.** Done
  incrementally, one domain at a time, but it is real work.
- **Decorator-based registration adds a small import-time cost**
  — route modules trigger registration on import. Today the
  registration is implicit (the ``elif`` chain). Net: same
  startup cost, different shape.
- **Path-parameter typing must agree with the spec.** The router
  passes path parameters as kwargs; if the handler signature
  drifts from the spec parameter names, the startup-time
  signature check fails before the server binds.
- **Two routing surfaces during migration** — registered routes
  use the router, unregistered fall through to the legacy chain.
  Slightly more dispatch overhead until Phase 2 completes.

### Neutral

- **No behavior change** for any existing route (both phases are
  pure refactor). Tests stay green throughout.
- **The OpenAPI spec doesn't grow** — it already contains every
  route. Phase 1 just makes the spec authoritative for routing,
  not just validation.

## Stewardship

Same shape as ADR-0003 / ADR-0005 / ADR-0006: directional
commitment, phased rollout, explicit steward approval before each
phase. Reversibility:

- **Phase 1 revert**: keep the router infrastructure (it does no
  harm to be imported but unused), revert the health-domain route
  module, restore the inline ``elif`` branches. The strict-mode
  startup check disables itself when no routes are registered.
- **Phase 2 revert (per domain)**: each domain commit is its own
  ``api/routes/<domain>.py`` deletion + restoration of the legacy
  ``elif`` branches. ~5-minute revert per domain.

## Phase 1 deliverables (✅ shipped at `e9b3a595` and prior)

- Fix ``/metrics`` shadow at
  ``src/media_stack/api/handlers_get.py:1273``.
- ``api/routing/router.py`` — ``Router`` class.
- ``api/routing/registration.py`` — ``@get`` / ``@post`` /
  ``@delete`` / ``@put`` / ``@patch`` decorators.
- ``api/routing/dispatch.py`` — request entry-point invoked by
  ``server.py``. Handles 404 / 405 distinction, request/response
  schema validation.
- ``api/contract_validator.py`` extended to validate request
  bodies (today only response).
- ``api/routes/health.py`` — first migrated domain
  (``/healthz`` / ``/readyz`` / ``/api/health*`` / ``/api/ops/health``).
- ``handlers_get.GetRequestHandler.handle()`` consults the router
  first, falls through to the legacy chain on miss.
- ``OpenApiHandlerParity`` ratchet upgraded from literal-anchor to
  semantic match (handler signature parameters match spec ``in:
  path`` parameters).
- New burndown ratchet at
  ``tests/unit/contracts/test_router_route_burndown.py``: the
  count of ``elif path`` branches in
  ``handlers_{get,post}.handle()`` only goes DOWN. Phase 2 drives
  it to zero.

## Phase 2 deliverables (✅ shipped at `26f3e638`)

Original plan called for ~18 commits, one per domain. Actual
rollout was 8 waves (the first few bundled multiple domains via
parallel-agent migration; the last three were targeted scope
adjustments + cleanup). See the [commit history table](#phase-2-commit-history)
above for the detailed mapping.

- ✅ All 240 routes migrated into `api/routes/*.py` modules
  (41 RouteModule classes total).
- ✅ `api/handlers_get.py` + `api/handlers_post.py` deleted in
  the cleanup commit (`26f3e638`); helpers extracted into 9
  service modules.
- ✅ The legacy fallback in `server.py` is removed; `do_GET` /
  `do_POST` call `router.dispatch(...)` directly. NO_MATCH
  emits a strict 404; METHOD_NOT_ALLOWED emits 405 with the
  spec-declared verbs.

## Relationship to other ADRs

- **ADR-0001 / ADR-0002** (repo / hexagonal restructure): the
  router lives in ``api/`` — adapters layer per the hexagon. No
  layering changes.
- **ADR-0003** (orchestrator) + **ADR-0004** (verifier) + **ADR-0005**
  (orchestrator-driven bootstrap): orthogonal — those ADRs are
  about WHAT the controller does, this one is about HOW the API
  surface is structured.
- **ADR-0006** (per-service promise registries): same architectural
  pattern at a different layer. ADR-0006 made YAML-defined
  promises authoritative for orchestration; ADR-0007 makes
  YAML-defined OpenAPI authoritative for routing. Both replace
  parallel sources of truth with one.
