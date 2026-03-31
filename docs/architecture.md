# Architecture

This platform is organized as a control plane plus a data plane.

- **Control plane**: deployment scripts, bootstrap job, reconcile logic, and verification tooling.
- **Data plane**: downloader clients, Arr import pipeline, media libraries, and playback services.

## Diagram Catalog

Rendered diagram artifacts live in `docs/diagrams`.

Core diagrams:
- `logical-topology.*`
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

  JS --> ARR
  ARR --> PR
  ARR --> QB
  ARR --> SAB
  BAZ --> ARR
```

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
  GIT[Git Config] --> INSTALL[Install and Rebuild Scripts]
  INSTALL --> K8S[Kubernetes Resources]
  INSTALL --> BOOT[Bootstrap Job]
  BOOT --> APPS[Arr/Prowlarr/Jellyseerr/Jellyfin Settings]
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

## Architectural Guarantees

- Rerunning deployment and bootstrap is expected and supported.
- Downloader/import path conventions are explicit and codified.
- Namespace-scoped deployments allow side-by-side validation.
- Drift is reduced through periodic reconcile and explicit verification.
