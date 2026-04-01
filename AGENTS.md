# AGENTS.md

## Why This File Exists
This repo is a declarative media platform, not a click-through homelab script bundle.
Agents and contributors must optimize for:
- Reproducibility
- Safe automation
- Clear ownership boundaries
- Backward-compatible operations by default

## Source Of Truth (Priority Order)
1. Declarative config in `bootstrap/media-stack.bootstrap.json`
2. Kubernetes manifests in `k8s/**/*.yaml`
3. Typed/defaulted behavior in Python services under `scripts/bootstrap_services/`
4. Runtime app state (UI edits) as temporary drift to be reconciled back into code

If a behavior differs between UI and repo code, repo code wins after next reconcile/bootstrap.

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

## App Swap Contract
Swapping a technology should be app-local and config-driven.

Primary binding points:
- `technology_bindings` in `bootstrap/media-stack.bootstrap.json`
- plugin manifests in `scripts/bootstrap_defaults/plugins/<technology>/manifest.json`
  - `adapter_classes` (servarr/download_client/media_server)
  - `app_service_classes`
  - `service_technology_map`
- optional runtime-only hooks in config:
  - `adapter_hooks.operation_handlers`
  - `adapter_hooks.runner_operation_plans`
  - `adapter_hooks.media_server_operation_plans`

Swap workflow:
1. Add/replace app adapter/service module under `scripts/bootstrap_services/apps/<app>/...`
2. Register the technology in `scripts/bootstrap_defaults/plugins/<technology>/manifest.json`
3. Bind the role in `technology_bindings`
3. Rebuild and push the bootstrap runner image (`scripts/build-bootstrap-runner-image.sh`)
4. Run unit tests + live bootstrap smoke

Do not add new hard-coded `if implementation == ...` logic in orchestration layers when a hookable adapter path is sufficient.

### Non-Negotiable Isolation Rules
- `scripts/bootstrap_services/bootstrap_runner_service.py` must remain orchestration-only.
- App/technology-specific branching belongs in:
  - `scripts/bootstrap_services/apps/<app>/**`
  - adapter modules referenced by plugin manifests
  - declarative phase plans under `adapter_hooks.runner_operation_plans` or `adapter_hooks.media_server_operation_plans`
- Do not add new app-specific conditionals in `BootstrapRunnerService` for precheck/ensure/indexer flow; bind operations through phase plans instead.
- If adding/swapping an app requires edits in runner orchestration logic, treat it as a design bug and refactor before merge.
- Prefer adding a new adapter/service + config hook over adding conditionals in shared runtime modules.
- Keep operation names stable; change bindings/hook paths for swaps, not runner internals.
- Do not place app-specific implementation files at `scripts/bootstrap_services/*` root when an app package exists.

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

## Bootstrap Image Packaging Contract
Bootstrap jobs run from a prebuilt image (`docker/bootstrap-runner.Dockerfile`).
- Any new module imported by `scripts/bootstrap-apps.py` must be included by the image build context.
- Keep runtime Python under `scripts/` so `COPY scripts /opt/media-stack/scripts` captures required modules.
- Validate runtime changes by rebuilding/pushing the bootstrap runner image before live bootstrap tests.

## Scripts Directory Policy
- Keep `scripts/*.sh` as user/operator entrypoints and small compatibility wrappers.
- Keep `scripts/*.py` limited to CLI entrypoints and intentionally shared tooling.
- Put domain behavior in `scripts/bootstrap_services/**` rather than root `scripts/` where possible.

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

## Validation Checklist (Pre-Merge)
1. `bash -n scripts/*.sh scripts/lib/*.sh`
2. `python3 -m py_compile` for modified Python files
3. `ruff check scripts tests`
4. `black --check scripts tests`
5. `python3 -m unittest discover -s tests/unit -p 'test_*.py'`
6. Live bootstrap smoke in cluster:
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
