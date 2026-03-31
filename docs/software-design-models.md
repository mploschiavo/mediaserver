# Software Design Models

This page captures the internal design models used by the bootstrap/runtime system after the refactor.

## 1) Software Component Model

- Diagram: [docs/diagrams/software-component-model.svg](diagrams/software-component-model.svg)
- Purpose:
  - Shows composition root vs. runtime services vs. adapter modules.
  - Clarifies where to add code for a new technology without touching global orchestration.

## 2) Technology Adapter Model

- Diagram: [docs/diagrams/technology-adapter-model.svg](diagrams/technology-adapter-model.svg)
- Purpose:
  - Shows how `technology_bindings` and `adapter_hooks` drive runtime resolution.
  - Documents the fallback chain: explicit hook -> convention discovery -> generic no-op.

## 3) Bootstrap Runtime Model

- Diagram: [docs/diagrams/bootstrap-runtime-model.svg](diagrams/bootstrap-runtime-model.svg)
- Purpose:
  - Captures lifecycle states of bootstrap execution.
  - Makes failure and retry behavior explicit for troubleshooting and test design.

## Design Intent

- Composition over inheritance for orchestration and side-effect boundaries.
- Per-technology adapters for swap isolation.
- Typed config and explicit operation plans as runtime contracts.
- Thin shell entrypoints + Python implementations under `scripts/cli/` and `scripts/bootstrap_services/`.

## Regenerate Diagrams

```bash
bash scripts/render-architecture-diagrams.sh
```
