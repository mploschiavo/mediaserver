# Operations Runbook

## Day 0: Install

```bash
bash scripts/install.sh --profile full --node-ip <NODE_IP>
bash scripts/install.sh --profile full --storage-mode dynamic-pvc --node-ip <NODE_IP>
```

Namespace-isolated environment:
```bash
bash scripts/install.sh --profile full --namespace media-stack-dev --ingress-domain dev.local --node-ip <NODE_IP>
bash scripts/install.sh --profile full --namespace media-stack-dev --storage-mode dynamic-pvc --ingress-domain dev.local --node-ip <NODE_IP>
```

## Day 0/1: Rebuild Drill

Use this regularly to prove recoverability:
```bash
bash scripts/rebuild-verify.sh <NODE_IP> [NAMESPACE] [PROFILE]
```

## Secrets Lifecycle

Generate or rotate:
```bash
bash scripts/generate-secrets.sh
ROTATE_EXISTING=1 bash scripts/generate-secrets.sh
```

Credential reconcile helpers:
```bash
bash scripts/ensure-qbit-credentials.sh
bash scripts/set-qbit-secret.sh <USERNAME> <PASSWORD>
```

## Bootstrap and Reconcile

```bash
bash scripts/bootstrap-all.sh
bash scripts/run-bootstrap-job.sh
bash scripts/verify-flow.sh [NAMESPACE]
```

Checkpoint/resume controls:
```bash
# default resume enabled
bash scripts/bootstrap-all.sh

# disable resume and force full phase rerun
bash scripts/bootstrap-all.sh --no-resume

# custom checkpoint state file
bash scripts/bootstrap-all.sh --state-file .state/bootstrap-all-media-stack.json
```

Runtime overlays:
- base + env overlays live under `config/runtime/`.
- enable with `config_overlays.enabled=true` in your bootstrap config.
- select env with `config_overlays.env` (`dev`, `stage`, `prod`).

Optional periodic reconcile is available through Kubernetes CronJob manifests.
Default scheduled jobs in `full` profile:
- `media-stack-bootstrap-reconcile`: full idempotent reconcile loop
- `media-stack-jellyfin-prewarm`: metadata/artwork + guide/channel prewarm refresh
- `media-stack-media-hygiene`: failed queue cleanup + filesystem hygiene pass + qB IP filter reconcile

qB IP filter defaults are config-as-code under `media_hygiene.qbit_ipfilter` in
`bootstrap/media-stack.bootstrap.json`:
- Source URL: `https://github.com/DavidMoore/ipfilter/releases/download/lists/ipfilter.dat`
- Refresh cadence: minimum once per 24h (even though hygiene job runs more often)
- Storage targets: primary PVC path plus host-path mirror for mixed storage-mode compatibility
- Failure behavior: if source is unavailable, keep and re-apply cached filter file instead of failing

Disk guardrails defaults are configured in `bootstrap/media-stack.bootstrap.json` under `disk_guardrails` (default max 65% used, target 58%, qB cleanup policy when over threshold, monitor path `/srv-stack/media`).
Maintainerr is deployed as an optional app (`maintainerr.<domain>`) with persistent config at `/opt/data`.
Maintainerr policy-as-code is also rendered to `/srv-config/maintainerr/policy.json` from the `maintainerr` section in bootstrap config.
Rule definitions are managed as one-file-per-rule JSON under `scripts/bootstrap_defaults/maintainerr_rules/`
with optional namespace-local overrides from `maintainerr.rules_library.relative_path`.

qB queue and category-budget guardrails are configured under
`download_clients.qbittorrent.queue_guardrails`:
- `max_queued_by_category`: hard cap on queued/downloading items per category
- `max_total_size_gib_by_category`: optional size cap per category (GiB)
- `max_weight_percent_by_category`: optional weighted-share cap per category (% of managed qB payload)
- `budget_prune_states`: which torrent states are eligible when reducing category budget

## Validation and Tests

```bash
bash scripts/test.sh
RUN_PLAYWRIGHT=1 STACK_NODE_IP=<NODE_IP> bash scripts/test.sh
RUN_API_E2E=1 NAMESPACE=<NAMESPACE> bash scripts/test.sh
bash scripts/run-api-e2e.sh <NAMESPACE>
bash scripts/microk8s-smoke-test.sh <NODE_IP> [NAMESPACE]
bash scripts/validate-bootstrap-config.sh
```

## Backup and Restore

```bash
bash scripts/backup-stack.sh
bash scripts/restore-stack.sh ./backups/media-stack-backup-YYYYMMDD-HHMMSS.tar.gz
```

## Observability

```bash
bash scripts/stack-status.sh
bash scripts/bootstrap-debug.sh
bash scripts/watch-install.sh
```

## Namespace Hygiene

Clean up stale test namespaces:
```bash
kubectl get ns -o name | grep '^namespace/media-stack-' | grep -v '^namespace/media-stack$' | xargs -r kubectl delete --wait=false
```

## Related Docs

- [architecture.md](architecture.md)
- [deployment-model.md](deployment-model.md)
- [source-of-truth.md](source-of-truth.md)
- [networking.md](networking.md)
- [storage.md](storage.md)
- [maintainerr-rules-library.md](maintainerr-rules-library.md)
- [troubleshooting.md](troubleshooting.md)
