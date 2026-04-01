# Scripts Lifecycle Guide

This directory intentionally contains both:
- stable shell entrypoints (`*.sh`) for operators
- Python CLI implementations in `scripts/cli/`

## Design Rules

- Keep shell scripts as thin wrappers around Python CLIs when logic is non-trivial.
- Shared wrapper behavior lives in [`scripts/lib/run-python-cli.sh`](./lib/run-python-cli.sh).
- Python CLIs should live in `scripts/cli/*_main.py`.
- Avoid new root-level Python compatibility wrappers.
- `install.sh`, `rebuild-and-bootstrap.sh`, `run-bootstrap-job.sh`, and `bootstrap-all.sh`
  are now Python-backed wrappers with
  phase logging and checkpoint-aware orchestration.

## Pluggable Runtime Contract

- Technology registration is manifest-driven under `scripts/bootstrap_defaults/plugins/*/manifest.json`.
- Shared orchestration scripts must remain technology-neutral.
- Runtime hook overrides are limited to operation handlers and phase plans.
- Generic operation names are used in base orchestration (for example `torrent_client_login`).
- Legacy qB helper/CLI names remain for backward compatibility (`ensure-qbit-credentials.sh`, etc.).

## Stable Operator Entrypoints

- `install.sh`
- `rebuild-and-bootstrap.sh`
- `bootstrap-all.sh`
- `run-bootstrap-job.sh`
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

## Deprecated (Sunset Path)

- `backup-configs.sh`
  - Deprecated in favor of `backup-stack.sh` (full stack backup + secret export).
- `post-install-checklist.sh`
  - Deprecated in favor of `docs/first-run-wiring.md` and `docs/operations.md`.

These scripts remain for backward compatibility and emit deprecation warnings.

---

**Project Steward**
Matthew Loschiavo • [matthewloschiavo.com](https://matthewloschiavo.com) • [mploschiavo@gmail.com](mailto:mploschiavo@gmail.com) • [LinkedIn](https://www.linkedin.com/in/matthewloschiavo)
