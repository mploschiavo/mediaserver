# Media Stack — Documentation

Pick the doc that matches what you're trying to do. If you're new, [Quickstart](tutorials/quickstart.md) is the right starting point on every platform.

## Decision tree

- **Want to deploy and use the stack?** → [Quickstart](tutorials/quickstart.md), then [Deployment](how-to/deployment.md).
- **Want to maintain it day-to-day?** → [Operations](how-to/operations.md), [Troubleshooting](how-to/troubleshooting.md), [Upgrades](how-to/upgrades.md).
- **Want to understand or extend the code?** → [architecture/principles.md](architecture/principles.md), then [architecture/overview.md](architecture/overview.md).
- **Just looking something up?** → [reference/](reference/).
- **Reporting a bug?** → [CONTRIBUTING.md](../CONTRIBUTING.md).

## Tutorials & how-to guides

Action-oriented. Each one is short — if you need depth, links into [`architecture/`](architecture/) are inline.

| Doc | Use when |
|---|---|
| [tutorials/quickstart.md](tutorials/quickstart.md) | Bringing the stack up the first time |
| [how-to/deployment.md](how-to/deployment.md) | Compose or Kubernetes install in detail |
| [how-to/auth.md](how-to/auth.md) | Setting up Authelia / Authentik / Google IdP |
| [how-to/networking.md](how-to/networking.md) | DNS, ingress, edge routing |
| [how-to/storage.md](how-to/storage.md) | PVCs, bind mounts, storage classes |
| [how-to/operations.md](how-to/operations.md) | Backup, restore, status, diagnostics |
| [how-to/security.md](how-to/security.md) | What ships hardened today (with the live baseline) |
| [how-to/upgrades.md](how-to/upgrades.md) | Routine upgrade flow + rollback |
| [how-to/troubleshooting.md](how-to/troubleshooting.md) | Things to try when something is broken |
| [how-to/teardown.md](how-to/teardown.md) | Wiping a stack safely |
| [how-to/connecting-devices.md](how-to/connecting-devices.md) | Smart TV / Roku / Apple TV / mobile setup |
| [how-to/user-management.md](how-to/user-management.md) | Creating users, roles, permissions |
| [how-to/intel-transcoding.md](how-to/intel-transcoding.md) | Hardware transcoding on Intel iGPUs |
| [how-to/media-integrity.md](how-to/media-integrity.md) | Library scrubs + hygiene checks |
| [how-to/deploy-parity.md](how-to/deploy-parity.md) | Compose ↔ K8s parity verification |
| [how-to/openapi-regen.md](how-to/openapi-regen.md) | Regenerating the OpenAPI spec |
| [how-to/ui-container.md](how-to/ui-container.md) | UI image + dashboard operations |

## Reference

Look-up material — generated from code where possible.

| Doc | Source |
|---|---|
| [reference/configuration.md](reference/configuration.md) | All configurable knobs |
| [reference/service-catalog.md](reference/service-catalog.md) | Per-service metadata + endpoints |
| [reference/services.md](reference/services.md) | Service-contract listing |
| [reference/promises.md](reference/promises.md) | Generated from `contracts/promises/promises.yaml` (every OTB guarantee — agnostic, compose-only, k8s-only) |
| [reference/maintainerr-rules.md](reference/maintainerr-rules.md) | Maintainerr collection-rule schema |
| [reference/security-a11y-contract.md](reference/security-a11y-contract.md) | UI security + accessibility contract |
| [reference/ui-design-system.md](reference/ui-design-system.md) | UI design tokens + component conventions |
| [reference/api/](reference/api/) | OpenAPI-derived endpoint reference |
| [reference/cli/](reference/cli/) | Console-script catalog |

## Architecture

For people working on the stack. If you only want to *use* it, you can skip this directory.

| Doc | About |
|---|---|
| [architecture/principles.md](architecture/principles.md) | Why the project exists + the operational principles |
| [architecture/overview.md](architecture/overview.md) | Control plane / data plane, plugin isolation, source-of-truth, design models |
| [architecture/bootstrap-runtime.md](architecture/bootstrap-runtime.md) | Bootstrap profile schema + execution flow |
| [architecture/promises-registry.md](architecture/promises-registry.md) | The promise system + meta-ratchet |
| [architecture/adding-a-service.md](architecture/adding-a-service.md) | How to add a new service |
| [architecture/technology-swaps.md](architecture/technology-swaps.md) | Swapping a technology binding (e.g. Plex → Jellyfin) |
| [architecture/service-registry.md](architecture/service-registry.md) | Service contract format |
| [architecture/repo-layout.md](architecture/repo-layout.md) | Where things live |
| [architecture/indexer-pipeline.md](architecture/indexer-pipeline.md) | How Prowlarr discovery / tagging / push-sync works |
| [architecture/k8s-deploy-pipeline.md](architecture/k8s-deploy-pipeline.md) | K8s deploy phase ordering |
| [architecture/premium-ux.md](architecture/premium-ux.md) | Metadata, artwork, rails tuning |
| [architecture/gitops.md](architecture/gitops.md) | GitOps promotion workflow |
| [architecture/security-roadmap.md](architecture/security-roadmap.md) | Open security work + planned checks |
| [architecture/sdlc.md](architecture/sdlc.md) | Branch strategy, CI pipeline, release flow |
| [architecture/testing.md](architecture/testing.md) | Unit, Playwright, API E2E, verification suites |
| [architecture/supply-chain.md](architecture/supply-chain.md) | Image signing, SBOM, dependency hygiene |
| [architecture/api-keys.md](architecture/api-keys.md) | API-key minting, discovery, persistence |
| [architecture/orchestrator-coverage-matrix.md](architecture/orchestrator-coverage-matrix.md) | What the orchestrator covers vs. legacy paths |
| [architecture/adr/](architecture/adr/) | Architecture Decision Records |

## Diagrams

Rendered architecture and topology diagrams live in [diagrams/](diagrams/). Regenerate them all:

```bash
media-stack-render-arch-diagrams
```

## Conventions used in these docs

- Snippets that touch live state include the exact command to verify the change worked.
- Examples use `media-stack` as the namespace / project name; substitute your own where appropriate.
- "OTB" = out-of-the-box. Anything labeled OTB is wired automatically by the bootstrap and re-asserted by reconcile.
- Anything aspirational lives in [architecture/security-roadmap.md](architecture/security-roadmap.md) or is explicitly marked "(forthcoming)" in this index.
