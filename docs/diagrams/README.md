# Diagram Pack

This folder contains multiple architecture views.

## Backend / Platform Views

- `logical-topology.*`: service topology and ingress fan-out
- `media-data-pipeline.*`: request/download/import/playback data path
- `bootstrap-sequence.*`: bootstrap job sequence
- `deployment-model.*`: environment promotion and namespace deployment model
- `source-of-truth-flow.*`: desired-state and drift-reconcile loop
- `operating-loop.*`: change-test-promote operational lifecycle

## UI Surface View

- `ui-surface-map.*`: homepage/request/playback/operator surfaces and handoffs

## Regeneration

```bash
bash scripts/render-architecture-diagrams.sh
```
