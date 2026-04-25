# Media Stack — Documentation

Pick the doc that matches what you're trying to do. If you're new, [Quickstart](quickstart.md) is the right starting point on every platform.

## Decision tree

- **Want to deploy and use the stack?** → [Quickstart](quickstart.md), then [Deployment](deployment.md).
- **Want to maintain it day-to-day?** → [Operations](operations.md), [Troubleshooting](troubleshooting.md), [Upgrades](upgrades.md).
- **Want to understand or extend the code?** → [internals/principles.md](internals/principles.md), then [internals/architecture.md](internals/architecture.md).
- **Just looking something up?** → [reference/](reference/).
- **Reporting a bug?** → [CONTRIBUTING.md](../CONTRIBUTING.md).

## Top-level docs

Action-oriented. Each one is short — if you need depth, links into [`internals/`](internals/) are inline.

| Doc | Use when |
|---|---|
| [quickstart.md](quickstart.md) | Bringing the stack up the first time |
| [deployment.md](deployment.md) | Compose or Kubernetes install in detail |
| [auth.md](auth.md) | Setting up Authelia / Authentik / Google IdP |
| [networking.md](networking.md) | DNS, ingress, edge routing |
| [storage.md](storage.md) | PVCs, bind mounts, storage classes |
| [operations.md](operations.md) | Backup, restore, status, diagnostics |
| [security.md](security.md) | What ships hardened today (with the live baseline) |
| [upgrades.md](upgrades.md) | Routine upgrade flow + rollback |
| [troubleshooting.md](troubleshooting.md) | Things to try when something is broken |
| [teardown.md](teardown.md) | Wiping a stack safely |
| [connecting-devices.md](connecting-devices.md) | Smart TV / Roku / Apple TV / mobile setup |
| [user-management.md](user-management.md) | Creating users, roles, permissions |
| [intel-transcoding.md](intel-transcoding.md) | Hardware transcoding on Intel iGPUs |

## Reference

Look-up material — generated from code where possible.

| Doc | Source |
|---|---|
| [reference/configuration.md](reference/configuration.md) | All configurable knobs |
| [reference/service-catalog.md](reference/service-catalog.md) | Per-service metadata + endpoints |
| [reference/promises.md](reference/promises.md) | Generated from `.ratchets/promises/promises.yaml` (cross-platform OTB guarantees) |
| [reference/promises-k8s.md](reference/promises-k8s.md) | Generated from `contracts/promises-k8s.yaml` (K8s-only guarantees) |
| [reference/maintainerr-rules.md](reference/maintainerr-rules.md) | Maintainerr collection-rule schema |

## Internals

For people working on the stack. If you only want to *use* it, you can skip this directory.

| Doc | About |
|---|---|
| [internals/principles.md](internals/principles.md) | Why the project exists + the 11 operational principles |
| [internals/architecture.md](internals/architecture.md) | Control plane / data plane, plugin isolation, source-of-truth, design models |
| [internals/bootstrap-runtime.md](internals/bootstrap-runtime.md) | Bootstrap profile schema + execution flow |
| [internals/promises-registry.md](internals/promises-registry.md) | The promise system + meta-ratchet |
| [internals/adding-a-service.md](internals/adding-a-service.md) | How to add a new service |
| [internals/technology-swaps.md](internals/technology-swaps.md) | Swapping a technology binding (e.g. Plex → Jellyfin) |
| [internals/service-registry.md](internals/service-registry.md) | Service contract format |
| [internals/repo-layout.md](internals/repo-layout.md) | Where things live |
| [internals/indexer-pipeline.md](internals/indexer-pipeline.md) | How Prowlarr discovery / tagging / push-sync works |
| [internals/k8s-deploy-pipeline.md](internals/k8s-deploy-pipeline.md) | K8s deploy phase ordering |
| [internals/premium-ux.md](internals/premium-ux.md) | Metadata, artwork, rails tuning |
| [internals/gitops.md](internals/gitops.md) | GitOps promotion workflow |
| [internals/security-roadmap.md](internals/security-roadmap.md) | Open security work + planned checks |
| [internals/sdlc.md](internals/sdlc.md) | Branch strategy, CI pipeline, release flow |
| [internals/testing.md](internals/testing.md) | Unit, Playwright, API E2E, verification suites |
| [internals/supply-chain.md](internals/supply-chain.md) | Image signing, SBOM, dependency hygiene |

## Diagrams

Rendered architecture and topology diagrams live in [diagrams/](diagrams/). Regenerate them all:

```bash
bash bin/render-architecture-diagrams.sh
```

## Conventions used in these docs

- Snippets that touch live state include the exact command to verify the change worked.
- Examples use `media-stack` as the namespace / project name; substitute your own where appropriate.
- "OTB" = out-of-the-box. Anything labeled OTB is wired automatically by the bootstrap and re-asserted by reconcile.
- Anything aspirational lives in [internals/security-roadmap.md](internals/security-roadmap.md) or is explicitly marked "(forthcoming)" in this index.
