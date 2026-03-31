# Repo Layout

The repository keeps deployable runtime assets in existing stable paths while introducing a product-oriented top-level structure for long-term maintainability.

## Current Runtime-Critical Paths

- `k8s/`: Kubernetes manifests, profile overlays, bootstrap job manifests
- `bootstrap/`: app-level configuration-as-code
- `scripts/`: install, reconcile, diagnostics, and verification tooling
- `tests/`: unit and e2e test suites
- `docs/`: architecture, operations, and design documents

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
- Runtime lifecycle scripts: `scripts/`
- Quality gates and regressions: `tests/`
- Product narrative and operator docs: `docs/`

## Why This Layout

- Keeps existing automation stable.
- Makes migration to stronger GitOps patterns straightforward.
- Improves onboarding for contributors by separating runtime assets from design/docs concerns.
