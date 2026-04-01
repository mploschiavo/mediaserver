# Repo Layout

The repository keeps deployable runtime assets in existing stable paths while introducing a product-oriented top-level structure for long-term maintainability.

## Current Runtime-Critical Paths

- `k8s/`: Kubernetes manifests, profile overlays, bootstrap job manifests
- `bootstrap/`: app-level configuration-as-code
- `scripts/`: install, reconcile, diagnostics, and verification tooling
- `tests/`: unit and e2e test suites
- `docs/`: architecture, operations, and design documents

Manifest-driven pluggability paths inside `scripts/`:
- `scripts/bootstrap_defaults/plugins/<technology>/manifest.json`: technology registration contract
- `scripts/bootstrap_defaults/runner_operation_plans.json`: shared runner phase contract
- `scripts/bootstrap_defaults/media_server_operation_plans.json`: media-server phase contract
- Event-driven handler registration in plugin manifests: `event_handlers.<EVENT>.<handler_key>`
- `scripts/bootstrap_services/apps/<app>/`: app-local implementations
- `scripts/bootstrap_services/download_client_adapters/`: torrent/usenet adapters
- `scripts/bootstrap_services/media_server_adapters/`: media server adapters
- `scripts/bootstrap_services/apps/servarr/technologies/`: Servarr adapters

## Product-Oriented Structure Scaffolding

- `platform/`: deployment-model scaffolding (`base`, `overlays`)
- `apps/`: per-app ownership boundaries and future app-specific overlays
- `config/`: default/profile/policy config domains
- `examples/`: concrete operator examples and sample environment files
- `docs/diagrams/`: architecture and software-design visual set (source `.mmd` + rendered `.svg/.png`)
- `docs/screenshots/`: runtime UI and cluster evidence artifacts generated from automated capture flows

These directories are intentionally introduced without breaking existing scripts.

## Recommended Ownership Model

- Platform manifests and cluster primitives: `k8s/`, `platform/`
- App wiring and defaults: `bootstrap/`, `apps/`, `config/`
- Technology registration and role bindings: `bootstrap/media-stack.bootstrap.json`, `scripts/bootstrap_defaults/plugins/`
- Shared runtime lifecycle orchestration: `scripts/bootstrap-apps.py`, `scripts/bootstrap_services/runtime_factory/*`, `scripts/bootstrap_services/bootstrap_runner_service.py`
- App/technology behavior modules: `scripts/bootstrap_services/apps/*`, adapter directories
- Quality gates and regressions: `tests/`
- Product narrative and operator docs: `docs/`

## Why This Layout

- Keeps existing automation stable.
- Makes migration to stronger GitOps patterns straightforward.
- Improves onboarding for contributors by separating runtime assets from design/docs concerns.

---

**Project Steward**
Matthew Loschiavo • [matthewloschiavo.com](https://matthewloschiavo.com) • [mploschiavo@gmail.com](mailto:mploschiavo@gmail.com) • [LinkedIn](https://www.linkedin.com/in/matthewloschiavo)
