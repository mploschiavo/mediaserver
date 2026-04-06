# Kubernetes Guide

This bundle assumes:
- a local cluster
- an ingress class named `public` is available (MicroK8s default)
- PVC-backed storage via `k8s/storage-pvc.yaml`

## Prerequisites (Operator/User)

Use this path if your goal is to deploy and run the stack.

- Host OS: Ubuntu 24.04 LTS or Ubuntu 25.04+ (recommended)
  - https://ubuntu.com/download
- Kubernetes runtime:
  - MicroK8s: https://microk8s.io/docs/getting-started
- Kubernetes CLI:
  - `kubectl`: https://kubernetes.io/docs/tasks/tools/install-kubectl-linux/
  - Or use `microk8s kubectl`
- Python 3 + pip:
  - https://www.python.org/downloads/
  - Ubuntu install:
    - `sudo apt-get update`
    - `sudo apt-get install -y python3 python3-pip`
- Git:
  - https://git-scm.com/download/linux

Quick validation:

```bash
microk8s status --wait-ready
kubectl version --client
python3 --version
pip3 --version
git --version
```

## Prerequisites (Developer)

Use this path if you are modifying code, running tests, or extending adapters.

- Everything in Operator/User prerequisites
- Python virtual environment tooling:
  - Ubuntu: `sudo apt-get install -y python3-venv`
- Node.js + npm (Playwright and Mermaid rendering):
  - https://nodejs.org/en/download
- Docker Engine (optional, for bootstrap-runner image build/push):
  - https://docs.docker.com/engine/install/ubuntu/
- Optional local image registry access for custom bootstrap runner images

Quick validation:

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install --upgrade pip
python3 -m pip install docker kubernetes pyyaml requests
python3 -m pip install ruff black
npx -y @mermaid-js/mermaid-cli@10.9.1 -h
bash bin/test.sh
```

## Apply

### One-command deploy (recommended)

**Linux / macOS:**
```bash
./deploy-k8s.sh                                              # default profile
./deploy-k8s.sh examples/bootstrap-profiles/media-k8s-standard.yaml
./deploy-k8s.sh my-profile.yaml --delete                     # teardown + redeploy
```

**Any OS (cross-platform, requires Python 3.11+):**
```bash
python deploy.py k8s                                         # default profile
python deploy.py k8s examples/bootstrap-profiles/media-k8s-standard.yaml
python deploy.py k8s --delete                                # teardown + redeploy
```

### Manual kubectl commands (works on any OS with kubectl)
```bash
# from repo root — applies all manifests via kustomize
kubectl apply -k k8s/profiles/standard

# profile variants
kubectl apply -k k8s/profiles/minimal
kubectl apply -k k8s/profiles/full
kubectl apply -k k8s/profiles/public-demo
kubectl apply -k k8s/profiles/power-user

# from repo root (core only, no optional services)
kubectl apply -k k8s
```

### Full manual deploy (any OS)
```bash
kubectl create namespace media-dev
kubectl apply -k k8s/profiles/standard
kubectl -n media-dev create configmap media-stack-bootstrap-config \
  --from-file=config.json=contracts/media-stack.config.json --dry-run=client -o yaml | kubectl apply -f -
kubectl -n media-dev create configmap media-stack-bootstrap-profile \
  --from-file=profile.yaml=examples/bootstrap-profiles/media-k8s-standard.yaml --dry-run=client -o yaml | kubectl apply -f -
# Wait for pods to start, then trigger bootstrap:
kubectl -n media-dev port-forward svc/media-stack-bootstrap 9100:9100 &
curl -X POST http://127.0.0.1:9100/actions/bootstrap -H "Content-Type: application/json" -d "{}"
```

### Advanced deploy scripts
```bash
# installer wizard with profile selection
bash bin/install.sh --profile full --node-ip <NODE_IP>
bash bin/install.sh --profile full --storage-mode dynamic-pvc --node-ip <NODE_IP>

# deterministic rebuild + verification (recommended for DR confidence)
bash bin/deploy-verify.sh <NODE_IP> [NAMESPACE] [PROFILE]

# fully automatic rebuild + bootstrap + smoke test (recommended)
bash bin/deploy-stack.sh <NODE_IP>
PROFILE=power-user bash bin/deploy-stack.sh <NODE_IP>
```
Profile notes:
- `public-demo` intentionally skips bootstrap in `deploy-stack.sh` and scales downloader automation down.

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
Build/push bootstrap runner image first (used by bootstrap Deployment + CronJobs):
```bash
bash bin/build-bootstrap-runner-image.sh
```

Run idempotent post-deploy wiring:
```bash
# one-command pipeline:
bash bin/bootstrap-all.sh

# optional: create/update qB secret (defaults to STACK_ADMIN credentials)
bash bin/set-qbit-secret.sh
# optional: reconcile qB WebUI credentials to match secret now
bash bin/ensure-qbit-credentials.sh
bash bin/run-bootstrap-job.sh
bash bin/sync-unpackerr-keys.sh
# optional: auto-test all Prowlarr templates/presets and add the ones that pass
bash bin/run-prowlarr-auto-indexers.sh
# full one-command flow from fresh namespace:
bash bin/deploy-stack.sh <NODE_IP>
# full one-command bootstrap on existing namespace:
bash bin/bootstrap-all.sh
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
- `contracts/media-stack.config.json`
- `contracts/prowlarr-indexers.example.json`
- `bin/bootstrap-apps.py`
- `k8s/bootstrap.yaml`
- `docker/bootstrap-runner.Dockerfile`
- `bin/build-bootstrap-runner-image.sh`

Override the runner image without editing manifests:
```bash
BOOTSTRAP_RUNNER_IMAGE=<registry>/<repo>/media-stack-bootstrap-runner:<tag> bash bin/bootstrap-all.sh
```

Set stack admin credentials in `k8s/secrets.example.yaml` for fully automated download-client wiring.
Defaults are `admin` / `<namespace>`, and qBittorrent uses those same values by default.
`JELLYFIN_API_KEY` is optional; bootstrap can auto-discover/recover it from Jellyfin DB and persist it in the secret.
Set/update live:
```bash
bash bin/generate-secrets.sh
bash bin/set-qbit-secret.sh [USERNAME] [PASSWORD]
bash bin/ensure-qbit-credentials.sh
bash bin/set-jellyfin-api-key.sh <JELLYFIN_API_KEY>
```

## Multi-namespace and remote DNS
Deploying side-by-side stacks:
```bash
bash bin/install.sh --profile full --namespace media-stack-dev --ingress-domain dev.local --node-ip <NODE_IP>
bash bin/install.sh --profile full --namespace media-stack-e2e --ingress-domain e2e.local --node-ip <NODE_IP>
```

Render host entries for a specific namespace:
```bash
bash bin/render-hosts-example.sh <NODE_IP> media-stack-dev
```

Render dnsmasq/AdGuard entries for a specific namespace:
```bash
bash bin/render-dnsmasq-snippet.sh <NODE_IP> media-stack-dev
```

Clean up old test namespaces:
```bash
kubectl get ns -o name | grep '^namespace/media-stack-' | grep -v '^namespace/media-stack$' | xargs -r kubectl delete --wait=false
```

## TLS and DNS
```bash
bash bin/setup-lan-tls.sh
bash bin/render-dnsmasq-snippet.sh <NODE_IP> [NAMESPACE]
```

## Backup and restore
```bash
bash bin/backup-stack.sh
bash bin/restore-stack.sh ./backups/media-stack-backup-YYYYMMDD-HHMMSS.tar.gz
```

## Scale policy
```bash
bash bin/apply-scale-policy.sh
SCALE_TO_ZERO=1 bash bin/apply-scale-policy.sh
```
KEDA background component examples:
```bash
kubectl apply -f k8s/keda-workers.example.yaml
```

## StorageClass Profiles
Deployments are already PVC-based by default. You can choose storage behavior without editing app Deployment YAML files.

1. Default behavior: rely on the cluster default StorageClass (`k8s/storage-pvc.yaml` has no `storageClassName` by default).
2. Inject one class at deploy time (no manifest edits):
```bash
bash bin/install.sh --profile full --storage-mode dynamic-pvc --storage-class <STORAGE_CLASS_NAME> --node-ip <NODE_IP>
```
3. Pin all claims to a class by using `k8s/pvc-storage.example.yaml` as your template.
   Or patch in place:
```bash
bash bin/set-pvc-storage-class.sh <STORAGE_CLASS_NAME>
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
bash bin/microk8s-patch-ingress-class.sh nginx
bash bin/microk8s-smoke-test.sh <NODE_IP>
bash bin/microk8s-reconcile.sh --include-optional
# if already in k8s/:
bash ../bin/microk8s-reconcile.sh --include-optional
```
`microk8s-smoke-test.sh` skips ingress hosts when the backend service is not installed (useful for core-only deployments).

## Common recovery
If logs show `s6-applyuidgid` permission errors, or Deployments are stuck between old/new ReplicaSets:
```bash
bash bin/microk8s-reconcile.sh --include-optional
```

If Arr apps fail to add root folders with `Folder '/media/' is not writable by user 'abc'`:
```bash
kubectl -n media-stack rollout restart deploy/sonarr deploy/radarr deploy/lidarr deploy/readarr deploy/bazarr deploy/prowlarr deploy/qbittorrent
```

If bootstrap status is unclear, collect focused diagnostics:
```bash
MEDIA_STACK_LOG_LEVEL=DEBUG bash bin/bootstrap-all.sh --no-resume
```

If PVCs are stuck `Pending`, inspect claim events and storage class:
```bash
kubectl -n media-stack describe pvc
kubectl get storageclass
```

---

**Project Steward**
Matthew Loschiavo • [matthewloschiavo.com](https://matthewloschiavo.com) • [mploschiavo@gmail.com](mailto:mploschiavo@gmail.com) • [LinkedIn](https://www.linkedin.com/in/matthewloschiavo)
