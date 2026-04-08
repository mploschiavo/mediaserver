# Source of Truth

This platform follows a strict desired-state hierarchy.

![Source-of-truth flow](diagrams/source-of-truth-flow.png)

## Canonical Sources

1. **Git-tracked manifests and configs**
  - `k8s/*.yaml`
  - `k8s/profiles/*`
  - `docker/docker-compose.yml`
  - `docker/.env.example`
  - `contracts/defaults/*.yaml`
  - `contracts/services/*.yaml`
  - `contracts/media-stack.profile.yaml`
  - `contracts/media-stack.profile.schema.json`
  - `src/media_stack/contracts/runner_operation_plans.json`
  - `src/media_stack/contracts/media_server_operation_plans.json`
  - scripts under `bin/`

2. **Cluster secrets generated/reconciled from code**
  - `media-stack-secrets` (Kubernetes Secret)
  - local export file `secrets.generated.env` for operator visibility

3. **Runtime state in each application**
  - Arr/Prowlarr/Jellyseerr/Jellyfin UI state

Runtime state is not authoritative if it conflicts with declarative config.

## Binding and Registration Contracts

- Active component choice is declared only in `technology_bindings`.
- Technology registration is declared only in per-service YAML contracts.
- Runtime config can override behavior plans and handlers, but not class registration maps.
- Shared lifecycle events are concrete (`RunnerEvent`) and handlers are technology-local
  (`event_handlers.<EVENT>.<handler_key>`), so base orchestration remains technology-neutral.

## Reconciliation Rules

- Install/rebuild scripts always re-apply profile manifests.
- Bootstrap job re-applies manifest-bound cross-app integration state.
- Optional periodic reconcile job reduces drift over time.
- Validation scripts (`verify-flow.sh`, smoke tests) assert expected outcomes.

## Drift Policy

Allowed:
- temporary runtime changes while testing

Expected:
- runtime changes are either promoted back into config files, or overwritten by next reconcile

Not allowed for long-term operation:
- undocumented manual UI-only changes that cannot survive rebuild

## Promotion Workflow

1. Make declarative change in Git.
2. Validate in non-prod namespace.
3. Promote the same change to primary namespace.
4. Run verification scripts.

See [docs/gitops.md](gitops.md).

---

**Project Steward**
Matthew Loschiavo • [matthewloschiavo.com](https://matthewloschiavo.com) • [mploschiavo@gmail.com](mailto:mploschiavo@gmail.com) • [LinkedIn](https://www.linkedin.com/in/matthewloschiavo)
