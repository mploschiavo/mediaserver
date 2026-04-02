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
2. Kubernetes manifests in `k8s/**/*.yaml`
3. Typed/defaulted behavior in Python services under `scripts/bootstrap_services/`
4. Runtime app state (UI edits) as temporary drift to be reconciled back into code

If a behavior differs between UI and repo code, repo code wins after next reconcile/bootstrap.

## Native Manifest Policy
- Prefer native Kubernetes YAML and native Docker Compose YAML as the runtime contract.
- Do not invent bespoke manifest DSLs/schemas when native fields/syntax already express the behavior.
- Keep platform config close to upstream-native semantics (`apiVersion/kind/spec` for Kubernetes, `services/networks/volumes` for Compose).
- If higher-level config is needed for orchestration, it must map transparently to native manifests and must not obscure or replace native capability.
- Avoid custom wrapper keys that duplicate native manifest fields.
- During refactors, preserve native manifest readability and portability over framework-specific abstractions.

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

## Platform Swap Contract
Swapping deployment platforms/runtimes must be platform-local and config-driven, with the same isolation expectations as app swaps.

Primary binding points:
- `platform_target` / `PLATFORM_TARGET` in rebuild CLI config (`scripts/cli/rebuild_cli_config_service.py`)
- platform adapter factory in `scripts/core/platform_adapter.py`
- platform adapter modules implementing `RebuildPlatformAdapter` (for example `scripts/core/kubernetes_rebuild_platform_adapter.py`, `scripts/core/compose_rebuild_platform_adapter.py`)
- SDK/runtime adapters in `scripts/core/` (for example `kube.py`, `docker.py`)

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
- Keep isolation on separate axes:
  - deployment target (`k8s`, `compose`, future targets)
  - container runtime (`docker`, `containerd`, future runtimes)
  - edge/router provider (ingress/traefik/nginx/etc.)
  - authN/authZ provider (`authelia`, `authentik`, future providers)
- A change in one axis must not require code edits in the other axes beyond declarative binding/config.

### Edge/Auth Isolation Contract
- Reverse-proxy routing and auth provider wiring must be declarative and pluggable, not hard-coded into app services.
- Shared orchestration must not embed provider-specific branches like `if auth_provider == ...`; use provider adapter bindings.
- Route strategy (`subdomain` vs `path-prefix`) must be configurable and provider-agnostic.
- Preserve device-critical direct host support for media servers (for example Jellyfin native TV/mobile clients) even when consolidated path-prefix routing is enabled for browser apps.
- Default security posture for internet exposure:
  - explicit exposure intent flag/config is required before enabling public routes
  - centralized SSO/forward-auth policy is preferred over per-app bespoke auth config
  - TLS and auth middleware references must come from platform/provider config, not app code constants
- New provider/runtime integrations must ship with:
  - adapter module
  - declarative config binding
  - contract tests proving no shared-orchestration code changes are required for provider swaps

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

## Bash vs Python Policy
Keep Bash when it is a tiny, stable wrapper.
Migrate Bash to Python when logic includes non-trivial branching, loops, parsing, retries, JSON/YAML transforms, or needs tests.
For Kubernetes and Docker runtime operations, Python SDK adapters are required; do not implement runtime behavior via CLI wrapper scripts.

## Kubernetes Client Policy
- Python Kubernetes helpers must use the official Kubernetes Python client (`kubernetes-client/python`) through `scripts/core/kube.py`.
- Use `KubernetesClient` naming in Python code; do not add new `KubectlClient` imports/usages.
- Do not add new Python code that shells out to `kubectl`; use the Kubernetes API adapter instead.
- Kubernetes orchestration/runtime behavior must be implemented in Python service/adapters, not CLI command wrappers.

## Docker Client Policy
- Python Docker helpers must use the official Docker SDK for Python (`docker-py`) through `scripts/core/docker.py` adapter boundaries.
- Do not add new Python code that shells out to `docker` or `docker compose`; use Docker SDK adapters/services instead.
- Compose/runtime orchestration must be API/SDK-driven in Python, not wrappers around Docker CLI commands.
- If an operator-facing shell wrapper exists, it must remain a thin entrypoint and must not be the implementation boundary for runtime logic.

## Debug Artifact Policy
- Do not add tracked `*debug*` wrapper/entrypoint files for bootstrap flows.
- Use runtime log levels (`MEDIA_STACK_LOG_LEVEL`) and structured logs instead of dedicated debug scripts.

## Bootstrap Image Packaging Contract
Bootstrap jobs run from a prebuilt image (`docker/bootstrap-runner.Dockerfile`).
- Any new module imported by `scripts/bootstrap-apps.py` must be included by the image build context.
- Keep runtime Python under `scripts/` so `COPY scripts /opt/media-stack/scripts` captures required modules.
- Validate runtime changes by rebuilding/pushing the bootstrap runner image before live bootstrap tests.

## Scripts Directory Policy
- Keep `scripts/*.sh` as user/operator entrypoints and small compatibility wrappers.
- Keep `scripts/*.py` limited to CLI entrypoints and intentionally shared tooling.
- Put domain behavior in `scripts/bootstrap_services/**` rather than root `scripts/` where possible.
- App-specific Python implementation must not live under `scripts/cli/`; place it under `scripts/bootstrap_services/apps/<app>/**`.
- `scripts/cli/*.py` must remain app/technology-neutral orchestration glue.
  - Do not hard-code technology/app names in `scripts/cli`.
  - If a CLI is app-specific or stack-composed, move it under `scripts/bootstrap_services/apps/<app>/cli/` (or `apps/stack/cli/` for stack-level UX flows).
  - Shell wrappers may keep historical script names and should resolve via `scripts/lib/run-python-cli.sh`.
- Reconcile orchestration contract:
  - `adapter_hooks.microk8s_reconcile.phase_plan` is the source of truth for reconcile order/conditions.
  - Reconcile steps must declare `event` + `handler` and use `RunnerEvent`.
  - Do not add bespoke hard-coded reconcile sequencing in CLI modules.

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

## Validation Checklist (Pre-Merge)
1. `bash -n scripts/*.sh scripts/lib/*.sh`
2. `python3 -m py_compile` for modified Python files
3. `ruff check scripts tests`
4. `black --check scripts tests`
5. `python3 -m unittest discover -s tests/unit -p 'test_*.py'`
6. `rg -n "from core.kube import KubectlClient|KubectlClient.from_environment" scripts tests` returns no matches
7. For modified Python files, verify no new subprocess/shell invocations execute `kubectl`, `docker`, or `docker compose`; use SDK adapters instead.
8. `git ls-files | rg -i "debug"` contains no tracked debug wrapper/CLI files
9. `rg -n -i "\\b(arr|homepage|jelly|maintainerr|qb|sab|goodread)\\w*\\b" scripts/bootstrap_services scripts/core scripts/bootstrap_lib --glob '!scripts/bootstrap_services/apps/**'` returns no matches
10. `rg -n -i "(jellyfin|jellyseerr|prowlarr|qbittorrent|qbit|sabnzbd|sonarr|radarr|lidarr|readarr|bazarr|unpackerr|maintainerr|tautulli|homepage|plex|emby|flaresolverr)" scripts/cli/*.py` returns no matches
11. For manifest/config changes, confirm no new bespoke manifest DSL was introduced where native Kubernetes/Compose YAML fields would suffice.
12. For new integrations, confirm official SDK/client options were evaluated and used unless explicitly documented otherwise.
13. For platform/auth/routing changes, verify bindings remain declarative (target/runtime/router/auth provider) and no new provider-specific branching appears in shared orchestration modules.
14. Live bootstrap smoke in cluster:
   - `bash scripts/bootstrap-all.sh`
   - confirm final phase summary is all `ok`

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
