# Diagram Pack

This folder contains multiple architecture views.

## Backend / Platform Views

- `logical-topology.*`: service topology and ingress fan-out
- `network-protocol-topology.*`: protocol-level network paths across clients, ingress, services, pods, storage, and external providers; includes zone/subnet examples and scaling notes
- `media-data-pipeline.*`: request/download/import/playback data path
- `bootstrap-sequence.*`: bootstrap job sequence
- `deployment-model.*`: environment promotion and namespace deployment model
- `source-of-truth-flow.*`: desired-state and drift-reconcile loop
- `operating-loop.*`: change-test-promote operational lifecycle
- `software-component-model.*`: composition-root, services, adapters, runtime boundaries
- `technology-adapter-model.*`: config-driven binding and adapter resolution model
- `bootstrap-runtime-model.*`: bootstrap execution states, retry, and failure transitions

## UI Surface View

- `ui-surface-map.*`: homepage/request/playback/operator surfaces and handoffs

## Regeneration

```bash
bash scripts/render-architecture-diagrams.sh
```

Render tuning is configurable via env vars:
- `MMDC_WIDTH` (default `2200`)
- `MMDC_HEIGHT` (default `1400`)
- `MMDC_SCALE` (default `2`)
- `MERMAID_CONFIG_FILE` (default `docs/diagrams/mermaid-render-config.json`, disables HTML labels for SVG viewer compatibility)
