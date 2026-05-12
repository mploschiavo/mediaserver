# Changelog

All notable changes to this stack. Dates reflect when the work landed on `main`.

## [v1.0.314] — 2026-05-02

### Fixed
- **k8s_resource probe was using snake_case dict keys.** The
  kubernetes Python client's ``.to_dict()`` returns Python-friendly
  snake_case keys (``image_pull_secrets``,
  ``persistent_volume_reclaim_policy``, ``claim_ref``). Every
  k8s_resource promise's assert is written against the API JSON
  shape (camelCase: ``imagePullSecrets``,
  ``persistentVolumeReclaimPolicy``, ``claimRef``) — the shape
  ``kubectl -o json`` emits and that the legacy
  ``media-stack-probe-promises`` CLI saw natively.
  - Net effect on live data: 3 false-positive ``failed_transient``
    statuses on k8s (pull-secret, two PV reclaim-policy probes)
    silently flipped to that state because the asserts couldn't
    find the keys, not because the world was broken.
  - Switched to ``client.ApiClient().sanitize_for_serialization()``,
    which returns the camelCase API JSON shape.
  - One regression test pins the shape so future refactors don't
    reintroduce the bug.

## [v1.0.313] — 2026-05-02

### Fixed
- **k8s_resource probe missing `configmap` kind.** Live data on
  v1.0.312 surfaced `profile-configmap-mounted` reporting "k8s_resource:
  unsupported kind 'configmap'" — the kind→API mapping table covered
  pod/service/pvc/pv/secret/deployment/ingress but missed configmaps.
  Added `CoreV1Api.list_namespaced_config_map` /
  `list_config_map_for_all_namespaces`. One additional dispatcher
  test pins the new kind specifically.

## [v1.0.312] — 2026-05-02

### Architecture
- **Orchestrator now implements k8s_resource + k8s_exec probe types.**
  Previously stubbed as `unknown` with detail "k8s_resource probe
  not implemented in orchestrator (Phase 5+)" — that string was a
  TODO in production code. The legacy `media-stack-probe-promises`
  CLI handled these via kubectl shell-out from the operator's host;
  the orchestrator runs INSIDE the controller pod and now uses the
  kubernetes Python client + the controller's service account RBAC
  (no kubectl binary dependency).
  - `k8s_resource`: lists pvc / pv / pod / deployment / service /
    ingress / secret via the appropriate `list_*` API. Cluster-scoped
    PV ignores any namespace field. ApiException → `unknown` (cooldown
    applies); assert-failure → `failed`.
  - `k8s_exec`: finds the first Running pod matching `pod_label`
    via `field_selector="status.phase=Running"`, exec's the command
    via `kubernetes.stream.stream`, evaluates the assert against
    stdout. Routing-var `${var}` substitution works on both the
    command and the assert expression. `skip_if_unset` lets a
    promise be N/A for deployments that haven't configured the
    referenced routing var.
  - 16 new dispatcher tests pin both probes' contracts (k8s
    available + happy path, label_selector pass-through, cluster-
    scoped kind, unsupported kind, k8s unavailable → unknown,
    ApiException → unknown, no-Running-pod → failed, skip_if_unset
    pass, var substitution).

### Impact
- K8s `media-stack-verify` should drop from ~14 unknown to near-zero
  for `k8s_resource`/`k8s_exec` probes (real failures stay flagged
  honestly; the verifier was reporting them as unknown only because
  the dispatcher was a stub).

## [v1.0.311] — 2026-05-02

### Architecture
- **ADR-0004 Phase 6.5 — delete legacy `media-stack-probe-promises`
  CLI.** Closes ADR-0003 Phase 5e.2. The CLI was the parallel
  probe-loop implementation that ran from the operator's host shell
  and re-implemented every probe outside the controller. With v1.0.310
  in production using `media-stack-verify` (the orchestrator-state
  HTTP client) for the same job, this CLI was orphan.
- Removed: `src/media_stack/cli/commands/probe_promises.py` (~800 LOC)
  and its console-script registration in `pyproject.toml`. The
  back-compat `_evaluate` alias and the test pinning it
  (`TestProbePromisesAlias`) come out with it — both were 5e.1
  transitional scaffolding.
- Updated: `render_promises_reference.py` (the doc generator) +
  regenerated `docs/reference/promises.md` to point operators at
  `media-stack-verify`. CLI index, promises-registry doc, and the
  ADR-0003 Phase 5e deletion audit refreshed to reflect shipped
  state.

### Rollback
The CLI is gone but the verifier path it replaces (`media-stack-verify`
+ `bin/test/verify-fresh-install.sh`) is the same code path it was
in v1.0.310. Roll back to v1.0.310 if a fresh-install regression
surfaces; the legacy CLI can be cherry-picked back from git history
(`git log --diff-filter=D -- src/media_stack/cli/commands/probe_promises.py`).

## [v1.0.310] — 2026-05-02

### Architecture
- **ADR-0004 — promise-driven fresh-install verifier.** Replaces
  `media-stack-probe-promises` (parallel probe loop running in the
  operator's host shell) with a thin client of the controller's
  orchestrator state. Operator and orchestrator now agree on "is
  the stack healthy?" by construction — same data, one runtime.
  - **Phase 6.1**: `GET /api/orchestrator/promises/state` endpoint
    serves the most recent persisted tick. 503 + `last_tick_age_seconds`
    when missing/stale so the verifier can retry vs fail.
  - **Phase 6.2**: `FreshInstallVerifier` class
    (`src/media_stack/application/verifier/fresh_install.py`).
    External-client mode (HTTP), structured `VerificationResult`
    dataclass, `verify()` (one-shot) and `wait_for_steady_state()`
    (poll until pass / deadline / fail-fast on `failed_permanent`).
    Honest-failure semantics: `unknown` counts as a failure (not
    silently green); empty attempts list is not acceptance.
  - **Phase 6.3**: `media-stack-verify` CLI registered as a
    console-script. Flag shape matches the legacy CLI for one-line
    wrapper-script swap. Exit codes 0/1/2 (pass / failed / unreachable
    or state-not-yet).
  - **Phase 6.4**: `bin/test/verify-fresh-install.sh` switched from
    `media-stack-probe-promises` to `media-stack-verify --wait 90`.
    Legacy CLI stays registered for one release of soak; Phase 6.5
    will delete it (unblocks ADR-0003 Phase 5e.2).

### Tests
- 33 new tests for verifier + CLI (acceptance buckets, exit codes,
  stale-recheck, env fallbacks, --filter narrowing, --wait dispatch,
  legacy flag compat).
- 12 new tests for the endpoint + dispatch wiring (200/503/missing/
  malformed/threshold boundaries; live-snapshot replay).
- Live `promise_state.json` snapshot captured at
  `tests/fixtures/orchestrator/` so a registry drift surfaces in
  the parser test, not in production.

### Rollback
Single-commit revert restores `verify-fresh-install.sh`'s call to
`media-stack-probe-promises`. Both CLIs still registered as console-
scripts; revert path is a one-line shell swap.

## [v1.0.309] — 2026-05-02

### Architecture
- **Retire the direct `jellyfin:ensure-api-key` auto-heal hook
  (ADR-0003).** The orchestrator's `jellyfin-api-key-discoverable`
  promise now drives this, dispatching
  `JellyfinLifecycle.mint_api_key` — which itself wraps the SAME
  `infrastructure.jellyfin.http_preflight.run_preflight` the legacy
  hook called. Equivalence is by construction; live-verified by a
  negative test on compose (deleting the key from jellyfin's SQLite
  DB triggered re-mint through the same code path within ~60s).
  - Coverage matrix: `docs/architecture/orchestrator-coverage-matrix.md`
  - The `jellyfin:ensure-api-key` job + handler stay registered
    (bootstrap may still invoke them); only the per-tick auto-heal
    invocation is removed.
- The other 3 auto-heal hooks (`guardrails:evaluate`,
  `jobs:close-stale-runs`, `orchestrator:satisfy-shadow`) stay —
  they have no orchestrator coverage / IS the orchestrator.

### Refactored
- **Lift the assert-expression evaluator out of
  `cli/commands/probe_promises.py` into
  `infrastructure/promises/assert_eval.py`.** The orchestrator
  dispatcher previously imported `_evaluate` from the CLI module,
  coupling the runtime path to legacy code. Both consumers now go
  through one auditable evaluation site; module-level `_evaluate`
  alias preserved in `probe_promises.py` for back-compat.
  - 20 new unit tests covering allowlist-builtins, generator-
    expression scope handling, multi-line YAML block scalars,
    error surfacing, and the legacy-alias contract.

### Rollback
Single-commit revert restores the legacy hook. Both pipelines are
idempotent so the revert is safe at any time.

## [v1.0.308] — 2026-05-01

### Fixed
- **`apply-arr-runtime-defaults` was silently no-op on every fresh
  deploy.** The wrapper in `services/apps/core/job_adapters.py`
  re-cases `app_keys` to capitalized names (`Radarr`, `Sonarr`)
  when `cfg.arr_apps` is empty (the default on contract-driven
  deploys). But every downstream patch checks `"radarr" in app_keys`
  with lowercase, so the check `impl in by_impl and impl in
  app_keys` always failed on the second clause. Result: `updated:
  {}` returned ok every tick, while NONE of the patches (language=
  Any, FLAC unlimited size, Readarr unknown-text, the new
  enableAuto auto-add) actually ran.
  - Removed the re-casing. ``app_keys`` now stays lowercase
    end-to-end. The prior assumption ("downstream wants
    Capitalized to match `arr_apps[].name`") was wrong; the
    downstream wants lowercase to match `by_impl` keys.
  - 14 unit tests pinning the dispatch logic continue to pass.
  - This is the bug behind v1.0.307's `updated: {}` symptom on
    radarr — the new `patch_arr_import_lists_auto` helper was
    fine, just never reached.

## [v1.0.307] — 2026-05-01

### Fixed
- **`apply-arr-runtime-defaults` now flips `enableAuto=True` on
  every enabled import list** (sonarr / radarr / lidarr / readarr).
  Previously the job's name promised "runtime defaults" but didn't
  touch import lists at all; a fresh deploy with TMDb/Trakt lists
  seeded would start with all of them set to `enableAuto=False`,
  meaning **the *arr stack wouldn't actually start fetching anything
  on its own**. Operators had to manually click into each list and
  toggle the auto-add slider — defeats the zero-touch design.
  - New helper `patch_arr_import_lists_auto()` PUTs the updated
    list back. Idempotent (skips lists already at True);
    respects operator intent (lists explicitly disabled stay off).
    Sets both `enableAuto` (newer API) and `enableAutomaticAdd`
    (older API) when the GET response shows either field.
  - Wired into `apply_arr_runtime_defaults()` for all four *arr
    types — same pattern as the existing `patch_arr_usenet_enabled`
    fan-out.
  - 4 new tests: flips disabled→enabled, idempotent at True, skips
    operator-disabled lists, sets legacy `enableAutomaticAdd` field
    when present.
- **3 ADR-0003 Phase 4d service-level promises tightened** based
  on live shadow data:
  - `jellyfin-libraries`: assert no longer requires `/media/*` for
    Jellyfin's auto-managed `boxsets` collection (Jellyfin stores
    those at `/config/data/collections` internally, never user
    media). Only the 4 user-required types (movies/tvshows/music/
    books) must point at `/media/*`.
  - `gateway-https-listener-up`: stops requiring Authelia OIDC
    discovery (`/.well-known/openid-configuration`) which 404s on
    stacks that haven't enabled OIDC server mode. Switched to a
    plain `http_status` probe — any HTTP response from the gateway
    proves TLS handshake + Envoy routing.
  - `radarr-import-lists-auto` left strict (operator confirmed:
    auto-add IS the intended out-of-the-box behavior); the
    underlying ensurer now actually flips the toggle.

## [ui-v1.3.71] — 2026-05-01

### Fixed
- **K8s 502s after controller pod recreate**, exposed by the
  v1.0.306 Phase 5a deploy. UI nginx couldn't resolve the bare
  `media-stack-controller` service name on k8s — `/etc/resolv.conf`
  has the right `search` domains but nginx's internal resolver
  doesn't honor them. The v1.3.70 fix only extracted the
  `nameserver`; it assumed bare names resolve fine on k8s, which
  isn't true.
- v1.3.71 extends `15-set-resolver.envsh` to also detect the first
  search domain. When `API_UPSTREAM`'s host part has no dots AND a
  search domain exists, the hook rewrites `API_UPSTREAM` to FQDN
  (`media-stack-controller.media-stack.svc.cluster.local:9100` on
  k8s; unchanged on compose where there's no search domain).
  Self-correcting: operator-supplied IPs or already-FQDN values
  bypass the rewrite.

## [v1.0.306] — 2026-05-01

### Architecture
- **ADR-0003 Phase 5a — Jellyfin promoted from shadow to primary.**
  First service-family to graduate out of dry-run-shadow.
  - New `live_services` parameter on `satisfy_promises(...)` —
    a frozenset of service ids whose ensurers run for real (vs
    dry-run-shadow). Other promises continue to dry-run.
    Promises with no service id (file probes, infra ensurers)
    always honor the global `dry_run` flag, so an operator
    flipping jellyfin live can't accidentally promote unrelated
    file-based promises.
  - Handler reads `ORCHESTRATOR_LIVE_SERVICES` env (comma-separated,
    case-insensitive) and passes it through. Operators can flip
    the rollout family by family without re-deploying the image —
    `kubectl set env` or compose env override.
  - 5a deploy sets `ORCHESTRATOR_LIVE_SERVICES=jellyfin` on both
    compose and k8s. Legacy `jellyfin:ensure-api-key` Phase 0
    ensurer continues to run in parallel during the soak — both
    are idempotent, so double-mint is a no-op.
- 7 new unit tests (live_services allowlist behavior on various
  shapes + env-var parsing edge cases).

### Operational
- `deploy/compose/docker-compose.yml` controller env adds
  `ORCHESTRATOR_LIVE_SERVICES` (default `jellyfin`, overridable via
  outer env).
- `deploy/k8s/base/controller/controller.yaml` controller env adds
  `ORCHESTRATOR_LIVE_SERVICES: jellyfin`. Pin bumped to v1.0.306.

### Rollback
If 5a regresses: `kubectl -n media-stack set env deployment/media-
stack-controller ORCHESTRATOR_LIVE_SERVICES=` (or remove from compose
env) reverts to full Phase-4c shadow without touching the image.

## [v1.0.305] — 2026-05-01

### Architecture
- **ADR-0003 Phase 4d follow-up.** v1.0.304 cut transient failures
  on compose from 15 → 5 + 4 unknown. The remaining 9 split into
  two known classes; this commit handles both:
  - **4 gateway probes refused** (`gateway-https-listener-up`,
    `gateway-app-prefix-route`, `gateway-jellyfin-route`,
    `gateway-http-redirects-to-https`). v1.0.304's synthetic resolver
    used `localhost:443`/`localhost:80` (matching the legacy CLI's
    host-shell perspective). But the orchestrator runs INSIDE the
    controller container, where those mappings aren't reachable.
    Routes now go to `envoy:8880` (compose HTTPS listener) /
    `envoy:8080` (compose plain HTTP) / `envoy:80` (k8s — TLS
    terminated at ingress). HTTPS to envoy:8880 disables TLS
    verification (self-signed cert valid for the public hostname,
    not the internal `envoy` DNS name).
  - **5 controller_basic 401s** (`adaptive-search-scheduled`,
    `dns-readiness-banner-data`, `foundational-jobs-run-before-app-jobs`,
    `internet-exposed-stack-must-have-auth`, `stuck-imports-scheduled`).
    Promises with `auth: controller_basic` need HTTP Basic against
    the controller's own API as the seeded stack admin. Same flow
    `probe_promises.py` uses; lifted into the dispatcher's
    `_auth_headers()` switch.
- 3 new tests (gateway-compose-internal URL, gateway-TLS-skip-verify,
  controller_basic header round-trip + empty-password defensive).
- After this: remaining shadow failures should be legitimate
  service-side issues only — JELLYFIN_API_KEY env empty mid-mint,
  jellyseerr/unpackerr/etc. not actually deployed in this stack.
  Phase 5 retires the legacy ensurers and orchestrator's own
  ensurer cycle resolves these.

## [v1.0.304] — 2026-05-01

### Architecture
- **ADR-0003 Phase 4d — orchestrator dispatcher gap fixes from live
  shadow data.** First v1.0.303 shadow tick on compose surfaced 15
  `failed_transient` promises; categorized into:
  - **7 synthetic-service-id failures** (`controller`, `gateway_https`,
    `gateway_http` — services without a `contracts/services/<id>.yaml`).
    Legacy `probe_promises.py` hardcodes URLs for these; orchestrator
    now does too. Compose: `controller` → `localhost:9100`,
    `gateway_http` → `localhost:80`, `gateway_https` → `localhost:443`.
    K8s: routes to the `envoy` Service for both gateway pseudo-
    services.
  - **1 jellyfin_key auth alias** (`jellyfin-libraries` HTTP 401).
    Promises authored with `auth: jellyfin_key` resolve to the same
    `X-Emby-Token` header the contract YAML's `auth_mode` declares,
    reading from `JELLYFIN_API_KEY` env. Was being silently dropped
    (returning empty headers) because dispatcher only recognized
    `auth: api_key`.
  - **5 file-path resolution failures** (relative paths under
    unset `CONFIG_ROOT`). `_resolve_file_path()` now falls back to
    `/srv-config` like `resolve_run_history_path()` does — the
    file-system layout the controller's PVC/bind mount establishes.
- 7 new tests covering synthetic-service URL builders (compose +
  k8s variants), unknown-service failure shape, and `jellyfin_key`
  → `X-Emby-Token` header round-trip.
- Remaining failures after this slice are legitimate service-side
  issues, NOT orchestrator bugs (e.g. JELLYFIN_API_KEY env empty
  while Phase 0 ensurer is mid-mint, jellyseerr/unpackerr not
  deployed in this compose stack). Phase 5 will retire the legacy
  ensurers; those will resolve on the orchestrator's own ensurer
  cycle.

## [v1.0.303] — 2026-05-01

### Fixed
- **Phase 4c container path resolution.** The first 4c deploy
  (v1.0.302) loaded a registry of zero promises in the container —
  `default_registry_path()` used `Path(__file__).parents[4]` which
  pointed at the site-packages root inside the container, not the
  repo root. Walks a candidate list now (env override
  `MEDIA_STACK_CONTRACTS_ROOT`, then dev path, then standard
  container paths `/app/contracts/`, `/contracts/`,
  `/usr/local/share/media-stack/contracts/`, `/opt/media-stack/contracts/`)
  and returns the first that exists. Same fix for
  `dispatcher._default_contracts_dir()` and the matching
  `default_contracts_root()` helper. `cooldown.default_state_path()`
  now falls back to `/srv-config` (matching `resolve_run_history_path`)
  instead of relative `config/`.
- 34 existing tests cover the path resolution; live verified that
  v1.0.303 loads the full 52-entry registry and writes
  `promise_state.json` to the same dir as `run-history.jsonl`.

## [v1.0.302] — 2026-05-01

### Architecture
- **ADR-0003 Phase 4c — orchestrator shadow-mode hookup.** First
  runtime change since v1.0.294: the auto-heal cycle now calls
  `run_job("orchestrator:satisfy-shadow")` every 60s, alongside the
  existing `guardrails:evaluate` / `jellyfin:ensure-api-key` /
  `jobs:close-stale-runs` hooks.
  - The new handler `media_stack.application.jobs.orchestrator_satisfy:
    satisfy_shadow` calls `satisfy_promises(dry_run=True)`. Probes
    fire across all 50+ registered promises in parallel; ensurers
    do NOT (avoids conflict with the legacy bootstrap pipeline
    still driving real mutations).
  - Per-tick aggregate lands in run-history under
    `job_name=orchestrator:satisfy-shadow` with summary fields
    (`ok_count`, `failed_transient_count`, `elapsed`, etc.) so
    operators can chart "promises green over time" through the
    existing `/api/jobs/history` endpoint without parsing logs.
  - Per-promise current state lives in
    `.controller/promise_state.json` (cooldown tracker file). The
    legacy ratchet meta-test (`test_promises_registry.py`) and the
    new dispatch-resolution ratchet (`test_promise_dispatch_resolution
    _ratchet.py`) continue to gate the registry's correctness.
- Platform detection (`KUBERNETES_SERVICE_HOST` env / explicit
  `MEDIA_STACK_RUNTIME` override) routes the orchestrator to the
  right platform-specific promise subset.
- 7 new unit tests covering platform detection, dry-run
  enforcement, no-op per-promise emit, and contract-registration
  ratcheting. 221 ADR-0003 tests total green.

### Operational
- Image rebuild + deploy. Compose: ``deploy/compose/docker-compose.yml``
  pinned to v1.0.302. K8s: ``kubectl set image`` rolled the controller
  deployment to v1.0.302 (kustomization newTag also bumped).

## [v1.0.301] — 2026-05-01

### Architecture
- **ADR-0003 Phase 4b — orchestrator core.** `satisfy_promises(...)`
  in `application/services/orchestrator.py` is the new central
  evaluation loop:
  - Topologically sorts promises by `depends_on` (deps probed first;
    dependents marked `dep_failed` when an upstream fails this tick)
  - Probes IN PARALLEL within each topo-level via
    `ThreadPoolExecutor` (default 8 workers, 30s batch bound) — a
    slow probe doesn't block faster siblings
  - Honors per-promise cooldown via the new
    `infrastructure/promises/cooldown.py::CooldownTracker` (in-mem +
    JSON persistence at `.controller/promise_state.json`; 30s
    transient / 300s permanent backoff windows)
  - Runs ensurers when probes fail (unless `dry_run`); re-probes to
    confirm; records `failed_permanent` only when the ensurer
    explicitly signals non-transient failure
  - Tier-leveled logging: INFO for tick start/end + state
    transitions, WARN for slow probes (>1s) + repeated transients,
    ERROR for permanent failures + defensive topology violations
- **Probe + ensurer dispatch tables** in
  `infrastructure/promises/dispatcher.py`. Pattern-matches
  `ProbeSpec.kind` / `EnsurerSpec.kind` to per-kind handlers:
  `lifecycle` / `http_json` / `http_text` / `http_status` /
  `file_json` / `file_text` for probes; `lifecycle` / `job` /
  `deploy` / `infra` for ensurers. K8s probes (`k8s_resource`,
  `k8s_exec`) return `unknown` with a Phase-5 marker — the legacy
  `probe_promises.py` CLI still handles those. Reuses the existing
  centralized `_evaluate(...)` helper from `probe_promises.py` for
  assert expressions — single auditable evaluation site.
- **`RunRecord.promise_id`** additive field. The orchestrator emits
  one record per probe + ensurer attempt, with `source=
  orchestrator_shadow` and `promise_id=<id>`, so operators can
  query the existing run-history API per-promise. Backwards-
  compatible: legacy records without `promise_id` round-trip as
  `None`.
- **CLI:** `bin/ops/orchestrator-eval.sh` runs one tick and prints
  a per-promise table (or JSON via `--json`). Exit 0 when no
  failures, 1 otherwise. Useful for local debugging and Phase 4d
  discrepancy chasing.
- 45 new unit tests (orchestrator topology + cooldown + dry-run +
  parallelism + summary, dispatcher per-kind, cooldown windows +
  persistence + atomic-write); 214 ADR-0003 tests total green.
- Pure additive — runtime image unchanged from v1.0.294. Auto-heal
  hookup + first deploy lands in Phase 4c.

## [v1.0.300] — 2026-05-01

### Architecture
- **ADR-0003 Phase 4a — promise types + registry loader.** First slice
  of the orchestrator track:
  - New `media_stack.domain.services.promises` package with typed
    value classes — `Promise`, `ProbeSpec` (8 probe kinds: lifecycle,
    http_json, http_text, http_status, file_json, file_text,
    k8s_resource, k8s_exec) and `EnsurerSpec` (4 ensurer kinds:
    lifecycle, job, deploy, infra). Discriminated unions; the Phase
    4b orchestrator pattern-matches on `.kind` without per-handler
    if-statements. Pure, frozen, no I/O.
  - New `media_stack.infrastructure.promises.registry` loader that
    parses `contracts/promises/promises.yaml` into typed values.
    Both schemas coexist by design: legacy `ensured_by: ensure-foo`
    string entries (~50 today) become `JobEnsurer`; new
    `ensured_by: { type: lifecycle, ... }` entries become
    `LifecycleEnsurer`. Errors carry the offending promise id +
    one-line reason — operator-actionable.
  - First two lifecycle-shaped promises in the registry as
    end-to-end proof: `jellyfin-running` (lifecycle probe + deploy
    ensurer) and `jellyfin-api-key-discoverable` (lifecycle probe
    + lifecycle ensurer + depends_on chain). Phase 4c expands.
- **New ratchet** `test_promise_dispatch_resolution_ratchet.py`
  enforces: every lifecycle-typed probe/ensurer resolves to a real
  service whose contract names a `lifecycle_class` whose class
  satisfies `ServiceLifecycle` AND has the named method;
  `depends_on` references real promise ids; the dependency graph
  has no cycles. Failing fast at CI rather than at orchestrator
  boot.
- 30 new unit tests; 169 total ADR-0003 tests green.
- Pure additive code — runtime image unchanged from v1.0.294.

## [v1.0.299] — 2026-05-01

### Architecture
- **ADR-0003 Phase 3c — auth + no-API-key lifecycles, ADR-0003
  Phase 3 closes.** Five more `ServiceLifecycle` adapters
  (Authelia, Authentik, Homepage, FlareSolverr, Envoy) all in the
  no-API-key shape. The repetition is captured in a new shared
  base, `media_stack.adapters._lifecycle_base.NoApiKeyLifecycleBase`,
  so each per-service adapter is ~10 LOC:

      class HomepageLifecycle(NoApiKeyLifecycleBase):
          service_id = "homepage"
          _default_health_path = "/"

  The contract YAML still names the concrete class
  (`adapters.homepage.lifecycle:HomepageLifecycle`), so the
  orchestrator + ratchet keep their per-service granularity. The
  base just kills ~80 LOC of repetition per service.
- `MaintainerrLifecycle` (Phase 3b) refactored to use the same
  base — net ~70 LOC removed in that file. Behavior is identical;
  the seven Phase-3b Maintainerr tests continue to pass unchanged.
- Five more contract YAMLs declare `plugin.lifecycle_class`. Two
  of them (Authentik, Envoy) didn't have a `plugin:` section at
  all before — added.
- Ratchet floor 11 → 16. **All 16 services that this slice plans
  to cover are now Protocol-conformant.** Phase 3 is done.
- 24 new unit tests cover the base's tri-state probe,
  health-path-overridable behavior, the uniform no-API-key
  contract, and per-service metadata (parameterized across all 6
  no-key adapters incl. refactored Maintainerr).
- Pure additive code — runtime image unchanged.

## [v1.0.298] — 2026-05-01

### Architecture
- **ADR-0003 Phase 3b — media-management lifecycles.** Three more
  ServiceLifecycle adapters land:
  - `BazarrLifecycle` — YAML variant of the Sab/*arr "wait for the
    file" pattern (`bazarr/config/config.yaml` ``apikey: <value>``).
    Replaces the structural shape that allowed
    `ensure-bazarr-language-profile` to log a settings POST 500 while
    returning status=ok.
  - `JellyseerrLifecycle` — JSON variant (`settings.json`
    `main.apiKey`). Same flow, different format reader.
  - `MaintainerrLifecycle` — first "no API key concept" lifecycle.
    Maintainerr is a consumer of upstream services' keys (Jellyfin,
    Sonarr, Radarr, Jellyseerr, Tautulli) and has no key of its own.
    `probe_has_api_key` returns ok with explanatory detail; mint /
    discover / persist are inert with `reason=no_api_key_concept`
    evidence. Establishes the uniform shape so the orchestrator can
    call every lifecycle method on every service without
    per-service if-statements.
- Three more contract YAMLs (bazarr + jellyseerr + maintainerr)
  declare `plugin.lifecycle_class`. Ratchet floor 8 → 11.
- 25 new unit tests pinning YAML/JSON discover paths, structural
  vs transient mint failures, and the no-api-key uniform contract.
- Pure additive code — runtime image unchanged.

## [v1.0.297] — 2026-05-01

### Architecture
- **ADR-0003 Phase 3a — download-client lifecycles.** First slice
  of Phase 3, covering the two services whose absence broke the
  bootstrap on 2026-05-01:
  - `media_stack.adapters.qbittorrent.lifecycle.QbittorrentLifecycle`
    — qBit's auth model (session-cookie via username/password, no
    static API key) maps the "API key" concept to the WebUI admin
    password. `mint_api_key` fails LOUDLY (`transient=False`) when
    the password env is missing — explicitly avoiding the
    `ensure-qbittorrent-categories` silent-error-as-ok bug class
    noted in memory. Probe treats both 200 and 403 as "running"
    (403 just means the auth gate is doing its job).
  - `media_stack.adapters.sabnzbd.lifecycle.SabnzbdLifecycle` —
    structurally a sibling to `ServarrLifecycle` but with INI
    rather than XML config. Same "wait for the file" mint
    semantic — `transient=True` while sabnzbd.ini hasn't been
    written, `transient=False` when the file exists but the
    `[misc] api_key=` line is missing.
- Two more contract YAMLs (qbittorrent + sabnzbd) declare
  `plugin.lifecycle_class`. Permissive ratchet floor bumped from
  6 → 8 services.
- 29 new unit tests pinning probe tri-state, idempotent mints,
  honest mint failures, env+secret persist semantics.
- Pure additive code — runtime image unchanged.

## [v1.0.296] — 2026-05-01

### Architecture
- **ADR-0003 Phase 2 — first lifecycle implementations land.** Two
  adapters now satisfy the Phase-1 `ServiceLifecycle` Protocol:
  - `media_stack.adapters.jellyfin.lifecycle.JellyfinLifecycle` —
    wraps the existing `infrastructure.jellyfin` code (probe via
    `/System/Info/Public`, discover via the canonical SQLite reader
    with name-preference matching, mint via `http_preflight`,
    persist via env + best-effort k8s secret patch). The 13 existing
    Jellyfin infrastructure classes stay in place for now; Phase
    4-6 will switch consumers over and prune the redundancy.
  - `media_stack.adapters.servarr.lifecycle.ServarrLifecycle(service_id)` —
    one parameterized class for sonarr / radarr / lidarr / readarr /
    prowlarr (Bazarr is genuinely different — Phase 3). "Mint" is
    poll-and-wait for the *arr-process-generated `<ApiKey>` in
    `config.xml`; transient=True signals warmup, transient=False
    signals "file present but key missing" (operator action needed).
- **Six contract YAMLs** (jellyfin + 5 *arr) now name a
  `plugin.lifecycle_class`. The orchestrator doesn't consume it yet
  (Phase 4 territory) — the field is currently policed by a permissive
  ratchet that asserts: when present, the class MUST exist and MUST
  pass `isinstance(impl, ServiceLifecycle)`. Floor pinned at 6
  services; ratchets upward as Phase 3 lands more.
- 47 unit tests covering both adapters' tri-state probes, idempotent
  mints, file-not-yet-generated transient handling, env+secret
  persist with partial-failure semantics, and the YAML ratchet.
- Pure additive code — runtime behavior unchanged from v1.0.294. No
  image rebuild; legacy paths still in use until Phase 4.

## [v1.0.295] — 2026-05-01

### Architecture
- **ADR-0003 Phase 1 — `ServiceLifecycle` Protocol landed.** New
  `media_stack.domain.services` package with the Protocol every service
  adapter will implement (`probe_running`, `probe_has_api_key`,
  `mint_api_key`, `discover_api_key`, `persist_api_key`) plus the
  value types it speaks (`ProbeResult` tri-state probe outcome,
  `Outcome[T]` ensurer result with transient-vs-permanent failure
  signal, `OrchestrationContext` read-only runtime). Pure addition;
  no behavior change — runtime image is unchanged from v1.0.294.
  Phase 2 (Jellyfin + Servarr lifecycle implementations as the
  proofs) will deploy.
- 20 unit tests pinning factories, frozen-ness, runtime-checkable
  Protocol semantics, and the package re-export surface.

## [v1.0.294] — 2026-05-01

### Fixed
- **Onboarding banner counter now advances honestly.** The banner used to
  read "0 done / N running of N steps" forever even as bootstrap sub-jobs
  finished. Root cause: ``get_running_tree()`` filtered children to
  ``status=running``, so settled siblings vanished from the tree the
  instant they completed — taking the "done" tally with them instead of
  contributing to it. The tree now keeps settled descendants under their
  still-running parent (with terminal status intact and elapsed frozen at
  completion), while the top-level set stays gated to running so the Jobs
  page card still empties when bootstrap finishes. Backend-only change;
  the existing frontend flatten-and-count logic lights up automatically.

## [ui-v1.1.0] — 2026-04-24

### UI
- **Full luxury React 19 rewrite of the dashboard.** Replaces the prior
  thin Preact placeholder. Stack: React 19 + Vite 6 + Tailwind v4 (beta) +
  shadcn/ui + Tanstack Router/Query/Table + Framer Motion + cmdk + Sonner +
  Vaul + Geist Variable fonts.
- **Mobile-first.** 44px touch-target floor, `safe-area-inset-*`,
  `@media (hover:hover)` to suppress hover-stuck on touch devices, bottom
  nav.
- **PWA.** Manifest + service worker (NetworkOnly `/api/*`, CacheFirst Geist
  CDN), install-prompt, offline-friendly app shell, 3 home-screen shortcuts
  (Media Integrity, Logs, Reconcile now).
- **Theming.** Light/dark via `next-themes` + OKLCH palette; honors system
  preference.
- **Routes.** `/media-integrity` (adapter health, reconcile/enforce,
  needs-review queue with optimistic updates), `/content`, `/logs`, `/ops`,
  `/routing`, `/webhooks`, `/users`, `/me`. Plus `/profile`, `/settings`
  placeholders and a `$.tsx` 404 catchall.
- **Polish.** ErrorBoundary with diagnostics-copy, SkeletonCard /
  SkeletonTable primitives, skip-link a11y, in-app `CommandPalette` (cmdk)
  bound to ⌘K, `ConnectionStatus` indicator polling `/api/health`.

### Fixed
- `ConnectionStatus.tsx` was polling `/api/healthz` (404 in prod) — corrected
  to `/api/health`.
- PWA PNG icons (`public/icons/*.png`) shipped as 0-byte placeholders —
  regenerated from SVG sources via ImageMagick.

### Quality ratchets (new)
- `pnpm size` — `size-limit` enforcing per-chunk + total JS gzip budget
  (250 KB ceiling; current 240.8 KB).
- `pnpm check:todos` — TODO/FIXME count snapshot at `.ratchets/todos.json`
  (currently 11).
- `pnpm lint` — flat ESLint config locks `no-console`, `no-only-tests`,
  `@typescript-eslint/no-explicit-any` at 0.
- a11y: `vitest-axe` against AppShell / CommandPalette / UserMenu /
  MediaIntegrity routes, blocks `serious` + `critical` violations.
- Path-contract test: every `/api/*` literal in `src/` must exist in the
  OpenAPI spec.
- Manifest contract: every PNG referenced from `dist/manifest.webmanifest`
  must exist + match declared dimensions.

### Distribution
- Image: `harbor.iomio.io/public/media-stack-ui:v1.1.0` — already deployed
  (k8s + compose manifests pinned). Tests: 462/462 passing. Bundle:
  240.8 KB total JS gzip.

### Auth
- Unchanged. Cookies issued by Authelia, validated by Envoy `ext_authz`;
  the UI sends `credentials: "same-origin"`. No new tokens, no
  localStorage credentials.

## [v1.0.94] — 2026-04-19

### Security
- **Admin bootstrap redesign.** `STACK_ADMIN_PASSWORD` is now a one-time seed
  used only until the first successful login. The dashboard forces a password
  rotation on first login; rotated credentials live in
  `${CONFIG_ROOT}/controller/users.json` and the env value is never consulted
  again. Added `source` field (`env-seed` / `env-legacy` / `rotated`) so
  support can see which path produced a credential.
- **Break-glass recovery.** Deleting `users.json` re-enables the seed
  credential for a single login, documented in `docs/auth-guide.md`.

### Auth
- **Authelia 4.38 OIDC rebuild.** Ground-up rewrite of OIDC config generation.
  New `OidcCrypto` helper emits RSA PEM keys via `openssl` and hashes client
  secrets with `passlib` pbkdf2-sha512 in Authelia's adjusted-base64 format
  (`+` → `.`) — the only form Authelia's internal parser accepts.
- **Declarative OIDC client registry** at `contracts/auth/oidc_clients.yaml`
  with `{base}`, `{sub}`, `{gateway}` placeholders. Moves Jellyseerr client
  registration out of hardcoded Python into a contract that travels with
  configuration.
- **Domain topology auto-detection.** `_resolve_domain_pair` now handles
  flat profiles (K8s, `routing.base_domain` set, no sub) separately from
  nested profiles (compose). Fixes the 2026-04-18 K8s login loop where
  `auth.m.iomio.io` was being emitted instead of `auth.iomio.io`.
- **Secret preservation across regens.** `_reuse_existing_secrets` + placeholder
  detection so `configure_auth` never trashes Authelia's SQLite encryption
  key, which would brick startup on the next boot.

### Infrastructure
- **Compose ↔ K8s parity enforcement.** 13 parity tests across 6 test classes
  in `tests/unit/test_compose_k8s_parity.py` (shared config mounts, env vars,
  admin seed values, image tags, kustomization coverage, state persistence,
  placeholder seeds).
- **Controller state PVC on K8s.** New `media-stack-config-controller` PVC
  (1Gi) mounted at `/srv-config/controller` so `users.json`, audit log,
  API tokens, and password policy survive pod restart — previously ephemeral.
- **Authelia config PVC on K8s.** `/config` is now a PVC instead of `emptyDir`;
  the init container only seeds when empty. Matches compose bind-mount
  semantics so controller-written `configuration.yml` is what Authelia reads.
- **`auth-authelia.yaml` added to kustomization.** `kubectl apply -k k8s/`
  now provisions Authelia; previously required a separate apply.

### Routing
- **Prowlarr UrlBase via API reconciliation.** File-patching `config.xml` is
  insufficient — Prowlarr rehydrates the file from its SQLite DB on startup.
  New `_reconcile_url_base` in `services/apps/servarr/http_preflight.py`
  PUTs `/api/v1/config/host` so the value lands in the DB and survives
  restart. Covers all ARR apps with the correct API version (`v3` for
  Sonarr/Radarr, `v1` for the rest).
- **Envoy prefix vs UrlBase audit test** (`test_envoy_prefix_matches_app_url_base.py`)
  enforces on-disk consistency — if Envoy advertises `/app/<slug>`, the app's
  config must serve from that prefix, or browser assets will 404.

### Distribution
- `bin/regen-dist.sh` regenerates `dist/docker-compose.yml` and
  `dist/k8s-deploy.yaml` from sources; both bundles now pin
  `media-stack-controller:v1.0.94` (previously drifted to `v1.0.1` and
  `v1.0.6` respectively).

## [v1.0.67 .. v1.0.69] — 2026-04-17 .. 2026-04-18

### TLS
- **Envoy auto-mints a self-signed cert** the first time the compose generator
  finds an empty cert dir (`_resolve_or_mint_certs`). HTTPS on 443, HTTP on
  80 redirects to HTTPS. Required for Authelia 4.38 session cookies.
- **Cert upload UI** — dashboard can replace the self-signed cert with a
  user-provided one; controller reloads Envoy after install.
- **Controller-triggered Envoy reload regenerates config first** before
  SIGHUP-ing Envoy, so cert swaps and vhost additions actually land.
- **Copy Hosts button** on the dashboard now emits every Envoy vhost plus
  a sync-hosts script, resolving the "I added an app and `/etc/hosts` is
  out of date" footgun.

## [v1.0.48 .. v1.0.65] — 2026-04-13 .. 2026-04-17

### Security hardening (controller)
- Origin/Referer cross-check on CSRF (v1.0.51)
- IP-based failed-login lockout — 20 fails / 5 min → 15 min 429 (v1.0.52)
- Audit every mutating POST, hash-chained (v1.0.53)
- RBAC `controller_admin` role + session cookies + OIDC redirect hook (v1.0.56)
- Refresh-token pattern + K8s NetworkPolicy (v1.0.57)
- Sudo re-auth gate + webhook HMAC verification (v1.0.59)
- Audit-log chain verifier, auto Envoy reload on cert install (v1.0.64)
- Security event Prometheus counters + session idle timeout (v1.0.65)

### Security baseline harness
- Pure-HTTP audit runner (`tests/security/security_audit.py`) with 19 checks
  across authentication, CSRF/session, response hygiene, and abuse prevention.
- Per-service suites for Controller, Jellyfin, Jellyseerr, Sonarr, Radarr,
  Prowlarr, Bazarr.
- CI gate (`security-baseline-harness` job) runs harness-unit tests on every
  push; live per-service suites run when a target is reachable.

## [v1.0.1 .. v1.0.46] — 2026-04-08 .. 2026-04-13

### Platform foundation
- Controller security hardening: auth by default, bearer tokens, global
  CSRF + rate limit, SSRF block, security headers (v1.0.46).
- User + role management: CRUD API, dashboard UI, Authelia + Jellyfin
  providers, hash-chained audit log.
- Controller v1.0.2: `argon2-cffi` + user-mgmt validator tolerance.
- Class-based architecture refactor (v1.0.6).
- Home screen rails, qBit categories, Maintainerr path (v1.0.5).
- TRASHguides custom-format import API (Phase 3b).
- Configure-auto-scan job for Sonarr/Radarr → Jellyfin (Phase 3a).
- Bootstrap DAG: configure-auth, configure-indexers, configure-arr-clients
  jobs wired through the jobs framework.

## [v1.0.0] — 2026-04-07

- Initial release: images pushed to `harbor.iomio.io`, all manifests pinned.
