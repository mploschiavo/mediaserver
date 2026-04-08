# Repo Layout

The repository keeps deployable runtime assets in existing stable paths while introducing a product-oriented top-level structure for long-term maintainability.

## Current Runtime-Critical Paths

- `k8s/`: Kubernetes manifests, profile overlays, bootstrap job manifests
- `docker/`: Docker Compose runtime manifests and env templates
- `contracts/`: per-service YAML (`services/*.yaml`), profile YAML, adapter-hooks config (`adapter-hooks.k8s.yaml`), and catalog YAML
- `bin/`: install, reconcile, diagnostics, and verification tooling
- `tests/`: unit and e2e test suites
- `docs/`: architecture, operations, and design documents

### Plugin Architecture (service-specific code)

All service-specific code lives in `src/media_stack/services/apps/`:

```
services/apps/
  bazarr/           # subtitle automation
  download_clients/ # shared download client helpers, registry_helpers, runtime_compat
  flaresolverr/     # indexer helper
  homepage/         # dashboard constants, config
  integrations/     # cross-service config resolvers
  jellyfin/         # media server: gpu, api_key_db, config_resolver, home_rails, libraries
  jellyseerr/       # request management
  maintainerr/      # retention policy
  openseerr/        # alternative request management
  plex/             # alternative media server
  prowlarr/         # indexer manager: api_key_reader, runtime_compat
  qbittorrent/      # torrent client adapter
  readarr/          # books automation
  sabnzbd/          # usenet client adapter
  servarr/          # shared arr app framework: pipeline, technologies, traits
  sonarr/           # TV automation (sonarr_seed)
  stack/            # stack-level: routing_defaults, config_diagnostics, config_policy
  tautulli/         # analytics
  unpackerr/        # post-download extraction
```

Service contracts in `contracts/services/*.yaml` declare metadata (host, port, API key format, health paths). The registry at `src/media_stack/api/services/registry.py` loads these at import time. **Zero platform code changes needed for new services.**

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
- Technology registration and role bindings: `contracts/services/*.yaml`, `contracts/defaults/*.yaml`
- Shared runtime lifecycle orchestration: `bin/controller.py`, `src/media_stack/services/runtime_factory/*`, `src/media_stack/cli/commands/`
- App/technology behavior modules: `src/media_stack/services/apps/*` (fully isolated, extractable)
- Quality gates and regressions: `tests/`
- Product narrative and operator docs: `docs/`

## Why This Layout

- Keeps existing automation stable.
- Makes migration to stronger GitOps patterns straightforward.
- Improves onboarding for contributors by separating runtime assets from design/docs concerns.

---

**Project Steward**
Matthew Loschiavo • [matthewloschiavo.com](https://matthewloschiavo.com) • [mploschiavo@gmail.com](mailto:mploschiavo@gmail.com) • [LinkedIn](https://www.linkedin.com/in/matthewloschiavo)
