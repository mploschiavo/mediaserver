# Kubernetes Guide

This bundle assumes:
- a local cluster
- an ingress class named `public` is available (MicroK8s default)
- PVC-backed storage via `k8s/storage-pvc.yaml`

## Apply
```bash
# installer wizard with profile selection
bash scripts/install.sh --profile full --node-ip <NODE_IP>
bash scripts/install.sh --profile full --storage-mode dynamic-pvc --node-ip <NODE_IP>
bash scripts/install.sh --profile full --storage-mode legacy-hostpath --node-ip <NODE_IP>

# deterministic rebuild + verification (recommended for DR confidence)
bash scripts/rebuild-verify.sh <NODE_IP> [NAMESPACE] [PROFILE]

# fully automatic rebuild + bootstrap + smoke test (recommended)
bash scripts/rebuild-and-bootstrap.sh <NODE_IP>
PROFILE=power-user bash scripts/rebuild-and-bootstrap.sh <NODE_IP>

# from repo root
kubectl apply -k k8s

# if already in k8s/
kubectl apply -k .

# core + optional + unpackerr manifests together
kubectl apply -k k8s/all

# profile manifests
kubectl apply -k k8s/profiles/minimal
kubectl apply -k k8s/profiles/full
kubectl apply -k k8s/profiles/public-demo
kubectl apply -k k8s/profiles/power-user
```
Profile notes:
- `public-demo` intentionally skips bootstrap in `rebuild-and-bootstrap.sh` and scales downloader automation down.

If you get `evalsymlink failure ... /k8s/k8s`, you ran `kubectl apply -k k8s` while already inside `k8s/`.

Equivalent manual apply:
```bash
kubectl apply -f k8s/namespace.yaml
kubectl apply -f k8s/hardening.yaml
kubectl apply -f k8s/secrets.example.yaml
kubectl apply -f k8s/storage-pvc.yaml
kubectl apply -f k8s/core.yaml
kubectl apply -f k8s/ingress-traefik.yaml
kubectl apply -f k8s/scale-policy.yaml
```

Apply optional apps only after core is healthy:
```bash
kubectl apply -f k8s/optional.yaml
```

Apply Unpackerr only after arr API keys are set:
```bash
kubectl apply -f k8s/unpackerr.yaml
kubectl -n media-stack scale deploy/unpackerr --replicas=1
```

## Configuration-as-code bootstrap
Build/push bootstrap runner image first (used by bootstrap Job + cronjobs):
```bash
bash scripts/build-bootstrap-runner-image.sh
```

Run idempotent post-deploy wiring:
```bash
# one-command pipeline:
bash scripts/bootstrap-all.sh

# optional: create/update qB secret (defaults to STACK_ADMIN credentials)
bash scripts/set-qbit-secret.sh
# optional: reconcile qB WebUI credentials to match secret now
bash scripts/ensure-qbit-credentials.sh
bash scripts/run-bootstrap-job.sh
bash scripts/sync-unpackerr-keys.sh
# optional: auto-test all Prowlarr templates/presets and add the ones that pass
bash scripts/run-prowlarr-auto-indexers.sh
# full one-command flow from fresh namespace:
bash scripts/rebuild-and-bootstrap.sh <NODE_IP>
# full one-command bootstrap on existing namespace:
bash scripts/bootstrap-all.sh
```

What it configures:
- Arr root folders
- Arr Completed Download Handling (CDH) defaults
- Prowlarr app links for Sonarr/Radarr/Lidarr/Readarr
- Prowlarr indexers from `prowlarr_indexers` config block
- qBittorrent categories + Arr qBittorrent download clients
- Jellyseerr Sonarr + Radarr mappings
- Jellyseerr Jellyfin wiring
- Jellyfin startup wizard/admin bootstrap (from stack admin secret)
- Jellyfin Movies/TV/Music/Books library wiring (from bootstrap config)
- Jellyfin Live TV tuner/guide reconcile (when enabled in bootstrap config)
- Prowlarr indexer sync trigger

Still manual:
- private provider/indexer credentials and quality preferences
- Private indexer credentials/CAPTCHA providers

Declarative config and job files:
- `bootstrap/media-stack.bootstrap.json`
- `bootstrap/prowlarr-indexers.example.json`
- `scripts/bootstrap-apps.py`
- `k8s/bootstrap-job.yaml`
- `docker/bootstrap-runner.Dockerfile`
- `scripts/build-bootstrap-runner-image.sh`

Override the runner image without editing manifests:
```bash
BOOTSTRAP_RUNNER_IMAGE=<registry>/<repo>/media-stack-bootstrap-runner:<tag> bash scripts/bootstrap-all.sh
```

Set stack admin credentials in `k8s/secrets.example.yaml` for fully automated download-client wiring.
Defaults are `admin` / `media-stack-admin`, and qBittorrent uses those same values by default.
`JELLYFIN_API_KEY` is optional; bootstrap can auto-discover/recover it from Jellyfin DB and persist it in the secret.
Set/update live:
```bash
bash scripts/generate-secrets.sh
bash scripts/set-qbit-secret.sh [USERNAME] [PASSWORD]
bash scripts/ensure-qbit-credentials.sh
bash scripts/set-jellyfin-api-key.sh <JELLYFIN_API_KEY>
```

## Multi-namespace and remote DNS
Deploying side-by-side stacks:
```bash
bash scripts/install.sh --profile full --namespace media-stack-dev --ingress-domain dev.local --node-ip <NODE_IP>
bash scripts/install.sh --profile full --namespace media-stack-e2e --ingress-domain e2e.local --node-ip <NODE_IP>
```

Render host entries for a specific namespace:
```bash
bash scripts/render-hosts-example.sh <NODE_IP> media-stack-dev
```

Render dnsmasq/AdGuard entries for a specific namespace:
```bash
bash scripts/render-dnsmasq-snippet.sh <NODE_IP> media-stack-dev
```

Clean up old test namespaces:
```bash
kubectl get ns -o name | grep '^namespace/media-stack-' | grep -v '^namespace/media-stack$' | xargs -r kubectl delete --wait=false
```

## TLS and DNS
```bash
bash scripts/setup-lan-tls.sh
bash scripts/render-dnsmasq-snippet.sh <NODE_IP> [NAMESPACE]
```

## Backup and restore
```bash
bash scripts/backup-stack.sh
bash scripts/restore-stack.sh ./backups/media-stack-backup-YYYYMMDD-HHMMSS.tar.gz
```

## Scale policy
```bash
bash scripts/apply-scale-policy.sh
SCALE_WORKERS_TO_ZERO=1 bash scripts/apply-scale-policy.sh
```
KEDA worker examples:
```bash
kubectl apply -f k8s/keda-workers.example.yaml
```

## StorageClass Profiles
Deployments are already PVC-based by default. You can choose storage behavior without editing app Deployment YAML files.

1. Default behavior: rely on the cluster default StorageClass (`k8s/storage-pvc.yaml` has no `storageClassName` by default).
2. Inject one class at deploy time (no manifest edits):
```bash
bash scripts/install.sh --profile full --storage-mode dynamic-pvc --storage-class <STORAGE_CLASS_NAME> --node-ip <NODE_IP>
```
3. Pin all claims to a class by using `k8s/pvc-storage.example.yaml` as your template.
   Or patch in place:
```bash
bash scripts/set-pvc-storage-class.sh <STORAGE_CLASS_NAME>
```
4. MicroK8s custom pvDir class example:
```bash
microk8s kubectl apply -f k8s/storageclass-microk8s.example.yaml
```
5. AKS Azure Files example (RWX-friendly):
```bash
kubectl apply -f k8s/storageclass-aks-azurefile.example.yaml
```
6. Verify storage classes:
```bash
kubectl get storageclass
```

## Inspect
```bash
kubectl -n media-stack get pods,svc,ingress
kubectl -n media-stack logs deploy/jellyfin --tail=200
```

## MicroK8s helper scripts
```bash
# only needed if your ingress class is not "public"
bash scripts/microk8s-patch-ingress-class.sh nginx
bash scripts/microk8s-smoke-test.sh <NODE_IP>
bash scripts/microk8s-reconcile.sh --include-optional
# if already in k8s/:
bash ../scripts/microk8s-reconcile.sh --include-optional
```
`microk8s-smoke-test.sh` skips ingress hosts when the backend service is not installed (useful for core-only deployments).

## Common recovery
If logs show `s6-applyuidgid` permission errors, or Deployments are stuck between old/new ReplicaSets:
```bash
bash scripts/microk8s-reconcile.sh --include-optional
```

If Arr apps fail to add root folders with `Folder '/media/' is not writable by user 'abc'`:
```bash
# legacy-hostpath mode only:
sudo PUID=911 PGID=911 bash scripts/fix-media-perms.sh /srv/media-stack
kubectl -n media-stack rollout restart deploy/sonarr deploy/radarr deploy/lidarr deploy/readarr deploy/bazarr deploy/prowlarr deploy/qbittorrent
```

If bootstrap status is unclear, collect focused diagnostics:
```bash
bash scripts/bootstrap-debug.sh
```

If PVCs are stuck `Pending`, inspect claim events and storage class:
```bash
kubectl -n media-stack describe pvc
kubectl get storageclass
```
