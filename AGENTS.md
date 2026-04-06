# AGENTS.md

## Why This File Exists
This repo is a declarative media platform, not a click-through homelab script bundle.
Agents and contributors must optimize for:
- Reproducibility
- Safe automation
- Clear ownership boundaries
- Intentional compatibility only when policy requires it

## Agent Operating Contract
Role:
- Act as a principal/staff-plus engineer and chief-architect level software designer.
- Optimize for long-term maintainability, correctness, operability, and clean evolution.

Core standard:
- Build modular, isolated, pluggable, testable, observable systems with explicit boundaries.
- Prefer simple designs first, but refactor aggressively when structure is wrong.

Compatibility policy:
- Do not preserve backward compatibility by default during early development.
- Preserve compatibility only when explicitly required, tied to a supported public API in the current major version, or within an intentional migration window.

Non-negotiables:
- Keep domain logic independent from framework, transport, persistence, and platform internals.
- Prefer composition, dependency inversion, typed contracts, and explicit interfaces.
- Remove dead code, shims, and transitional layers when migration is complete.
- Optimize for long-term codebase health over local patching.

Self-review checklist:
- Are module boundaries explicit and clean?
- Is domain logic isolated from framework/infrastructure code?
- Did I introduce only necessary abstractions?
- Are chosen patterns justified by a concrete problem?
- Did I remove obsolete compatibility paths and dead code?
- Are breaking changes intentional, documented, and policy-aligned?
- Would this design still look correct in 2 years?
- Could a new senior engineer understand top-level architecture quickly?

## Source Of Truth (Priority Order)
1. Declarative config in `bootstrap/media-stack.bootstrap.json`
2. Declarative catalogs/policy maps in `bootstrap/*.catalog.yaml` and `bootstrap/*.policy.yaml`
3. Kubernetes manifests in `k8s/**/*.yaml`
4. Typed/defaulted behavior in Python services under `scripts/bootstrap_services/`
5. Runtime app state (UI edits) as temporary drift to be reconciled back into code

If a behavior differs between UI and repo code, repo code wins after next reconcile/bootstrap.

## Native Manifest Policy
- Prefer native Kubernetes YAML and native Docker Compose YAML as the runtime contract.
- Do not invent bespoke manifest DSLs/schemas when native fields/syntax already express the behavior.
- Keep platform config close to upstream-native semantics (`apiVersion/kind/spec` for Kubernetes, `services/networks/volumes` for Compose).
- If higher-level config is needed for orchestration, it must map transparently to native manifests and must not obscure or replace native capability.
- Avoid custom wrapper keys that duplicate native manifest fields.
- During refactors, preserve native manifest readability and portability over framework-specific abstractions.

## Config-Driven Mapping Policy
- Keep static mapping data in declarative files under `bootstrap/`, not Python conditionals/maps.
- Mappings include (non-exhaustive): install-profile app lists, app toggle->section maps, reserved-key lists, and policy path bindings.
- Python policy modules should interpret config; they should not be the source of truth for mapping data.
- If a mapping update requires code edits instead of config edits, stop and refactor before merge.
- Do not embed structured runtime payload templates (JSON/YAML bodies) as Python dict literals in services.
- Keep payload templates in declarative config files (for example under `bootstrap/` or `config/defaults/`) and load/patch them in code.

## Runtime Artifact Replay Contract
- Each rebuild/bootstrap run must emit target-separated runtime artifacts under `.state/runtime-artifacts/<run-id>/`.
- Kubernetes runs must capture the resolved YAML payloads actually applied to the cluster (post-override values), plus minimal metadata for replay/audit.
- Compose runs must capture:
  - fully expanded Compose YAML (resolved env substitutions),
  - selected/runtime Compose YAML used for deployment decisions,
  - deployment plan metadata (project, service order, routing/auth posture).
- Runtime artifacts must be organized by target (`kubernetes/`, `compose/`) and be reusable for replay, troubleshooting, and future migration work.
- Logging must include artifact root/file paths for operator visibility, but must never print secret values or token contents.

## Destructive Operation Policy
- Destructive operations (environment teardown, namespace deletion, container removal, data wipe) must never be the default.
- The default for any destructive flag must be the safe/no-op value (e.g., `DELETE_NAMESPACE=0`, `WIPE_DATA=0`).
- Enabling a destructive operation requires explicit opt-in via an environment variable or CLI flag; it must never be inferred from the absence of arguments.
- Destructive teardown paths must use two-step confirmation:
  `DELETE_NAMESPACE=1` plus `DELETE_NAMESPACE_CONFIRM=<target-identifier>` (or `I_UNDERSTAND`).
  Target identifier is namespace for Kubernetes and compose project name for Compose.
- All destructive operations must emit a prominently labelled `[DESTRUCTIVE]` log line before executing that names what will be removed and how to disable it.
- Calling a CLI script with no arguments must never trigger a destructive side effect; it must either show help, fail fast with a clear error, or perform a read-only/dry-run action.
- If `scripts/with-env.sh` and the Python CLI have defaults for the same variable, they must agree. `with-env.sh` is the canonical shell default; the Python CLI must match it.
- Automation/CI workflows that require teardown must set the relevant env var explicitly in their pipeline config — not rely on the CLI default.

## Machine Patch And Drift Control Policy
- Host/machine patching must be declarative, versioned, and automated from repo code.
- Do not rely on ad-hoc/manual host edits for Docker, Kubernetes, edge routing, auth providers, certificates, or filesystem prerequisites.
- If a machine patch is required:
  - encode it as platform/provider-owned code under the owning folder (`scripts/core/platforms/**`, `scripts/core/edge/providers/**`, `scripts/core/auth/providers/**`),
  - document required inputs and idempotency behavior,
  - emit structured logs for patch start/result and artifact path(s),
  - persist replayable artifacts under `.state/runtime-artifacts/<run-id>/...` when applicable.
- Any historically manual patch path must be migrated into code before merge; leaving “run this one-off command” instructions as the primary mechanism is not acceptable.
- Repo automation is the source of truth for machine patch state; if manual break-glass intervention occurs, it must be reconciled back into declarative code immediately.

## Shim Removal Policy
- Compatibility shims are temporary and must have explicit removal intent.
- Once migration completes, remove old shims/wrappers/re-export modules in the same or immediate follow-up change.
- Do not retain stale alias modules (for example old `scripts/cli/rebuild_*` re-export stubs) after callers are moved.
- New work must import/use canonical module paths only; do not introduce fresh compatibility indirection without explicit policy approval.

## SDK-First Integration Policy
- Prefer official, well-maintained vendor SDKs/clients before creating custom APIs, wrappers, or protocol layers.
- Do not invent bespoke internal APIs when a best-practice SDK already covers the required behavior.
- If no suitable SDK exists, document the gap and keep any custom adapter minimal, explicit, and easy to replace.
- New integration abstractions must justify why SDK-first was not possible.

## Architecture Layers
- Orchestration entrypoints:
  - Bash wrappers in `scripts/*.sh` (thin only)
  - Python CLIs in `scripts/cli/*.py` (and `scripts/bootstrap-apps.py` composition root)
- Domain/service logic:
  - `scripts/bootstrap_services/`
  - App-scoped compatibility/service modules in `scripts/bootstrap_services/apps/<app>/`
- Reusable bootstrap helpers:
  - `scripts/bootstrap_lib/`
- Cross-cutting infrastructure helpers:
  - `scripts/core/`
- Platform-specific runtime/adapters:
  - `scripts/core/platforms/kubernetes/**`
  - `scripts/core/platforms/compose/**`
- Auth provider implementations:
  - `scripts/core/auth/providers/<provider>/**`
- Edge/router provider implementations:
  - `scripts/core/edge/providers/<provider>/**`
- Shell shared helpers:
  - `scripts/lib/`

### Boundary Rules
- Domain modules do not call shell directly.
- Side effects go through small adapters/services.
- Stateless transforms stay as pure functions.
- Avoid hidden global state when dependency injection is practical.
- Any extensible taxonomy (app names, aliases, install profiles, auth providers, route aliases, env passthrough keys, host/url templates) must be declarative and config-driven.
- Do not hardcode app/provider/profile lists in `scripts/core/`, `scripts/cli/`, `scripts/bootstrap_lib/`, or platform/framework layers.
- If extending apps/providers/profiles requires a code edit instead of a config edit, stop and refactor before merge.
- Provider identifiers (for example `traefik`, `authelia`, `authentik`, `nginx`, `caddy`) are data, not control flow constants.
- Use canonical provider keys in declarative config/profile fields (for example `routing.provider`, `adapter_hooks.edge.router_provider`).
  - For current edge router providers, canonical keys are `traefik` and `envoy` (no aliases/typos).
- Shared orchestration/platform code must not define provider allow-lists, provider enums, or provider-specific default strings; resolve providers from declarative bindings/catalogs.
- App/provider-specific env var names (for example `*_API_KEY`, middleware names, router label keys) must come from config manifests/hooks, not hardcoded lists in shared modules.

## App Swap Contract
Swapping a technology should be app-local and config-driven.

Primary binding points:
- `technology_bindings` in `bootstrap/media-stack.bootstrap.json`
- plugin manifests in `scripts/bootstrap_defaults/plugins/<technology>/manifest.json`
  - `adapter_classes` (servarr/download_client/media_server)
  - `app_service_classes`
  - `service_technology_map`
- optional runtime-only hooks in config:
  - `adapter_hooks.event_handlers`
  - `adapter_hooks.operation_handlers` (legacy compatibility only)
  - `adapter_hooks.runner_event_plans`
  - `adapter_hooks.runner_operation_plans`
  - `adapter_hooks.media_server_event_plans`
  - `adapter_hooks.media_server_operation_plans`

Swap workflow:
1. Add/replace app adapter/service module under `scripts/bootstrap_services/apps/<app>/...`
2. Register the technology in `scripts/bootstrap_defaults/plugins/<technology>/manifest.json`
3. Bind the role in `technology_bindings`
3. Rebuild and push the bootstrap runner image (`scripts/build-bootstrap-runner-image.sh`)
4. Run unit tests + live bootstrap smoke

Do not add new hard-coded `if implementation == ...` logic in orchestration layers when a hookable adapter path is sufficient.

## Deployment Target Contract
The target deployment experience for each platform:
- **Compose:** `docker compose --profile standard up -d` (or equivalent profile flag) stands up the full stack including bootstrap.
- **Kubernetes:** `kubectl apply -k k8s/profiles/standard/` stands up the full stack including bootstrap Deployment with HTTP API.
- Both platforms must converge on the same bootstrap runner image and codebase. The bootstrap runner is a persistent Deployment (not a Job) on both platforms, exposing an HTTP API on port 9100 with `--serve --auto-run`. Platform-specific entry point wiring is allowed; platform-specific bootstrap logic is not.
- Host-side Python CLI orchestration (`deploy-stack.sh` / `deploy_stack_main.py`) is a power-user/CI tool, not the primary deployment path. The primary path must be platform-native.
- Do not introduce Helm charts, Helm values files, or Helm template logic. Use Kustomize overlays for Kubernetes profile management and native Compose profiles for Docker Compose.

### Bootstrap Runner as HTTP API Service
- The bootstrap runner is a persistent Deployment (not a Job) exposing an HTTP API on port 9100.
- Endpoints: `/actions/{name}`, `/status`, `/config`, `/logs/stream` (SSE), `/webhooks`, `/reload`, `/healthz`, `/readyz`, `/` (dashboard).
- Actions: `bootstrap`, `auto-indexers`, `restart-apps`, `sync-indexers`, `envoy-config`, `reconcile`.
- Features: action-level retry with exponential backoff, parallel phase execution (`"parallel": true` in phase config), webhook notifications on action complete/error, runtime config toggles via `/config`, SSE log streaming.
- HTTP endpoints are thin wrappers over existing Python service classes — the API layer must not contain domain logic, only dispatch to service classes.
- Both `docker compose` and `kubectl` entry points run through the same bootstrap runner image and API — do not fork platform-specific bootstrap logic.
- Credential preflight (Jellyfin wizard, qBittorrent password, SABnzbd config) must use HTTP API calls to running services over the container network, not `docker exec` from the host.
- The bootstrap runner must be declared in the compose file and in the K8s manifests (as a Deployment), not created ad-hoc by host-side Python.

## Platform Swap Contract
Swapping deployment platforms/runtimes must be platform-local and config-driven, with the same isolation expectations as app swaps.

Primary binding points:
- `platform_target` / `PLATFORM_TARGET` in deploy CLI config (`scripts/cli/deploy_cli_config_service.py`)
- platform adapter factory in `scripts/core/platform_adapter.py`
- platform adapter modules implementing `RebuildPlatformAdapter`:
  - `scripts/core/platforms/kubernetes/**`
  - `scripts/core/platforms/compose/**`
- SDK/runtime adapters live under their platform folders:
  - `scripts/core/platforms/kubernetes/kube_client.py`
  - `scripts/core/platforms/compose/docker_client.py`

Swap workflow:
1. Add a new platform adapter module that implements the shared platform interface.
2. Keep runtime definition native-first (Kubernetes YAML, Docker Compose YAML, or equivalent native spec for the target).
3. Register target alias/dependencies in `scripts/core/platform_adapter.py` without leaking target logic into orchestration flows.
4. Add/update SDK adapter boundaries for the target runtime (for example Docker, containerd-backed engines) before adding custom abstractions.
5. Add unit tests for target normalization, adapter construction, and platform lifecycle behavior.

### Platform Isolation Rules
- Shared orchestration entrypoints (`scripts/cli/*.py`, `scripts/bootstrap_services/bootstrap_runner_service.py`) must remain platform-neutral; they may select a target and call adapter interfaces only.
- Do not add new target-specific branching in orchestration phases when behavior can live inside a platform adapter.
- If adding/swapping a platform requires broad edits across shared orchestration modules, treat it as a design bug and refactor behind adapter boundaries before merge.
- Prefer additive target plugins/adapters over mutating existing targets.
- `scripts/core/platform_adapter.py` must remain a plugin discovery/dispatch layer only.
  - Do not hardcode target branches like `if target == "k8s"` / `if target == "compose"` in this shared module.
  - Resolve targets through plugin registry discovery under `scripts/core/platforms/*/plugin.py`.
- `scripts/cli/deploy_stack_main.py` must not import platform-specific modules directly.
  - It must bind to platform behavior via shared contracts/registry only.
- Hard folder boundary for platform implementations:
  - Kubernetes-specific Python code must live under `scripts/core/platforms/kubernetes/**`.
  - Compose/Docker-specific Python code must live under `scripts/core/platforms/compose/**`.
  - Shared `scripts/core/**` modules must stay platform-neutral and must not embed target-specific constants/branches.
  - A platform swap should be achievable by adding/removing one platform folder plus declarative bindings, without editing unrelated platform folders.
- Keep isolation on separate axes:
  - deployment target (`k8s`, `compose`, future targets)
  - container runtime (`docker`, `containerd`, future runtimes)
  - edge/router provider (ingress/traefik/nginx/etc.)
  - authN/authZ provider (`authelia`, `authentik`, future providers)
  - storage binding model (k8s PVC/storageClass vs compose host bind mounts)
- A change in one axis must not require code edits in the other axes beyond declarative binding/config.

### Storage Binding Isolation Rules
- Kubernetes target storage is PVC-driven (`storageClass`, claims, access modes) and must not assume host bind paths.
- Compose target storage is host-bind driven (`CONFIG_ROOT`, `DATA_ROOT`, `MEDIA_ROOT`) and must not assume PVC semantics.
- Do not reuse the same filesystem root for both targets during local workflows; use target-specific roots to avoid ownership/permission drift.
- Compose runtime code must preflight bind mount paths (existence + writability for declared container user/group) before container start and fail fast with actionable remediation when invalid.
- Compose runtime must preflight published host ports and hard-fail on collisions before container recreation.
- `resources.disk_space_gb` / `STACK_DISK_ALLOCATION_GB` must be enforced as an explicit compose storage budget guardrail (not log-only).
- Compose runs must emit a storage budget artifact in runtime artifacts for replay/audit.
- Persistence behavior must be validated by tests for restart, recreate/redeploy, and delete/redeploy scenarios.

### Cross-Platform Runtime Support Policy
- Compose is the cross-platform runtime target for developer workstations:
  - Linux, macOS, and Windows are supported through Docker Engine/Docker Desktop.
- Kubernetes local-target assumptions are OS-scoped:
  - MicroK8s is Linux-first; do not assume MicroK8s on Windows/macOS.
  - Keep kubernetes target logic cluster-API-driven, not distro-specific shell assumptions.
- Cross-platform changes must avoid hardcoding Linux-only host paths in shared orchestration logic.
- Add/update unit tests for path/env normalization and target capability selection when introducing platform-sensitive behavior.
- If a runtime feature is Linux-only, document it explicitly in user docs and fail fast with actionable errors on unsupported hosts.

### Edge/Auth Isolation Contract
- Reverse-proxy routing and auth provider wiring must be declarative and pluggable, not hard-coded into app services.
- Shared orchestration must not embed provider-specific branches like `if auth_provider == ...`; use provider adapter bindings.
- Shared orchestration must not embed provider allow-lists like `{none, authelia, authentik}`; allowed providers must come from declarative config/catalog.
- Hard folder boundary for edge providers:
  - Provider-specific implementation must live under `scripts/core/edge/providers/<provider>/**`.
  - Shared modules may load providers via discovery/registry (`scripts/core/edge/provider_registry.py`), but must not hardcode provider behavior inline.
  - Adding/removing an edge provider should primarily be folder add/delete + config updates.
- Hard folder boundary for auth providers:
  - Provider-specific implementation must live under `scripts/core/auth/providers/<provider>/**`.
  - Shared modules may load providers via discovery/registry, but must not hardcode provider behavior inline.
  - Adding/removing an auth provider should primarily be folder add/delete + config updates.
- Route strategy (`subdomain` vs `path-prefix`) must be configurable and provider-agnostic.
- Gateway listener port must be declarative/config-driven (`routing.gateway_port` / `APP_GATEWAY_PORT`), not hardcoded.
- Route/link validation must run against the configured gateway host+port (not fixed assumptions like `:80` or `:18080`).
- Preserve device-critical direct host support for media servers (for example Jellyfin native TV/mobile clients) even when consolidated path-prefix routing is enabled for browser apps.
- For path-prefix single-host routing, browser app links must resolve under the configured gateway path prefix (for example `/app/<service>`) unless explicitly configured otherwise.
- Session affinity for root-relative app assets/API (for example `/assets/*`, `/system/*`, `/settings/*`) must avoid cross-app cookie collisions:
  - do not use one shared mutable cookie key for all apps
  - only stamp app-routing cookies on HTML document navigations, not every API/static response
- Default security posture for internet exposure:
  - explicit exposure intent flag/config is required before enabling public routes
  - centralized SSO/forward-auth policy is preferred over per-app bespoke auth config
  - TLS and auth middleware references must come from platform/provider config, not app code constants
- New provider/runtime integrations must ship with:
  - adapter module
  - declarative config binding
  - contract tests proving no shared-orchestration code changes are required for provider swaps
- Compose edge providers must be plugin-discovered from provider-local modules under `scripts/core/platforms/compose/edge/providers/<provider>/`.
- `scripts/core/platforms/compose/rebuild_platform_adapter.py` must not hardcode provider branches (`traefik`/`envoy`) or import provider implementation classes directly.
- Provider swaps (add/remove/change) must be achievable by provider folder/config changes plus tests, without editing shared compose adapter orchestration.
- Provider-specific runtime implementation files must not live under `scripts/core/platforms/compose/services/`; keep them inside provider folders only.
- Removing an unused provider (for example deleting `edge/providers/traefik` or `edge/providers/envoy`) must not require shared compose service/orchestration edits beyond declarative config selection updates.
- New edge providers (for example `nginx`) must be introduced by adding a new provider folder + plugin + tests, not by adding shared-layer branching.

### Compose Single-DNS Path-Prefix Contract
- Compose path-prefix deployments must support a single gateway DNS entry (`apps.<stack>.<domain>`) for browser apps.
- Local/dev default mapping example: `127.0.0.1 apps.media-dev.local`.
- Required operator URL pattern is `http://<gateway-host>:<edge-port>/app/<service>` (for example `/app/homepage`, `/app/bazarr`, `/app/jellyseerr`).
- Prefer rewrite/header strategies in the selected edge provider over cross-host redirects for browser app routing.
- For ARR-family apps and Prowlarr behind path-prefix routing, bootstrap must set app host `urlBase`/`UrlBase` to `/app/<service>` so post-login navigation and API calls (for example `/system/status`) stay on the correct app route.
- Acceptance must validate representative app pages render as usable UIs from the gateway path-prefix URLs (not just HTTP 200).

### Non-Negotiable Isolation Rules
- `scripts/bootstrap_services/bootstrap_runner_service.py` must remain orchestration-only.
- App/technology-specific branching belongs in:
  - `scripts/bootstrap_services/apps/<app>/**`
  - adapter modules referenced by plugin manifests
  - declarative phase plans under `adapter_hooks.runner_event_plans` /
    `adapter_hooks.media_server_event_plans` (or legacy `*_operation_plans`)
- Do not add new app-specific conditionals in `BootstrapRunnerService` for precheck/ensure/indexer flow; bind operations through phase plans instead.
- If adding/swapping an app requires edits in runner orchestration logic, treat it as a design bug and refactor before merge.
- Prefer adding a new adapter/service + config hook over adding conditionals in shared runtime modules.
- Keep operation names stable; change bindings/hook paths for swaps, not runner internals.
- Do not place app-specific implementation files at `scripts/bootstrap_services/*` root when an app package exists.
- App-specific types/policies/pipelines must live under `scripts/bootstrap_services/apps/<app>/`.
- If a root-level shared module becomes app-specific during refactor, move it (do not leave duplicate logic in root).
- Hard-fail leakage rule:
  - Platform/framework modules outside `scripts/bootstrap_services/apps/**` must not contain app keywords.
  - Treat these tokens as app-specific leakage indicators: `arr`, `homepage`, `jelly`, `maintainerr`, `qb`, `sab`, `goodread`.
  - If any leakage is found, fail the build and move that logic into the owning app package.

### Event Contract (Required)
- Runtime handlers are lifecycle-event driven.
- Use `RunnerEvent` values (in `scripts/bootstrap_services/enums.py`) for all plugin handler registration.
- Plugin manifests register handlers under `event_handlers.<EVENT>.<handler_key>`.
- Runner/media-server phase plans must declare `event` + `handler` (legacy `operation` is compatibility-only).
- Do not re-introduce bespoke per-operation wiring classes in orchestration layers.
- `scripts/bootstrap-apps.py` must not inject inline handler callables for runtime operations; runtime handlers must come from declarative `adapter_hooks.event_handlers`.
- Runtime binding context must be sourced from `technology_bindings` (config/manifests), not hard-coded role maps in entrypoints.
- Keep app-specific runtime modules under `scripts/bootstrap_services/apps/<app>/runtime/*`; shared runtime modules must stay technology-neutral, and pipeline/discovery handler wiring belongs in app-scoped handler modules (`apps/<app>/runtime_ops.py`) plus declarative RunnerEvent config.
- `TechnologyLifecycle*` orchestration classes are obsolete in this repo; lifecycle flow must be expressed through `RunnerEvent` plans and handlers.

## Design Rules
- Prefer composition over inheritance.
- Use OOP only when state ownership/lifecycle is explicit.
- Use dataclasses for config/DTO-style records.
- Introduce patterns only when they reduce complexity:
  - Strategy for real behavior variants
  - Adapter for legacy/shell integration
  - Decorators for retries/timing/instrumentation

### Avoid By Default
- Deep inheritance trees
- Singleton-heavy designs
- Abstract interfaces with one implementation and no boundary value

## File Size And Decomposition Policy
- Avoid monolithic source files across the entire repo.
- For hand-written project files (Python, shell, YAML/JSON manifests, and configs), target `<= 500` lines per file.
- Soft limit: `600` lines. If a change pushes a file above this, split cohesive domains into package-local modules/services in the same change.
- Hard ceiling: avoid introducing or expanding non-generated files above `900` lines.
- Existing oversized files are refactor debt: do not increase them, and prefer shrinking/splitting them whenever touched.
- Prefer Kubernetes-style decomposition for platform code: small orchestration adapters plus service/helper modules grouped by concern.
- If an exception is temporarily unavoidable, document owner + follow-up milestone in the same PR/commit message and schedule immediate extraction.

## Bash vs Python Policy
Keep Bash when it is a tiny, stable wrapper.
Migrate Bash to Python when logic includes non-trivial branching, loops, parsing, retries, JSON/YAML transforms, or needs tests.
For Kubernetes and Docker runtime operations, Python SDK adapters are required; do not implement runtime behavior via CLI wrapper scripts.

## Kubernetes Client Policy
- Python Kubernetes helpers must use the official Kubernetes Python client (`kubernetes-client/python`) through `scripts/core/platforms/kubernetes/kube_client.py`.
- Use `KubernetesClient` naming in Python code; do not add new `KubectlClient` imports/usages.
- Do not add new Python code that shells out to `kubectl`; use the Kubernetes API adapter instead.
- Kubernetes orchestration/runtime behavior must be implemented in Python service/adapters, not CLI command wrappers.
- Do not use Helm. Use Kustomize overlays (`k8s/profiles/*/kustomization.yaml`) for profile-driven manifest management. Helm charts, values.yaml, and Chart.yaml are prohibited.

## Docker Client Policy
- Python Docker helpers must use the official Docker SDK for Python (`docker-py`) through `scripts/core/platforms/compose/docker_client.py` adapter boundaries.
- Do not add new Python code that shells out to `docker` or `docker compose`; use Docker SDK adapters/services instead.
- Compose/runtime orchestration must be API/SDK-driven in Python, not wrappers around Docker CLI commands.
- If an operator-facing shell wrapper exists, it must remain a thin entrypoint and must not be the implementation boundary for runtime logic.
- Compose chaos/fault-injection flows must run through Docker SDK adapters, include healing verification, and remain platform-local.

## Internal Communication Addressing Policy
- Kubernetes internal app-to-app communication must use Kubernetes Service DNS (`http://<service>.<namespace>.svc.cluster.local:<port>` or same-namespace short service name), not Pod IPs.
- Compose internal app-to-app communication must use Docker network service/container DNS (`http://<service>:<port>`), not host DNS names.
- Treat Compose service DNS as the discovery contract analogous to Kubernetes Services; do not depend on bridge IP stability.
- Never persist container bridge IP addresses in bootstrap config, profile defaults, manifests, or runtime policy mappings.
- `.local` hosts (for example `homepage.local`, `jellyfin.local`) are edge/browser/device entrypoints, not internal service-to-service endpoints.
- Loopback addressing (`127.0.0.1` / `localhost`) is allowed only when the caller and callee are the same container/process context:
  - healthchecks inside that container,
  - in-container preflight/repair scripts via `docker exec`,
  - host/operator checks intentionally targeting published host ports.
- Bootstrap/preflight containers attached to the Compose project network must call dependencies via service DNS first; host-loopback + Host-header routing is fallback-only and must be explicit in config.
- Bootstrap API automation must not use edge/gateway path-prefix URLs (for example `/app/<service>`); use direct service DNS URLs only (for example `http://prowlarr:9696`) to avoid HTML/auth redirects during machine-to-machine calls.
- If Compose runtime customizes container names/networks, ensure canonical service DNS aliases remain resolvable for all configured internal URLs.

## Image Pinning Policy
- Runtime workload images must not use floating `latest` tags.
- Prefer digest-pinned image references for compose/k8s workload manifests.
- If digest pinning is temporarily unavailable, use an explicit version tag and document the exception.

## Container Healthcheck Reliability Policy
- Healthchecks are part of the runtime contract for long-running services; treat false-negative health states as production bugs.
- Prefer explicit loopback probes (`127.0.0.1`) over `localhost` in container healthchecks unless dual-stack behavior is intentionally validated.
- Use service-specific lightweight readiness endpoints where available (for example `/health`, `/ping`, `/api/status`) instead of heavy UI pages.
- Every healthcheck definition must set bounded `interval`, `timeout`, and `retries`; add `start_period` for known slow-start services.
- Healthcheck probes must not depend on external DNS, host routing, or authenticated sessions.
- During incident fixes, verify healthchecks from inside the container and inspect `.State.Health.Log` to confirm probe behavior matches runtime reality.
- Compose deployment acceptance requires all services with declared healthchecks to converge to `healthy`; if a service is operational but stuck `starting`/`unhealthy`, fix the probe before merge.

## Edge Page Usability Validation Policy
- Edge/gateway smoke tests must validate browser usability, not only HTTP 200 status codes.
- For routed UI endpoints, smoke checks must follow redirects and fail when the final path escapes the configured route/base-path contract.
- For HTML responses, smoke checks must probe first-party static asset references and fail on local asset 4xx responses.
- Path-prefix compose validation must probe via the configured gateway host + published edge port with Host-header routing.
- Add/maintain unit tests for the "200 but unusable page" class (redirect escapes, missing local assets, broken base-path behavior).
- Validate Homepage tile links during compose edge smoke:
  - tile URLs must only target deployed/selected apps for the active profile,
  - each internal tile URL must resolve to a usable page through the configured gateway/base-path contract,
  - 4xx and auth/forbidden terminal responses on internal tile links are failures.
- For ARR-family and Prowlarr routes in path-prefix mode, include post-login/deep-link checks for `/system/status` and fail when API loads break after navigation.
- If homepage config artifacts change during bootstrap, trigger homepage runtime revalidation so rendered tile links match generated config.

## Browser E2E Test Policy
- E2E tests must validate real browser behavior: DOM element rendering, navigation, button clicks, form submission — not just HTTP status codes.
- Use Playwright with a real Chromium browser for all UI/navigation tests. HTTP-only API checks belong in separate `ux-smoke.spec.ts` / `ingress.spec.ts` files.
- Test structure:
  - `tests/e2e/playwright/tests/ux-smoke.spec.ts` — HTTP-level smoke (status codes, API reachability, tile link validation)
  - `tests/e2e/playwright/tests/app-navigation.spec.ts` — Browser-level tests (page rendering, navigation, login, button clicks, asset integrity)
  - `tests/e2e/playwright/tests/ingress.spec.ts` — Host-routing validation for subdomain mode
  - `tests/e2e/playwright/tests/screenshot-capture.spec.ts` — Automated UI screenshots
- Every app routed through the edge proxy must have browser tests that verify:
  1. Initial page load renders real UI elements (not a blank page with 200 status)
  2. In-app navigation (Settings, sub-pages) stays within the app's base path
  3. Static assets (JS, CSS, images) load without 404 errors
  4. Login flows succeed where applicable (ARR apps, qBittorrent, Jellyseerr)
- Tests must use environment variables for all runtime config (gateway IP, host, port, credentials). Never hardcode hostnames or ports.
- For path-prefix routed apps, verify that clicking internal links (e.g., Settings) navigates to the correct path under `/app/<service>/`, not to a bare `/config/` or `/settings/`.
- For apps with `url_base` configured (SABnzbd, ARR apps), verify that the app generates links with the correct base path.
- When a routing or proxy fix is made, add or update the corresponding browser test to prevent regression.
- Run browser tests after every deploy: `cd tests/e2e/playwright && npx playwright test --project=browser`.

## App Seeding And First-Run Policy
- Any app that is deployed for a profile (including dependency-expanded services) must be seeded out of first-run/setup-wizard state during bootstrap.
- Credential/bootstrap scope (`app_auth.include`, app auth policies, startup seed hooks) must be computed from the effective deployed app set, not only the raw selected-app CSV.
- If an app should remain unseeded by design, require an explicit declarative opt-out flag in bootstrap config/policy and test that behavior.
- Add/maintain unit tests that prove representative deployed apps open in configured/authenticated mode (not registration/onboarding mode) after bootstrap policy transforms.
- Add/maintain profile-driven bootstrap-flag tests that verify:
  - `preconfigure_apps=true` enables bootstrap execution,
  - `preconfigure_api_keys=true` and `apply_initial_preferences=true` produce seeded/configured app state,
  - `auto_download_content=false` keeps automated downloading disabled.

## Chaos Testing Policy
- Chaos testing is declarative/profile-driven (`chaos.enabled`) and defaults to disabled.
- Canonical compose chaos actions are `restart_container`, `pause_container`, and `network_disconnect`.
- Each chaos action must be followed by readiness/healing verification before the next action.
- Chaos scheduling parameters (`duration_minutes`, `interval_seconds`, `actions`) must remain config-driven and test-covered.

## Commit Message Policy
- Do not include `Co-Authored-By` lines, AI/LLM tool attributions, or references to Claude, Anthropic, or any AI assistant in git commit messages.
- Commit messages should describe the change, not the tooling used to produce it.

## Compose Environment Contract
- Compose deploys must fail fast when required host-path env bindings are missing (`CONFIG_ROOT`, `MEDIA_ROOT`, `DATA_ROOT` when referenced by compose spec).
- Do not silently expand missing compose env vars into root-level paths (for example `/media`, `/config`) because this causes configuration drift and misleading storage-guardrail outcomes.
- Storage guardrails are scoped to configured stack storage roots, not whole-disk free space; keep logs/errors/tests explicit about that distinction.

## Debug Artifact Policy
- Do not add tracked `*debug*` wrapper/entrypoint files for bootstrap flows.
- Use runtime log levels (`MEDIA_STACK_LOG_LEVEL`) and structured logs instead of dedicated debug scripts.

## Bootstrap Image Packaging Contract
The bootstrap service runs from a prebuilt image (`docker/bootstrap-runner.Dockerfile`).
- Any new module imported by `scripts/bootstrap-apps.py` must be included by the image build context.
- Keep runtime Python under `scripts/` so `COPY scripts /opt/media-stack/scripts` captures required modules.
- Validate runtime changes by rebuilding/pushing the bootstrap runner image before live bootstrap tests.
- When code changes impact bootstrap/runtime behavior, rebuild the bootstrap runner image before manual compose/k8s verification to avoid stale-image false negatives.
- Mandatory sequencing for image-backed validation:
  - Rebuild image first, then run live verification. Never run live verification first.
  - If image rebuild did not happen in the same iteration, treat runtime test results as invalid.
  - Explicitly log the rebuild command/result before reporting compose/k8s runtime outcomes.

## Scripts Directory Policy
- Keep `scripts/*.sh` as user/operator entrypoints and small compatibility wrappers.
- Keep `scripts/*.py` limited to CLI entrypoints and intentionally shared tooling.
- Put domain behavior in `scripts/bootstrap_services/**` rather than root `scripts/` where possible.
- App-specific Python implementation must not live under `scripts/cli/`; place it under `scripts/bootstrap_services/apps/<app>/**`.
- `scripts/cli/*.py` must remain app/technology-neutral orchestration glue.
  - Do not hard-code technology/app names in `scripts/cli`.
  - If a CLI is app-specific or stack-composed, move it under `scripts/bootstrap_services/apps/<app>/cli/` (or `apps/stack/cli/` for stack-level UX flows).
  - Shell wrappers should use current canonical naming and resolve via `scripts/lib/run-python-cli.sh`.
- Reconcile orchestration contract:
  - `adapter_hooks.microk8s_reconcile.phase_plan` is the source of truth for reconcile order/conditions.
  - Reconcile steps must declare `event` + `handler` and use `RunnerEvent`.
  - Do not add bespoke hard-coded reconcile sequencing in CLI modules.

## Platform Keyword Ownership Policy
- Files containing platform-specific implementation keywords (`kubelet`, `kubectl`, `kube`, `k8s`, `kubernetes`) should live under `scripts/core/platforms/kubernetes/**` unless they are thin compatibility wrappers.
- Files containing compose/runtime-specific implementation keywords (`compose`, `docker`) should live under `scripts/core/platforms/compose/**` unless they are thin compatibility wrappers.
- `scripts/cli/*.py` may orchestrate platform selection, but platform implementation logic must be delegated to platform-local modules.
- If a CLI file accumulates platform implementation behavior, move that behavior into platform-local services/modules and keep the CLI as a boundary wrapper.
- `scripts/cli/deploy_cli_config_service.py` must remain orchestration glue; compose-specific arg/path handling must be implemented in compose-platform helpers.
- `scripts/cli/apply_scale_policy_main.py` must remain a compatibility wrapper only; kubernetes scale behavior belongs in kubernetes platform modules.

## Logging, Errors, and Secrets
- Use structured logging via `scripts/core/logging_utils.py`.
- Never log secrets, tokens, passwords, or API keys.
- Raise typed exceptions from `scripts/core/exceptions.py` for expected operational failures.
- Include enough context for diagnosis: namespace, app, phase, command target, remediation hints.

## Compatibility Requirements
Preserve unless explicitly documented:
- CLI flags and exit code semantics
- Environment variable names
- Config file formats
- Manifest interfaces consumed by existing scripts/automation

Wrapper scripts must maintain historical CLI behavior (help text, error handling, return codes).

## Testing Requirements
Minimum for refactor PRs:
- Unit tests for changed logic in `tests/unit/`
- Wrapper contract tests for CLI parity
- Golden tests for critical bootstrap config sections
- Lint + format checks for modified Python scope
- For compose `path-prefix` edge routing, include automated smoke validation for ARR system-status APIs through gateway URLs (`/app/<arr>/api/.../system/status`) using seeded API keys from runtime config.
- For request-manager auth bootstrap (Jellyseerr/OpenSeerr), include tests that prove local-admin credential seeding still happens when downstream integration/config calls fail.
- For optional integrations (for example Tautulli in Maintainerr), tests must cover partial-degrade behavior so one optional dependency failure does not block all integration/main-setting seeding.
- For edge/path-prefix changes, add/maintain smoke coverage that validates browser-usable navigation (route + critical assets + homepage tile destinations), not only HTTP 200 checks.
- Rebuild required runtime images after code changes and before live testing (for example `scripts/build-bootstrap-runner-image.sh` before compose/k8s bootstrap validation).
- For compose gateway verification, validate all enabled app routes under `/app/<service>` and verify homepage tile links navigate to working destinations, not placeholder/broken URLs.
- For profile-driven bootstrap behavior, add/maintain tests that assert:
  - tile links resolve to working app destinations through the configured gateway path-prefix routes.
  - when `bootstrap.preconfigure_apps=true`, `bootstrap.preconfigure_api_keys=true`, and `bootstrap.apply_initial_preferences=true`, runtime bootstrap flags/env produce configured apps (while `auto_download_content=false` keeps automatic downloading disabled).

Current key test suites:
- `tests/unit/test_shell_wrapper_contracts.py`
- `tests/unit/test_bootstrap_config_golden.py`
- `tests/unit/test_core_decorators.py`

## Test Execution Safety Rules
- Default to targeted unit-test execution first; do not run the full suite when a narrower pattern can validate the change.
- Use `scripts/cli/run_unit_tests_main.py` (or `bash scripts/test.sh`) so per-test resource telemetry is emitted.
- Treat resource-heavy suites (full unit sweep, Playwright, API e2e) as opt-in and announce intent before running them on developer workstations.
- When resource risk is unknown, run with constrained discovery (for example `UNIT_TEST_PATTERN=...`) and review telemetry offenders before broadening scope.
- Prefer setting `UNIT_TEST_TIMEOUT_SECONDS` for workstation runs when investigating instability or potential hangs.

## Lessons Learned (Operational)
- Before long-running e2e flows, create a checkpoint commit of completed refactors; keep subsequent e2e fixes as isolated follow-up commits.
- Compose edge routing must not rely on a single Docker-provider path; always emit inspectable runtime artifacts and a provider-fallback route config when available.
- Selected-app runs must be hard-gated end-to-end:
  - unselected technologies must have runtime inputs cleared (URLs/indexers/flags),
  - phase-plan steps must use `enabled_when_attr`/`enabled_attr` so unrelated prechecks never block bootstrap.
- Profile intent must control download seeding behavior:
  - `minimal`/`standard` profiles (or any profile with `auto_download_content=false`) must disable auto indexer seeding.
  - `full` profiles (or any profile with `auto_download_content=true`) may enable tested-indexer auto add and initial content sync.
- `RUN_BOOTSTRAP=1` validation requires a freshly built bootstrap-runner image whenever runtime imports or module paths changed.
- Keep repo-wide formatting sweeps isolated from behavior changes; do not mix debt cleanup with incident fixes.
- Path-prefix reverse-proxy routing for ARR-family apps is not sufficient by itself; server-side app `urlBase` must be seeded to `/app/<service>` or UI navigation can fail after login even when initial landing works.
- Do not infer deploy profile from environment purpose (`dev`/`prod`); preserve explicit `install_profile` from bootstrap profile YAML so `minimal`/`standard`/`full` behavior remains deterministic.

## Bootstrap Image Dev Workflow

Use this sequence whenever making changes to code that runs inside the bootstrap runner container (`scripts/bootstrap_services/**`, `scripts/bootstrap-apps.py`, or any module imported by the bootstrap runner).

```
# 1. Make code changes
# 2. Rebuild the bootstrap runner image locally (no registry push)
PUSH_IMAGE=0 bash scripts/build-bootstrap-runner-image.sh

# 3. Deploy and run bootstrap against the compose stack
bash scripts/deploy-stack.sh --bootstrap-profile-file examples/bootstrap-profiles/media-compose-standard.yaml
```

If you need a clean slate (full teardown + redeploy):
```
DELETE_NAMESPACE=1 DELETE_NAMESPACE_CONFIRM=<compose_project_name> \
  bash scripts/deploy-stack.sh --bootstrap-profile-file examples/bootstrap-profiles/media-compose-standard.yaml
```

Key rules:
- Never test bootstrap runtime behavior with a stale image. Rebuild first, validate second.
- `PUSH_IMAGE=0` builds and loads the image locally without pushing to the registry.
- Changes to `scripts/cli/`, `scripts/core/`, or `scripts/bootstrap_services/apps/stack/bootstrap_config_policy.py` (host-side policy handler) do **not** require an image rebuild — they take effect on the next deploy run.
- Changes to `scripts/bootstrap_services/runtime_factory/`, `scripts/bootstrap_services/apps/*/`, or `scripts/bootstrap-apps.py` **do** require an image rebuild.

## Validation Checklist (Pre-Merge)
1. `bash -n scripts/*.sh scripts/lib/*.sh`
2. `python3 -m py_compile` for modified Python files
3. `ruff check scripts tests`
4. `black --check scripts tests`
5. `python3 -m unittest discover -s tests/unit -p 'test_*.py'`
6. `rg -n "from core.platforms.kubernetes.kube_client import KubectlClient|KubectlClient.from_environment" scripts tests` returns no matches
7. For modified Python files, verify no new subprocess/shell invocations execute `kubectl`, `docker`, or `docker compose`; use SDK adapters instead.
8. `git ls-files | rg -i "debug"` contains no tracked debug wrapper/CLI files
9. `rg -n -i "\\b(arr|homepage|jelly|maintainerr|qb|sab|goodread)\\w*\\b" scripts/bootstrap_services scripts/core scripts/bootstrap_lib --glob '!scripts/bootstrap_services/apps/**'` returns no matches
10. `rg -n -i "(jellyfin|jellyseerr|prowlarr|qbittorrent|qbit|sabnzbd|sonarr|radarr|lidarr|readarr|bazarr|unpackerr|maintainerr|tautulli|homepage|plex|emby|flaresolverr)" scripts/cli/*.py` returns no matches
11. For manifest/config changes, confirm no new bespoke manifest DSL was introduced where native Kubernetes/Compose YAML fields would suffice.
12. For new integrations, confirm official SDK/client options were evaluated and used unless explicitly documented otherwise.
13. For platform/auth/routing changes, verify bindings remain declarative (target/runtime/router/auth provider) and no new provider-specific branching appears in shared orchestration modules.
14. `rg -n -i "(traefik|authelia|authentik|nginx|caddy)" scripts/core scripts/cli scripts/bootstrap_lib --glob '!scripts/bootstrap_services/apps/**'` returns only declarative adapter/binding definitions (no hardcoded provider branching or allow-lists).
15. `rg -n "if\\s+.*\\b(k8s|kubernetes|compose|docker-compose)\\b" scripts/core/platform_adapter.py scripts/cli/deploy_stack_main.py` returns no shared-orchestration hardcoded platform branches.
16. `rg -n "from core\\.platforms\\.(kubernetes|compose)" scripts/cli/deploy_stack_main.py` returns no matches.
17. `rg -n "from core\\.(edge|auth)\\.providers\\." scripts/core scripts/cli scripts/bootstrap_lib --glob '!scripts/core/edge/provider_registry.py' --glob '!scripts/core/auth/provider_registry.py'` returns no matches.
18. Live bootstrap smoke in cluster:
   - `bash scripts/bootstrap-all.sh`
   - confirm final phase summary is all `ok`
19. For rebuild/bootstrap runs, verify runtime artifacts were written under `.state/runtime-artifacts/<run-id>/` with target-separated `kubernetes/` and/or `compose/` payloads and replay metadata.
20. For modified non-generated files, verify `wc -l` shows no newly introduced file above `900` lines and no existing `>900` line file was expanded without same-change decomposition.
21. Compose changes: run/maintain unit coverage for host-port preflight collisions and storage budget guardrail behavior.
22. Profile/catalog/policy changes: verify mappings changed declaratively (config files) with no new hardcoded map constants in shared policy modules.
23. Chaos changes: verify profile examples keep `chaos.enabled: false` by default and compose chaos action paths are unit-tested.
24. Internal communication changes: verify k8s URLs use Service DNS, compose URLs use service/container DNS, and loopback/container IP usage is limited to same-container probes or explicit operator-host access paths.
25. Keyword ownership checks:
   - search `kubelet|kube|k8s` and ensure implementation files are under kubernetes platform modules (or wrapper-justified).
   - search `compose|docker` and ensure implementation files are under compose platform modules (or wrapper-justified).
26. Compose path-prefix/browser routing changes: verify representative single-gateway URLs render usable pages (not just 200), including:
   - `/app/homepage`
   - `/app/bazarr`
   - `/app/jellyseerr`
27. After runtime-impacting code changes in bootstrap/services/platform adapters:
   - run `PUSH_IMAGE=0 bash scripts/build-bootstrap-runner-image.sh` before live checks
   - run compose deploy/bootstrap verification after rebuild
   - confirm homepage tile destinations and enabled app URLs are validated in the same post-rebuild run.

## Operational Safety Rules
- Prefer additive/idempotent changes.
- Never use destructive `kubectl` or `git` actions unless explicitly requested.
- If live bootstrap fails, capture and document:
  - failing phase
  - failing command
  - relevant pod/job logs
  - exact code remediation applied

## Refactor Sequencing (Ongoing)
High-value next slices:
1. Continue reducing `scripts/bootstrap-apps.py` by extracting remaining cohesive domains.
2. Keep moving subprocess/network/file IO behind `scripts/core/` adapters.
3. Expand contract tests for additional shell wrappers and job-manifest parity.
4. Promote typed config models incrementally for bootstrap JSON sections.
