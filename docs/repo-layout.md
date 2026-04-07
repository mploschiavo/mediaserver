# Repo Layout

The repository keeps deployable runtime assets in existing stable paths while introducing a product-oriented top-level structure for long-term maintainability.

## Current Runtime-Critical Paths

- `k8s/`: Kubernetes manifests, profile overlays, bootstrap job manifests
- `docker/`: Docker Compose runtime manifests and env templates
- `contracts/`: per-service YAML (`services/*.yaml`), profile YAML, adapter-hooks config (`adapter-hooks.k8s.yaml`), and catalog YAML
- `bin/`: install, reconcile, diagnostics, and verification tooling
- `tests/`: unit and e2e test suites
- `docs/`: architecture, operations, and design documents

Manifest-driven pluggability paths inside `bin/`:
- `src/media_stack/contracts/plugins/<technology>/manifest.json`: technology registration contract
- `src/media_stack/contracts/runner_operation_plans.json`: shared runner phase contract
- `src/media_stack/contracts/media_server_operation_plans.json`: media-server phase contract
- Event-driven handler registration in plugin manifests: `event_handlers.<EVENT>.<handler_key>`
- `src/media_stack/services/apps/<app>/`: app-local implementations
- `src/media_stack/services/download_client_adapters/`: torrent/usenet adapters
- `src/media_stack/services/media_server_adapters/`: media server adapters
- `src/media_stack/services/apps/servarr/technologies/`: Servarr adapters

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
- App wiring and defaults: `contracts/`, `apps/`, `config/`
- Technology registration and role bindings: `contracts/media-stack.config.json`, `src/media_stack/contracts/plugins/`
- Shared runtime lifecycle orchestration: `bin/controller.py`, `src/media_stack/services/runtime_factory/*`, `src/media_stack/services/runner_operations_service.py`
- App/technology behavior modules: `src/media_stack/services/apps/*`, adapter directories
- Quality gates and regressions: `tests/`
- Product narrative and operator docs: `docs/`

## Why This Layout

- Keeps existing automation stable.
- Makes migration to stronger GitOps patterns straightforward.
- Improves onboarding for contributors by separating runtime assets from design/docs concerns.

---

**Project Steward**
Matthew Loschiavo • [matthewloschiavo.com](https://matthewloschiavo.com) • [mploschiavo@gmail.com](mailto:mploschiavo@gmail.com) • [LinkedIn](https://www.linkedin.com/in/matthewloschiavo)
