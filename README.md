# Media Automation Stack

Declarative, Kubernetes-native media automation platform for a self-hosted streaming experience.

This repository treats media infrastructure as code and application behavior as configuration code. A full teardown and rebuild should converge back to the same working state with minimal manual UI work.

Maintainer:
- Matthew Loschiavo
- matthewloschiavo.com
- mploschiavo@gmail.com

## Why This Exists

Ad hoc media installs usually fail over time because settings drift across many web UIs, credentials get out of sync, and rebuilding a node means hours of manual clicks.

This platform solves that by making the stack:
- reproducible: deploy + bootstrap from versioned files
- idempotent: reruns reconcile state instead of duplicating it
- environment-aware: same baseline supports `dev`, `e2e`, and `prod` namespaces
- operable: diagnostics, health checks, and verification scripts are built in

See [docs/why-this-exists.md](docs/why-this-exists.md).

## What Problems It Solves Better Than Ad Hoc Installs

- No click-heavy first run for Arr/Prowlarr/Jellyseerr wiring
- Shared credentials and deterministic secret generation
- Arr download handling, categories, and remote path mappings reconciled as code
- Jellyfin libraries, Live TV tuners, plugins, and home UX defaults configured from bootstrap config
- Repeatable rebuild flow suitable for "delete everything and recreate tomorrow"

## Architecture

- Primary architecture doc: [docs/architecture.md](docs/architecture.md)
- Deployment model: [docs/deployment-model.md](docs/deployment-model.md)
- Source-of-truth model: [docs/source-of-truth.md](docs/source-of-truth.md)
- Technology swap guide: [docs/technology-swaps.md](docs/technology-swaps.md)
- Software design models: [docs/software-design-models.md](docs/software-design-models.md)

Diagram set:
- [Logical topology](docs/diagrams/logical-topology.svg)
- [Media data pipeline](docs/diagrams/media-data-pipeline.svg)
- [Bootstrap sequence](docs/diagrams/bootstrap-sequence.svg)
- [Deployment model](docs/diagrams/deployment-model.svg)
- [Source-of-truth flow](docs/diagrams/source-of-truth-flow.svg)
- [Operating loop](docs/diagrams/operating-loop.svg)
- [UI surface map](docs/diagrams/ui-surface-map.svg)
- [Software component model](docs/diagrams/software-component-model.svg)
- [Technology adapter model](docs/diagrams/technology-adapter-model.svg)
- [Bootstrap runtime model](docs/diagrams/bootstrap-runtime-model.svg)

Regenerate diagrams:
```bash
bash scripts/render-architecture-diagrams.sh
```

## Source-of-Truth Philosophy

Priority order:
1. Git-managed manifests and bootstrap config
2. Generated/managed Kubernetes Secrets
3. Reconcile scripts and bootstrap Job/CronJob
4. Runtime app state

If UI state conflicts with bootstrap config, reconciliation pushes runtime back toward declared configuration.

See [docs/source-of-truth.md](docs/source-of-truth.md).

Technology backends are selected declaratively via:
- `technology_bindings` (active backend per role)
- `adapter_hooks` (reflection mapping key -> adapter class + optional operation handler hooks)

## Swap One Technology (Quick Path)

Use this path when replacing one component (for example qBittorrent -> Transmission, Jellyfin -> another media backend, or one Servarr app implementation) without editing `bootstrap-apps.py`.

1. Add/update the app/client config block in `bootstrap/media-stack.bootstrap.json`.
2. Add or update one adapter module under `scripts/bootstrap_services/...`.
3. Register the adapter class path in `bootstrap/media-stack.bootstrap.json` under `adapter_hooks`.
4. Change the active binding in `technology_bindings`.
5. Validate and reconcile:

```bash
bash scripts/validate-bootstrap-config.sh --config bootstrap/media-stack.bootstrap.json --schema bootstrap/media-stack.bootstrap.schema.json
bash scripts/bootstrap-all.sh
```

Optional defaults:
- `adapter_hooks.default_bindings` sets default `torrent_client`, `usenet_client`, and `media_server`.
- `adapter_hooks.technology_aliases` maps shorthand keys (`qbit`, `sab`, `jf`) to canonical keys.

Deep guide: [docs/technology-swaps.md](docs/technology-swaps.md)

## Deployment Model

Supported paths:
- Kubernetes (recommended): profile-driven deploy + bootstrap + verification
- Docker Compose (quick local path)

Kubernetes profiles:
- `minimal`: core stack only
- `full`: core + optional apps + bootstrap reconcile loop
- `public-demo`: safer demo profile (downloader automation reduced)
- `power-user`: full profile + stricter guardrails/TLS helpers

See [docs/deployment-model.md](docs/deployment-model.md).

## Quick Start (Kubernetes)

Recommended one-command install:
```bash
bash scripts/install.sh --profile full --node-ip <NODE_IP>
```

Storage-mode examples:
```bash
# default: StorageClass/PVC-driven (portable to AKS and other managed clusters)
bash scripts/install.sh --profile full --storage-mode dynamic-pvc --node-ip <NODE_IP>

# legacy single-node host directory prep (when you explicitly want hostPath semantics)
bash scripts/install.sh --profile full --storage-mode legacy-hostpath --node-ip <NODE_IP>

# optional: inject one storage class for all stack PVCs at deploy time
bash scripts/install.sh --profile full --storage-mode dynamic-pvc --storage-class <STORAGE_CLASS_NAME> --node-ip <NODE_IP>

# optional repo-level helper (edits k8s/storage-pvc.yaml)
bash scripts/set-pvc-storage-class.sh <STORAGE_CLASS_NAME>
```

Namespace-isolated install (side-by-side environment):
```bash
bash scripts/install.sh --profile full --namespace media-stack-dev --ingress-domain dev.local --node-ip <NODE_IP>
```

Disaster-recovery style rebuild + verification:
```bash
bash scripts/rebuild-verify.sh <NODE_IP> [NAMESPACE] [PROFILE]
```

Examples:
```bash
bash scripts/rebuild-verify.sh 192.168.1.60 media-stack full
bash scripts/rebuild-verify.sh 192.168.1.60 media-stack-dev power-user
```

## Bootstrap Runner Image

Bootstrap Jobs/CronJobs run from a prebuilt image instead of mounting Python source via ConfigMap.

Build and push to local registry:
```bash
bash scripts/build-bootstrap-runner-image.sh
```

Override image for one run:
```bash
BOOTSTRAP_RUNNER_IMAGE=192.168.1.60:30002/library/media-stack-bootstrap-runner:latest \
  bash scripts/bootstrap-all.sh
```

The same `BOOTSTRAP_RUNNER_IMAGE` env var is respected by:
- `scripts/run-bootstrap-job.sh`
- `scripts/run-prowlarr-auto-indexers.sh`

## Runtime Config Overlays and Resume

Runtime config is now layered:
- `config/runtime/base.json`
- `config/runtime/overlays/dev.json`
- `config/runtime/overlays/stage.json`
- `config/runtime/overlays/prod.json`

Enable overlays in bootstrap config:
```json
{
  "config_overlays": {
    "enabled": true,
    "env": "prod"
  }
}
```

Checkpoint-resume is enabled by default for `bootstrap-all`:
```bash
# default checkpoint file: .state/bootstrap-all-<namespace>.json
bash scripts/bootstrap-all.sh

# force a full rerun
bash scripts/bootstrap-all.sh --no-resume

# explicit checkpoint path
bash scripts/bootstrap-all.sh --state-file .state/bootstrap-all-media-stack.json
```

Overlay details:
- [config/runtime/README.md](config/runtime/README.md)

## Quick Start (Docker Compose)

```bash
bash scripts/prepare-host.sh
cp docker/.env.example docker/.env
cd docker
docker compose up -d
```

## End-to-End Automation Scope

The bootstrap pipeline configures these OTB when enabled:
- Sonarr/Radarr/Lidarr/Readarr root folders and media-management defaults
- Arr completed download handling and failed-download recovery defaults
- Arr quality-upgrade lifecycle (prefer 1080p cutoff, block 2160p/4K tiers by default)
- Prowlarr app links and optional indexer auto-add
- qBittorrent categories + Arr download clients
- SABnzbd category paths + Arr remote path mappings
- Lidarr/Readarr curated auto-subscribe discovery lists (music/books)
- Jellyseerr to Jellyfin/Sonarr/Radarr mappings
- Jellyfin libraries, Live TV tuners/guides, plugins, playback defaults, and curated rails
- Jellyfin prewarm pipeline (scheduled metadata/artwork + guide/channel refresh)
- Homepage service cards and device onboarding links
- Disk usage guardrails with qB cleanup policy (`disk_guardrails`, default max 65% used on `/srv-stack`)
- Global media hygiene (failed-queue cleanup + temp/zero-byte/orphan cleanup + dedupe pass + qB IP filter refresh/cache)
- Maintainerr policy-as-code artifact generation (mounted at `/srv-config/maintainerr/policy.json` in bootstrap jobs)

## Service URLs

Use your ingress domain suffix (default `.local`):
- `homepage.<domain>`
- `jellyfin.<domain>`
- `jellyseerr.<domain>`
- `sonarr.<domain>`
- `radarr.<domain>`
- `lidarr.<domain>`
- `readarr.<domain>`
- `bazarr.<domain>`
- `prowlarr.<domain>`
- `qbittorrent.<domain>`
- `sabnzbd.<domain>`
- `tautulli.<domain>`

Render host entries:
```bash
bash scripts/render-hosts-example.sh <NODE_IP> <NAMESPACE>
```

Render router DNS snippets:
```bash
bash scripts/render-dnsmasq-snippet.sh <NODE_IP> <NAMESPACE>
```

TV/mobile onboarding guidance:
- [docs/device-onboarding.md](docs/device-onboarding.md)

## Premium UX and Metadata Quality

If books/music/live TV rows appear flat or artwork is missing, focus on these areas:
- Ensure real content is imported (images do not populate without indexed/imported media)
- Keep naming hygiene for media files and folders
- Keep Jellyfin metadata/artwork tuning enabled in `bootstrap/media-stack.bootstrap.json`
- Keep Jellyfin plugin and home-rails reconciliation enabled
- Run bootstrap reconcile after metadata/provider changes
- Curated rails now include Movies + TV + Music + Books defaults
- Live TV default now includes XMLTV guide wiring plus refresh-on-bootstrap (`jellyfin_livetv.refresh_on_bootstrap`) to improve Guide/Now views

Reconcile now:
```bash
bash scripts/bootstrap-all.sh
bash scripts/verify-flow.sh <NAMESPACE>
```

For retention and grooming policy:
- Acquisition/import is handled by Arr.
- Downloader cleanup/guardrails are configured in this stack.
- Deep library lifecycle pruning is best handled with a dedicated groomer policy tool (for example Maintainerr).

Deep guidance:
- [docs/premium-ux.md](docs/premium-ux.md)
- [docs/troubleshooting.md](docs/troubleshooting.md)

## Repo Layout

Current layout (with platform-oriented structure overlays):
- `k8s/`: deployable Kubernetes manifests and profiles (runtime source)
- `bootstrap/`: declarative app bootstrap configuration
- `scripts/`: install/reconcile/verify tooling (`*.sh` operator entrypoints + Python implementations under `scripts/cli/` and `scripts/bootstrap_services/`)
- `tests/`: unit + Playwright e2e smoke checks
- `docs/`: architecture/operations/design docs and diagrams
- `platform/`, `apps/`, `config/`, `examples/`: productized structure scaffolding and guidance

Layout details: [docs/repo-layout.md](docs/repo-layout.md)

## Operational Principles

- Idempotent automation over manual UI steps
- Secure-by-default secret handling with deterministic regeneration
- Drift reconciliation through bootstrap job + reconcile cron
- Observable install/bootstraps with phase logs and diagnostics
- Namespace-first environment isolation

See [docs/operational-principles.md](docs/operational-principles.md).

## GitOps-Friendly Workflow

1. Edit declarative config/manifests in Git.
2. Validate with tests.
3. Apply with profile-driven install/rebuild scripts.
4. Verify state with smoke and flow checks.
5. Promote to another namespace/environment.

See [docs/gitops.md](docs/gitops.md).

## Testing and Verification

Run local test suite:
```bash
bash scripts/test.sh
```

Schema-only config validation:
```bash
bash scripts/validate-bootstrap-config.sh
```

Run Playwright ingress smoke:
```bash
RUN_PLAYWRIGHT=1 STACK_NODE_IP=<NODE_IP> bash scripts/test.sh
# or
bash scripts/run-playwright-smoke.sh <NODE_IP> [NAMESPACE]
```

Run API-level relationship verification:
```bash
RUN_API_E2E=1 NAMESPACE=<NAMESPACE> bash scripts/test.sh
# or
python3 tests/e2e/api/verify_api_relationships.py --namespace <NAMESPACE>
bash scripts/run-api-e2e.sh <NAMESPACE>
```

Runtime flow verification:
```bash
bash scripts/verify-flow.sh [NAMESPACE]
bash scripts/microk8s-smoke-test.sh <NODE_IP> [NAMESPACE]
```

## Operations

Backups:
```bash
bash scripts/backup-stack.sh
```

Restore:
```bash
bash scripts/restore-stack.sh ./backups/media-stack-backup-YYYYMMDD-HHMMSS.tar.gz
```

Stack status and diagnostics:
```bash
bash scripts/stack-status.sh
bash scripts/bootstrap-debug.sh
```

## Documentation Map

- [docs/why-this-exists.md](docs/why-this-exists.md)
- [docs/architecture.md](docs/architecture.md)
- [docs/diagrams/README.md](docs/diagrams/README.md)
- [docs/deployment-model.md](docs/deployment-model.md)
- [docs/source-of-truth.md](docs/source-of-truth.md)
- [docs/software-design-models.md](docs/software-design-models.md)
- [docs/repo-layout.md](docs/repo-layout.md)
- [docs/operational-principles.md](docs/operational-principles.md)
- [docs/gitops.md](docs/gitops.md)
- [docs/networking.md](docs/networking.md)
- [docs/storage.md](docs/storage.md)
- [docs/operations.md](docs/operations.md)
- [docs/troubleshooting.md](docs/troubleshooting.md)
- [docs/premium-ux.md](docs/premium-ux.md)
- [docs/device-onboarding.md](docs/device-onboarding.md)
- [docs/screenshots/README.md](docs/screenshots/README.md)
- [docs/k8s-guide.md](docs/k8s-guide.md)
- [docs/service-guide.md](docs/service-guide.md)
- [docs/first-run-wiring.md](docs/first-run-wiring.md)

## License

Licensed under Apache License 2.0. See [LICENSE](LICENSE).
