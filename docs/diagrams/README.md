# Diagram Pack

This folder contains multiple architecture views.

## Refresh status

Refreshed 2026-05-12 against ADR-0005 (lifecycle-shaped promises),
ADR-0006 (per-service registries), ADR-0007 (lifecycle dispatch),
and ADR-0015 (commands → workflows boundary, all phases through
7m landed). Canonical source for current dispatch semantics:
the ADR set under `docs/architecture/adr/` and `KNOWN_ACTIONS`
in `src/media_stack/api/services/known_actions.py`.

## Backend / Platform Views

- `logical-topology.*`: service topology and ingress fan-out
- `network-protocol-topology.*`: protocol-level network paths across clients, ingress, services, pods, storage, and external providers; includes zone/subnet examples and scaling notes
- `media-data-pipeline.*`: request/download/import/playback data path
- `bootstrap-sequence.*`: bootstrap action dispatch through the job-graph engine (post-ADR-0007)
- `deployment-model.*`: k8s + compose deployment paths (single `media-stack` namespace; Envoy + Authelia gateway)
- `source-of-truth-flow.*`: desired-state and drift-reconcile loop
- `operating-loop.*`: change-test-promote operational lifecycle
- `software-component-model.*`: composition root, contracts, CLI layer boundary (commands → workflows), adapters, runtime
- `technology-adapter-model.*`: manifest-driven binding and adapter/service/operation resolution contracts
- `bootstrap-runtime-model.*`: controller state machine — `RunAction` is the live nested state (core action → job-graph or KNOWN_ACTIONS job → lifecycle dispatch)

## UI Surface View

- `ui-surface-map.*`: homepage/request/playback/operator surfaces and handoffs

## Regeneration

```bash
bash bin/render-architecture-diagrams.sh
```

Render tuning is configurable via env vars:
- `MMDC_WIDTH` (default `2200`)
- `MMDC_HEIGHT` (default `1400`)
- `MMDC_SCALE` (default `2`)
- `MERMAID_CONFIG_FILE` (default `docs/diagrams/mermaid-render-config.json`, disables HTML labels for SVG viewer compatibility)

Recommended high-legibility render (used for repo snapshots):

```bash
MMDC_WIDTH=3200 MMDC_HEIGHT=2400 MMDC_SCALE=2 bash bin/render-architecture-diagrams.sh
```

Rendering notes:
- SVG output is generated with non-HTML labels to maximize browser compatibility.
- PNG output is preferred for inline markdown readability in GitHub/doc viewers.
- Keep both formats checked in so local zoomable SVG and markdown-friendly PNG remain available.

---

**Project Steward**
Matthew Loschiavo • [matthewloschiavo.com](https://matthewloschiavo.com) • [mploschiavo@gmail.com](mailto:mploschiavo@gmail.com) • [LinkedIn](https://www.linkedin.com/in/matthewloschiavo)
