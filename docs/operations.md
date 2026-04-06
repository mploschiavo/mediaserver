# Operations Runbook

![Operating loop](diagrams/operating-loop.png)

## Day 0: Install

```bash
bash bin/install.sh --profile full --node-ip <NODE_IP>
bash bin/install.sh --profile full --storage-mode dynamic-pvc --node-ip <NODE_IP>
```

Namespace-isolated environment:
```bash
bash bin/install.sh --profile full --namespace media-stack-dev --ingress-domain dev.local --node-ip <NODE_IP>
bash bin/install.sh --profile full --namespace media-stack-dev --storage-mode dynamic-pvc --ingress-domain dev.local --node-ip <NODE_IP>
```

## Day 0/1: Rebuild Drill

Use this regularly to prove recoverability:
```bash
bash bin/deploy-verify.sh <NODE_IP> [NAMESPACE] [PROFILE]
```

## Secrets Lifecycle

Generate or rotate:
```bash
bash bin/generate-secrets.sh
ROTATE_EXISTING=1 bash bin/generate-secrets.sh
```

Credential reconcile helpers:
```bash
bash bin/ensure-qbit-credentials.sh
bash bin/set-qbit-secret.sh <USERNAME> <PASSWORD>
```

## Bootstrap and Reconcile

The controller is a persistent HTTP API service on both platforms.

### Bootstrap API (port 9100)

Trigger actions via HTTP:
```bash
# Full bootstrap pipeline
curl -X POST http://localhost:9100/actions/bootstrap

# With runtime overrides
curl -X POST http://localhost:9100/actions/bootstrap \
  -H "Content-Type: application/json" \
  -d '{"auto_download_content": true, "retry": 2}'

# Individual actions
curl -X POST http://localhost:9100/actions/auto-indexers
curl -X POST http://localhost:9100/actions/envoy-config
curl -X POST http://localhost:9100/actions/restart-apps
curl -X POST http://localhost:9100/actions/sync-indexers
curl -X POST http://localhost:9100/actions/reconcile

# Runtime config toggles (persist across actions)
curl -X POST http://localhost:9100/config \
  -H "Content-Type: application/json" \
  -d '{"auto_download_content": true}'

# Hot-reload profile YAML
curl -X POST http://localhost:9100/reload

# Check status
curl http://localhost:9100/status

# Stream logs (SSE)
curl http://localhost:9100/logs/stream

# Register webhook
curl -X POST http://localhost:9100/webhooks \
  -H "Content-Type: application/json" \
  -d '{"url": "http://example.com/hook"}'

# Interactive dashboard
open http://localhost:9100/
```

### Script-based bootstrap (alternative)

```bash
bash bin/bootstrap-all.sh
bash bin/run-bootstrap-job.sh
bash bin/verify-flow.sh [NAMESPACE]
```

![Bootstrap runtime model](diagrams/bootstrap-runtime-model.png)

Checkpoint/resume controls:
```bash
# default resume enabled
bash bin/bootstrap-all.sh

# disable resume and force full phase rerun
bash bin/bootstrap-all.sh --no-resume

# custom checkpoint state file
bash bin/bootstrap-all.sh --state-file .state/bootstrap-all-media-stack.json
```

Runtime overlays:
- base + env overlays live under `config/runtime/`.
- enable with `config_overlays.enabled=true` in your bootstrap config.
- select env with `config_overlays.env` (`dev`, `stage`, `prod`).

Optional periodic reconcile is available through Kubernetes CronJob manifests.
Default scheduled jobs in `full` profile:
- `media-stack-controller-reconcile`: full idempotent reconcile loop
- `media-stack-jellyfin-prewarm`: metadata/artwork + guide/channel prewarm refresh
- `media-stack-media-hygiene`: failed queue cleanup + filesystem hygiene pass + qB IP filter reconcile

qB IP filter defaults are config-as-code under `media_hygiene.qbit_ipfilter` in
`contracts/media-stack.config.json`:
- Source URL: `https://github.com/DavidMoore/ipfilter/releases/download/lists/ipfilter.dat`
- Refresh cadence: minimum once per 24h (even though hygiene job runs more often)
- Storage targets: primary PVC path plus host-path mirror for mixed storage-mode compatibility
- Failure behavior: if source is unavailable, keep and re-apply cached filter file instead of failing

Disk guardrails defaults are configured in `contracts/media-stack.config.json` under `disk_guardrails` (default max 65% used, target 58%, qB cleanup policy when over threshold, monitor path `/srv-stack/media`).
Maintainerr is deployed as an optional app (`maintainerr.<domain>`) with persistent config at `/opt/data`.
Maintainerr policy-as-code is also rendered to `/srv-config/maintainerr/policy.json` from the `maintainerr` section in bootstrap config.
Rule definitions are managed as one-file-per-rule JSON/YAML under `src/media_stack/contracts/maintainerr_rules/{json,yaml}/`
with optional namespace-local overrides from `maintainerr.rules_library.relative_path`.

qB queue and category-budget guardrails are configured under
`download_clients.qbittorrent.queue_guardrails`:
- `max_queued_by_category`: hard cap on queued/downloading items per category
- `max_total_size_gib_by_category`: optional size cap per category (GiB)
- `max_weight_percent_by_category`: optional weighted-share cap per category (% of managed qB payload)
- `budget_prune_states`: which torrent states are eligible when reducing category budget

## Validation and Tests

```bash
bash bin/test.sh
RUN_PLAYWRIGHT=1 STACK_NODE_IP=<NODE_IP> bash bin/test.sh
RUN_API_E2E=1 NAMESPACE=<NAMESPACE> bash bin/test.sh
bash bin/run-api-e2e.sh <NAMESPACE>
bash bin/microk8s-smoke-test.sh <NODE_IP> [NAMESPACE]
bash bin/validate-bootstrap-config.sh
bash bin/run-playwright-screenshots.sh <NODE_IP> [NAMESPACE]
bash bin/capture-k8s-snapshots.sh [NAMESPACE]
```

## Backup and Restore

```bash
bash bin/backup-stack.sh
bash bin/restore-stack.sh ./backups/media-stack-backup-YYYYMMDD-HHMMSS.tar.gz
```

## Observability

```bash
bash bin/stack-status.sh
MEDIA_STACK_LOG_LEVEL=DEBUG bash bin/bootstrap-all.sh --no-resume
bash bin/watch-install.sh
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

---

**Project Steward**
Matthew Loschiavo • [matthewloschiavo.com](https://matthewloschiavo.com) • [mploschiavo@gmail.com](mailto:mploschiavo@gmail.com) • [LinkedIn](https://www.linkedin.com/in/matthewloschiavo)
