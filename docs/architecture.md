# Architecture

This platform is organized as a control plane plus a data plane.

- **Control plane**: deployment scripts, bootstrap job, reconcile logic, and verification tooling.
- **Data plane**: downloader clients, Arr import pipeline, media libraries, and playback services.

## Pluggable Layers and Contracts

The runtime is intentionally layered so technologies can be swapped locally without editing shared orchestration:

1. **Declarative bindings layer**
   `bootstrap/media-stack.bootstrap.json` selects active technologies per role through `technology_bindings`.
2. **Manifest registration layer**
   `scripts/bootstrap_defaults/plugins/<technology>/manifest.json` declares adapter classes, service classes, operation handlers, and aliases.
3. **App/technology implementation layer**
   `scripts/bootstrap_services/apps/<app>/`, `download_client_adapters/`, `media_server_adapters/`, and `servarr_technologies/`.
4. **Shared orchestration layer**
   `bootstrap-apps.py`, `runtime_factory/*`, and `bootstrap_runner_service.py` stay technology-neutral.

Contract rules:
- Registration is manifest-first, not runtime-config overrides.
- `adapter_hooks` is runtime-only for operation handlers, phase plans, wrapper phase-script maps, and scale-policy/worker orchestration lists.
- Shared operation contracts are generic (`torrent_client_login`, `setup_torrent_categories`).
- `BootstrapRunnerService` remains orchestration-only; app-specific branching belongs in app/adapter modules or declarative plans.

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
bash scripts/render-architecture-diagrams.sh
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
  INSTALL --> BOOT[Bootstrap Job]
  BOOT --> APPS[Manifest-Bound Runtime Operations]
  APPS --> VERIFY[verify-flow and smoke tests]
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
