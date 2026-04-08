# Architecture

This platform is organized as a control plane plus a data plane.

- **Control plane**: deployment scripts, persistent controller HTTP API service, reconcile logic, and verification tooling.
- **Data plane**: downloader clients, Arr import pipeline, media libraries, and playback services.

## Controller HTTP API Service

The controller is a persistent Deployment (not a one-shot Job) exposing an HTTP API on port 9100.
It serves as the operational control surface for both Kubernetes and Docker Compose.

Key endpoints:
- `POST /actions/{name}` — trigger actions: `bootstrap`, `auto-indexers`, `restart-apps`, `sync-indexers`, `envoy-config`, `reconcile`
- `GET /status` — full state with phases, preflights, app status, action history
- `GET /config` / `POST /config` — runtime toggles (e.g. `auto_download_content`)
- `GET /logs/stream` — Server-Sent Events (SSE) real-time log stream
- `POST /webhooks` — register webhook URLs for action completion/error notifications
- `POST /reload` — hot-reload bootstrap profile YAML
- `GET /healthz` / `GET /readyz` — Kubernetes liveness/readiness probes
- `GET /` — interactive HTML dashboard

Features:
- **Action-level retry** with exponential backoff (`{"retry": 2}` in POST body or `BOOTSTRAP_ACTION_MAX_RETRIES` env)
- **Parallel phase execution** — phases with `"parallel": true` in config run steps concurrently
- **Parallel download client preparation** — qBittorrent and SABnzbd configure concurrently
- **Parallel auto-indexer testing** — configurable via `AUTO_INDEXER_PARALLEL_WORKERS` (default 4)
- **Webhook notifications** on action complete/error
- **Runtime config toggles** persist across actions and merge into every action as defaults

## Plugin Isolation Architecture

The platform enforces strict isolation between platform code and service-specific code.
A third-party developer can implement any service by editing only two locations:

1. `contracts/services/{service}.yaml` -- service metadata, API key format, health path, etc.
2. `src/media_stack/services/apps/{service}/` -- all implementation code

**Zero platform code changes are required.** The `services/apps/` directory is designed to be extractable into a separate git repo.

### Enforcement

`tests/unit/test_no_hardcoded_services.py` scans all platform Python files for hardcoded service names.
**Current state: 0 allowlist entries.** Any new violation fails CI.

### Layers

1. **Service registry layer**
   `contracts/services/*.yaml` -- per-service YAML contracts declare metadata, API key format, health endpoints, version paths, etc. The registry loads at import time.
2. **YAML defaults layer**
   `contracts/defaults/*.yaml` -- operational defaults (arr settings, download client config, disk guardrails).
3. **App/technology implementation layer**
   `src/media_stack/services/apps/{app}/` -- all service-specific code: adapters, config resolvers, runtime ops, preflight handlers, CLI tools.
4. **Shared orchestration layer**
   `cli/commands/`, `services/runtime_factory/`, `api/` -- technology-neutral orchestration, routing, and API handlers.

### Contract Rules

- Registration is YAML-contract-first -- the service registry is the single source of truth.
- `adapter_hooks` in profile YAML is runtime-only for phase plans, scale-policy, and operation handlers.
- Shared operation contracts are generic (`torrent_client_login`, `setup_torrent_categories`).
- Platform code uses registry lookups by category (e.g. `category="torrent"`) not by service name.

## Diagram Catalog

Rendered diagram artifacts live in `docs/diagrams`.

Core diagrams:
- `logical-topology.*`
- `network-protocol-topology.*`
- `media-data-pipeline.*`
- `bootstrap-sequence.*`

Product/operations diagrams:
- `deployment-model.*`
- `source-of-truth-flow.*`
- `operating-loop.*`
- `ui-surface-map.*`

Software design model diagrams:
- `software-component-model.*`
- `technology-adapter-model.*`
- `bootstrap-runtime-model.*`

Regenerate all diagrams:
```bash
bash bin/render-architecture-diagrams.sh
```

## Logical Topology

![Logical topology](diagrams/logical-topology.png)

```mermaid
flowchart LR
  subgraph Clients[Client Surface]
    TV[TV Apps]
    WEB[Web and Mobile]
  end

  subgraph Edge[Ingress Edge]
    ING[Ingress Controller]
  end

  subgraph Stack[Media Namespace]
    HOME[Homepage]
    JF[Jellyfin]
    JS[Jellyseerr]
    PR[Prowlarr]
    ARR[Sonarr/Radarr/Lidarr/Readarr]
    BAZ[Bazarr]
    QB[qBittorrent]
    SAB[SABnzbd]
    MTR[Maintainerr]
  end

  TV --> ING
  WEB --> ING

  ING --> HOME
  ING --> JF
  ING --> JS
  ING --> PR
  ING --> ARR
  ING --> BAZ
  ING --> QB
  ING --> SAB
  ING --> MTR

  JS --> ARR
  ARR --> PR
  ARR --> QB
  ARR --> SAB
  BAZ --> ARR
```

## Network And Protocol Topology

- See [`docs/diagrams/network-protocol-topology.svg`](diagrams/network-protocol-topology.svg)
- Includes client-to-ingress routing, service/pod boundaries, protocol labels, and PVC data paths.

![Network and protocol topology](diagrams/network-protocol-topology.png)

## Request-to-Playback Data Path

```mermaid
flowchart LR
  USER[User Request in Jellyseerr] --> ARR[Arr App]
  ARR --> IDX[Prowlarr Indexers]
  ARR --> DL[qBittorrent or SABnzbd]
  DL --> TEMP[/data downloads/]
  ARR --> IMPORT[Import and Organize]
  IMPORT --> LIB[/media libraries/]
  LIB --> JF[Jellyfin Playback]
```

## Control Path

```mermaid
flowchart TD
  GIT[Git Config + Plugin Manifests] --> INSTALL[Install and Rebuild Scripts]
  INSTALL --> K8S[Kubernetes Resources]
  INSTALL --> BOOT[Controller Service API]
  BOOT --> ACTIONS[On-Demand Actions via HTTP]
  ACTIONS --> APPS[Manifest-Bound Runtime Operations]
  APPS --> VERIFY[verify-flow and Playwright tests]
  VERIFY --> GIT
```

## Software Design Models

Detailed model guide:
- [docs/software-design-models.md](software-design-models.md)

Key rendered artifacts:
- [Software component model](diagrams/software-component-model.svg)
- [Technology adapter model](diagrams/technology-adapter-model.svg)
- [Bootstrap runtime model](diagrams/bootstrap-runtime-model.svg)

![Software component model](diagrams/software-component-model.png)

![Technology adapter model](diagrams/technology-adapter-model.png)

![Bootstrap runtime model](diagrams/bootstrap-runtime-model.png)

## Architectural Guarantees

- Rerunning deployment and bootstrap is expected and supported.
- Downloader/import path conventions are explicit and codified.
- Namespace-scoped deployments allow side-by-side validation.
- Drift is reduced through periodic reconcile and explicit verification.
- Technology replacement is role-local and binding-driven.
- Removing one technology manifest does not break unrelated technologies when that role is rebound.

---

**Project Steward**
Matthew Loschiavo • [matthewloschiavo.com](https://matthewloschiavo.com) • [mploschiavo@gmail.com](mailto:mploschiavo@gmail.com) • [LinkedIn](https://www.linkedin.com/in/matthewloschiavo)
