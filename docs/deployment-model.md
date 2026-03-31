# Deployment Model

## Core Model

The stack is deployed into a Kubernetes namespace and converged by a bootstrap pipeline.

![Deployment model](diagrams/deployment-model.png)

Flow:
1. Apply profile manifests.
2. Generate or reconcile secrets.
3. Wait for workloads.
4. Run bootstrap job for cross-app wiring.
5. Verify ingress and service integration.
6. Keep drift low with periodic reconcile.

## Profiles

- `minimal`: essential media request/playback path.
- `full`: core + optional components + reconcile helpers.
- `public-demo`: demo-safe defaults and reduced downloader automation.
- `power-user`: full with additional operational guardrails.

Profile manifests live in `k8s/profiles/*`.

## Storage Modes

- `dynamic-pvc` (default): StorageClass/PVC-driven and portable across clusters.
- `legacy-hostpath`: keeps node-local host directory prep flow for single-node setups.

Example:
```bash
bash scripts/install.sh --profile full --storage-mode dynamic-pvc --node-ip <NODE_IP>
```

## Namespace Strategy

Use namespace isolation for environment promotion and safe experimentation.

Example:
```bash
bash scripts/install.sh --profile full --namespace media-stack-dev --ingress-domain dev.local --node-ip <NODE_IP>
bash scripts/install.sh --profile full --namespace media-stack-prod --ingress-domain prod.local --node-ip <NODE_IP>
```

## Rebuild-First Operations

The expected operating posture is rebuild-ready:
- PVC manifests are applied idempotently
- manifests are re-applied safely
- bootstrap wiring is re-runnable
- verification scripts validate outcomes

One command for full rebuild + verify:
```bash
bash scripts/rebuild-verify.sh <NODE_IP> [NAMESPACE] [PROFILE]
```

## Runtime Reconciliation

- Bootstrap job config is supplied via ConfigMap from `bootstrap/media-stack.bootstrap.json`.
- Optional reconcile CronJob can periodically re-apply desired application wiring.
- Drift introduced in web UIs is intentionally overwritten by declarative configuration.

## Multi-Node / Remote Operator Note

Default mode is StorageClass/PVC-driven, so remote operators can apply manifests from any machine with cluster access.
If you intentionally run `--storage-mode legacy-hostpath`, run host prep helpers on the target node hosting `/srv/media-stack`.

---

**Project Steward**
Matthew Loschiavo • [matthewloschiavo.com](https://matthewloschiavo.com) • [mploschiavo@gmail.com](mailto:mploschiavo@gmail.com) • [LinkedIn](https://www.linkedin.com/in/matthewloschiavo)
