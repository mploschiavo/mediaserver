# Diagram Pack

This folder contains multiple architecture views.

## Refresh status

The diagram pack is in the middle of a refresh against ADR-0005
(lifecycle-shaped promises), ADR-0006 (per-service registries),
ADR-0007 (lifecycle dispatch), and ADR-0015 (boot-prep extraction).
Diagrams marked **current** have been re-validated; diagrams marked
**legacy** were last touched April 2026 and predate the cleanup —
they still convey the right shape of the system but name a few
classes/actions by their pre-ADR identifiers. The canonical source
for current dispatch semantics is the ADR set under
`docs/architecture/adr/`.

## Backend / Platform Views

- `logical-topology.*` **(current)**: service topology and ingress fan-out
- `network-protocol-topology.*` **(current)**: protocol-level network paths across clients, ingress, services, pods, storage, and external providers; includes zone/subnet examples and scaling notes
- `media-data-pipeline.*` **(current)**: request/download/import/playback data path
- `bootstrap-sequence.*` **(legacy)**: bootstrap job sequence — high-level shape still accurate; phase names predate ADR-0007
- `deployment-model.*` **(current)**: environment promotion and namespace deployment model
- `source-of-truth-flow.*` **(current)**: desired-state and drift-reconcile loop
- `operating-loop.*` **(current)**: change-test-promote operational lifecycle
- `software-component-model.*` **(current)**: composition root, manifest registry, adapters, app services, runtime boundaries
- `technology-adapter-model.*` **(legacy)**: manifest-driven binding and adapter/service/operation resolution contracts — most identifiers still resolve; partial refresh needed against ADR-0006/0007
- `bootstrap-runtime-model.*` **(legacy)**: bootstrap execution states — action set has moved to the job-graph model (ADR-0007); use `KNOWN_ACTIONS` in `src/media_stack/api/services/known_actions.py` as the source of truth

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
