# Changelog

All notable changes to this stack. Dates reflect when the work landed on `main`.

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
- Image: `harbor.iomio.io/library/media-stack-ui:v1.1.0` — already deployed
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
