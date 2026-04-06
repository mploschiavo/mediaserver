# Scripts Lifecycle Guide

This directory intentionally contains both:
- stable shell entrypoints (`*.sh`) for operators
- framework/orchestration Python CLIs in `src/media_stack/cli/commands/`
- app-specific Python CLIs in `src/media_stack/services/apps/<app>/cli/`

## Design Rules

- Keep shell scripts as thin wrappers around Python CLIs when logic is non-trivial.
- Shared wrapper behavior lives in [`bin/lib/run-python-cli.sh`](./lib/run-python-cli.sh).
- Framework/orchestration CLIs should live in `src/media_stack/cli/commands/*_main.py`.
- App-specific CLIs should live in `src/media_stack/services/apps/<app>/cli/*_main.py`.
- Avoid new root-level Python compatibility wrappers.
- `install.sh`, `deploy-stack.sh`, `run-bootstrap-job.sh`, and `bootstrap-all.sh`
  are now Python-backed wrappers with
  phase logging and checkpoint-aware orchestration.

## Pluggable Runtime Contract

- Technology registration is manifest-driven under `src/media_stack/contracts/plugins/*/manifest.json`.
- Shared orchestration scripts must remain technology-neutral.
- Runtime hook overrides are limited to event handlers, phase plans, bootstrap wrapper phase-script maps, and scale-policy/worker lists in `adapter_hooks`.
- Concrete lifecycle events (`RunnerEvent`) drive orchestration; technologies provide handler bindings per event.
- Operator shell entrypoints are thin wrappers, and app implementation modules live under
  `src/media_stack/services/apps/<app>/cli/`.

## Stable Operator Entrypoints

- `install.sh`
- `deploy-stack.sh`
- `bootstrap-all.sh`
- `run-bootstrap-job.sh`
- `with-env.sh` (load env-file + run command with safe defaults)
- `ensure-qbit-credentials.sh`
- `ensure-jellyfin-bootstrap.sh`
- `ensure-sabnzbd-api-access.sh`
- `run-prowlarr-auto-indexers.sh`
- `sync-unpackerr-keys.sh`
- `validate-bootstrap-config.sh`
- `set-pvc-storage-class.sh`
- `run-playwright-smoke.sh`
- `run-playwright-screenshots.sh`
- `capture-k8s-snapshots.sh`
- `test.sh`

Playwright split:
- `run-playwright-smoke.sh` -> fast ingress/UX assertions only
- `run-playwright-screenshots.sh` -> screenshot artifact generation

**Project Steward**
Matthew Loschiavo • [matthewloschiavo.com](https://matthewloschiavo.com) • [mploschiavo@gmail.com](mailto:mploschiavo@gmail.com) • [LinkedIn](https://www.linkedin.com/in/matthewloschiavo)
