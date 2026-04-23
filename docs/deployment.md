# Deployment

Media Stack supports two runtime targets, both driven by the same per-service contracts:

- **Kubernetes** namespace deployment — the primary path; full bootstrap orchestration, periodic reconcile CronJobs, and KEDA-friendly.
- **Docker Compose** project deployment — the easiest path for a single-host install. Compose runs the same controller container and the same bootstrap.

If you don't already have a preference, start with [Compose](#docker-compose-deployment). It's one command from a fresh host.

![Deployment model](diagrams/deployment-model.png)

## Profiles

Both platforms share the same set of profiles (manifests live in `k8s/profiles/*` for Kubernetes and `examples/bootstrap-profiles/` for both):

| Profile | Includes |
|---|---|
| `minimal` | Essential media request / playback path (core services only) |
| `standard` | Core + Sabnzbd, Homepage, Maintainerr, Tautulli + Envoy gateway + controller |
| `full` | Standard + Plex + extended automation |
| `public-demo` | Demo-safe defaults; reduced downloader automation |
| `power-user` | Full + TLS + additional operational guardrails |

The bootstrap profile (`contracts/media-stack.profile.yaml`) declares target, purpose, stack name, install toggles, exposure intent, route strategy, and auth provider defaults. Validate with:

```bash
bash bin/validate-bootstrap-profile.sh
```

## Controller service

The controller is a **persistent Deployment** on both platforms:

- **Kubernetes** — `media-stack-controller` Deployment with ServiceAccount and RBAC.
- **Compose** — `controller` container with `restart: unless-stopped`.

It exposes an HTTP API on port 9100 with an interactive dashboard, action dispatch, SSE log streaming, webhook notifications, runtime config toggles, and action retry. See [Architecture → Controller HTTP API service](internals/architecture.md#controller-http-api-service) for the full endpoint reference.

---

## Docker Compose deployment

### Scope

Supported in the Compose target:

- Deploy / update services from `docker/docker-compose.yml`.
- Wait for running / healthy containers.
- Smoke-check container count + return a node IP hint.
- Print final container status summary.
- Apply route / auth edge labels declaratively from the bootstrap profile.

Not part of the Compose target:

- Kubernetes bootstrap Job / CronJob pipeline.
- Kubernetes Secret-based credential preservation/generation phases.
- Ingress-class patching (Compose routing labels are applied during container create/update).

### Prerequisites

- Docker Engine running and reachable by the Docker SDK (`docker-py`).
- Python runtime deps for automation entrypoints:
  ```bash
  python3 -m pip install docker kubernetes pyyaml requests
  ```
- Optional: `docker/.env` for local overrides (process env is used when absent).
- Optional but recommended: `contracts/media-stack.profile.yaml` for deployment / purpose / install / exposure / auth defaults.

### One-command deploy

**Linux / macOS:**

```bash
./deploy-compose.sh
./deploy-compose.sh --delete   # teardown + redeploy
```

**Any OS (cross-platform, requires Python 3.11+):**

```bash
python deploy.py compose
python deploy.py compose --delete
```

**Plain Compose (any OS):**

```bash
docker compose -f docker/docker-compose.yml up -d
# Wait for the controller to be healthy, then trigger:
curl -X POST http://127.0.0.1:9100/actions/bootstrap -H "Content-Type: application/json" -d "{}"
```

### Advanced deploy with the Stack Runner

```bash
bash bin/deploy-stack.sh \
  --platform-target compose \
  --namespace media-dev \
  --compose-project-name media-dev \
  --compose-file docker/docker-compose.yml \
  --compose-env-file docker/.env
```

Optional profiles:

```bash
bash bin/deploy-stack.sh \
  --platform-target compose \
  --namespace media-dev \
  --compose-project-name media-dev \
  --compose-profiles optional,plex
```

### Compose runtime notes

- Services with `profiles:` are skipped unless selected via `--compose-profiles` / `COMPOSE_PROFILES`.
- `install` toggles from the bootstrap profile map to `selected_apps` filtering.
- Path-prefix and hybrid route strategies can publish browser apps under one gateway host (e.g. `/app/sonarr`) while keeping Jellyfin direct-host routing for TV / mobile clients.
- `AUTH_PROVIDER` supports `none`, `authelia`, `authentik`. Provider services are selected automatically (`authelia`, `authentik`, `authentik-worker`).
- `run_bootstrap` is forced off for non-Kubernetes targets.
- Local browser access depends on your edge router host-port binding (e.g. `TRAEFIK_HTTP_PORT=18080` → `http://apps.media-dev.local:18080/app/homepage`).

### Edge router providers (Compose)

Traefik patching is automatic when the edge provider is `traefik`:

- runtime patch file: `${CONFIG_ROOT}/traefik/dynamic/media-stack.dynamic.yaml`
- implementation owner: `src/media_stack/core/platforms/compose/edge/providers/traefik/patch_service.py`

Envoy is a first-class Compose edge provider:

- runtime patch file: `${CONFIG_ROOT}/envoy/envoy.yaml`
- implementation owner: `src/media_stack/core/platforms/compose/edge/providers/envoy/patch_service.py`
- selection precedence: `--edge-router-provider` → `EDGE_ROUTER_PROVIDER` → `routing.provider` → `adapter_hooks.edge.router_provider`

### Auth provider notes (Compose)

- `authelia` defaults are seeded from `config/defaults/compose/auth/authelia/` on first start.
- `authentik` uses the official Compose server/worker + PostgreSQL pattern.
- Google IdP is configured in the provider UI/config after first start (Authentik: Google social login source + flow binding; Authelia: update `${CONFIG_ROOT}/authelia/configuration.yml` to match your upstream federation design).

---

## Kubernetes deployment

### Assumptions

- A local cluster.
- An ingress class named `public` is available (MicroK8s default).
- PVC-backed storage via `k8s/storage-pvc.yaml`.

### Prerequisites — operator/user

For deploying and running the stack:

- Host OS: Ubuntu 24.04 LTS or 25.04+ recommended (<https://ubuntu.com/download>).
- Kubernetes runtime: MicroK8s (<https://microk8s.io/docs/getting-started>).
- Kubernetes CLI: `kubectl` (<https://kubernetes.io/docs/tasks/tools/install-kubectl-linux/>) or `microk8s kubectl`.
- Python 3 + pip (`sudo apt-get install -y python3 python3-pip`).
- Git.

Validate:

```bash
microk8s status --wait-ready
kubectl version --client
python3 --version && pip3 --version && git --version
```

### Prerequisites — developer

For modifying code, running tests, or extending adapters — everything above plus:

- Python virtualenv tooling (`sudo apt-get install -y python3-venv`).
- Node.js + npm (Playwright + Mermaid rendering, <https://nodejs.org/en/download>).
- Docker Engine for controller image build/push (<https://docs.docker.com/engine/install/ubuntu/>).
- Optional local image registry access for custom controller images.

Validate:

```bash
python3 -m venv .venv && source .venv/bin/activate
python3 -m pip install --upgrade pip
python3 -m pip install docker kubernetes pyyaml requests ruff black
npx -y @mermaid-js/mermaid-cli@10.9.1 -h
bash bin/test.sh
```

### One-command deploy

**Linux / macOS:**

```bash
./deploy-k8s.sh                                              # default profile
./deploy-k8s.sh examples/bootstrap-profiles/media-k8s-standard.yaml
./deploy-k8s.sh my-profile.yaml --delete                     # teardown + redeploy
```

**Any OS:**

```bash
python deploy.py k8s
python deploy.py k8s examples/bootstrap-profiles/media-k8s-standard.yaml
python deploy.py k8s --delete
```

### Manual kubectl

```bash
# applies all manifests via kustomize
kubectl apply -k k8s/profiles/standard

# profile variants
kubectl apply -k k8s/profiles/{minimal,full,public-demo,power-user}

# core only (no optional services)
kubectl apply -k k8s
```

If `kubectl apply -k k8s` errors with `evalsymlink failure ... /k8s/k8s`, you ran it from inside `k8s/`.

### Full manual deploy

```bash
kubectl create namespace media-dev
kubectl apply -k k8s/profiles/standard
kubectl -n media-dev create configmap media-stack-controller-config \
  --from-file=adapter-hooks.yaml=contracts/adapter-hooks.k8s.yaml \
  --dry-run=client -o yaml | kubectl apply -f -
kubectl -n media-dev create configmap media-stack-controller-profile \
  --from-file=profile.yaml=examples/bootstrap-profiles/media-k8s-standard.yaml \
  --dry-run=client -o yaml | kubectl apply -f -
kubectl -n media-dev port-forward svc/media-stack-controller 9100:9100 &
curl -X POST http://127.0.0.1:9100/actions/bootstrap -H "Content-Type: application/json" -d "{}"
```

### Advanced deploy scripts

```bash
# installer wizard with profile selection
bash bin/install.sh --profile full --node-ip <NODE_IP>
bash bin/install.sh --profile full --storage-mode dynamic-pvc --node-ip <NODE_IP>

# deterministic rebuild + verification (recommended for DR confidence)
bash bin/deploy-verify.sh <NODE_IP> [NAMESPACE] [PROFILE]

# fully automatic rebuild + bootstrap + smoke test
bash bin/deploy-stack.sh <NODE_IP>
PROFILE=power-user bash bin/deploy-stack.sh <NODE_IP>
```

`public-demo` intentionally skips bootstrap in `deploy-stack.sh` and scales downloader automation down.

Equivalent manual apply when you don't want kustomize:

```bash
kubectl apply -f k8s/namespace.yaml
kubectl apply -f k8s/hardening.yaml
kubectl apply -f k8s/secrets.example.yaml
kubectl apply -f k8s/storage-pvc.yaml
kubectl apply -f k8s/core.yaml
kubectl apply -f k8s/ingress-traefik.yaml
kubectl apply -f k8s/scale-policy.yaml
```

Apply optional apps after core is healthy:

```bash
kubectl apply -f k8s/optional.yaml
```

Apply Unpackerr after Arr API keys are set:

```bash
kubectl apply -f k8s/unpackerr.yaml
kubectl -n media-stack scale deploy/unpackerr --replicas=1
```

### Configuration-as-code bootstrap

Build / push the controller image first (used by the controller Deployment + CronJobs):

```bash
bash bin/build-controller-image.sh
```

Run idempotent post-deploy wiring:

```bash
# one-command pipeline
bash bin/bootstrap-all.sh

# optional: create/update qB secret (defaults to STACK_ADMIN credentials)
bash bin/set-qbit-secret.sh

# optional: reconcile qB WebUI credentials to match secret
bash bin/ensure-qbit-credentials.sh
bash bin/run-bootstrap-job.sh
bash bin/sync-unpackerr-keys.sh

# optional: auto-test all Prowlarr templates/presets, add the ones that pass
bash bin/run-prowlarr-auto-indexers.sh

# full one-command flow from fresh namespace
bash bin/deploy-stack.sh <NODE_IP>
```

What it configures:

- Arr root folders + Arr Completed Download Handling defaults
- Prowlarr app links for Sonarr / Radarr / Lidarr / Readarr
- Prowlarr indexers from the `prowlarr_indexers` config block
- qBittorrent categories + Arr qBittorrent download clients
- Jellyseerr Sonarr + Radarr mappings + Jellyfin wiring
- Jellyfin startup wizard / admin bootstrap (from stack admin secret)
- Jellyfin Movies / TV / Music / Books library wiring
- Jellyfin Live TV tuner / guide reconcile (when enabled in profile)
- Prowlarr indexer sync trigger

Still manual:

- Private provider/indexer credentials and quality preferences
- Private indexer credentials / CAPTCHA providers

Override the runner image without editing manifests:

```bash
BOOTSTRAP_RUNNER_IMAGE=<registry>/<repo>/media-stack-controller:<tag> bash bin/bootstrap-all.sh
```

Set stack admin credentials in `k8s/secrets.example.yaml` for fully automated download-client wiring. Defaults are `admin` / `<namespace>`, and qBittorrent uses those same values by default. `JELLYFIN_API_KEY` is optional; bootstrap can auto-discover or recover it from the Jellyfin DB and persist it in the secret.

```bash
bash bin/generate-secrets.sh
bash bin/set-qbit-secret.sh [USERNAME] [PASSWORD]
bash bin/ensure-qbit-credentials.sh
bash bin/set-jellyfin-api-key.sh <JELLYFIN_API_KEY>
```

### Multi-namespace and remote DNS

```bash
bash bin/install.sh --profile full --namespace media-stack-dev --ingress-domain dev.local --node-ip <NODE_IP>
bash bin/install.sh --profile full --namespace media-stack-e2e --ingress-domain e2e.local --node-ip <NODE_IP>
```

Render host entries for a specific namespace:

```bash
bash bin/render-hosts-example.sh <NODE_IP> media-stack-dev
```

Render dnsmasq / AdGuard entries:

```bash
bash bin/render-dnsmasq-snippet.sh <NODE_IP> media-stack-dev
```

Clean up old test namespaces:

```bash
kubectl get ns -o name | grep '^namespace/media-stack-' | grep -v '^namespace/media-stack$' | xargs -r kubectl delete --wait=false
```

### TLS and DNS

```bash
bash bin/setup-lan-tls.sh
bash bin/render-dnsmasq-snippet.sh <NODE_IP> [NAMESPACE]
```

### Backup and restore

```bash
bash bin/backup-stack.sh
bash bin/restore-stack.sh ./backups/media-stack-backup-YYYYMMDD-HHMMSS.tar.gz
```

### Scale policy

```bash
bash bin/apply-scale-policy.sh
SCALE_TO_ZERO=1 bash bin/apply-scale-policy.sh
```

KEDA background-component examples:

```bash
kubectl apply -f k8s/keda-workers.example.yaml
```

### StorageClass profiles

Deployments are PVC-based by default. Choose storage behavior without editing app Deployment YAML:

1. **Default** — rely on the cluster default StorageClass (`k8s/storage-pvc.yaml` has no `storageClassName` by default).
2. **Inject one class at deploy time:**
   ```bash
   bash bin/install.sh --profile full --storage-mode dynamic-pvc --storage-class <NAME> --node-ip <NODE_IP>
   ```
3. **Pin all claims to a class** — use `k8s/pvc-storage.example.yaml` as your template, or patch in place:
   ```bash
   bash bin/set-pvc-storage-class.sh <NAME>
   ```
4. **MicroK8s custom pvDir class:**
   ```bash
   microk8s kubectl apply -f k8s/storageclass-microk8s.example.yaml
   ```
5. **AKS Azure Files (RWX-friendly):**
   ```bash
   kubectl apply -f k8s/storageclass-aks-azurefile.example.yaml
   ```
6. **Verify:**
   ```bash
   kubectl get storageclass
   ```

### Inspect

```bash
kubectl -n media-stack get pods,svc,ingress
kubectl -n media-stack logs deploy/jellyfin --tail=200
```

### MicroK8s helpers

```bash
# only needed if your ingress class is not "public"
bash bin/microk8s-patch-ingress-class.sh nginx
bash bin/microk8s-smoke-test.sh <NODE_IP>
bash bin/microk8s-reconcile.sh --include-optional
```

`microk8s-smoke-test.sh` skips ingress hosts when the backend service isn't installed (useful for core-only deployments).

### Common recovery

If logs show `s6-applyuidgid` permission errors, or Deployments are stuck between old/new ReplicaSets:

```bash
bash bin/microk8s-reconcile.sh --include-optional
```

If Arr apps fail to add root folders with `Folder '/media/' is not writable by user 'abc'`:

```bash
kubectl -n media-stack rollout restart \
  deploy/sonarr deploy/radarr deploy/lidarr deploy/readarr \
  deploy/bazarr deploy/prowlarr deploy/qbittorrent
```

For unclear bootstrap status, collect focused diagnostics:

```bash
MEDIA_STACK_LOG_LEVEL=DEBUG bash bin/bootstrap-all.sh --no-resume
```

If PVCs stay `Pending`, inspect claim events and storage class:

```bash
kubectl -n media-stack describe pvc
kubectl get storageclass
```

---

## Storage modes

`dynamic-pvc` is required: StorageClass / PVC-driven, portable across clusters.

```bash
bash bin/install.sh --profile full --storage-mode dynamic-pvc --node-ip <NODE_IP>
```

## Namespace strategy

Use namespace isolation for environment promotion and safe experimentation:

```bash
bash bin/install.sh --profile full --namespace media-stack-dev  --ingress-domain dev.local  --node-ip <NODE_IP>
bash bin/install.sh --profile full --namespace media-stack-prod --ingress-domain prod.local --node-ip <NODE_IP>
```

## Rebuild-first operations

The expected operating posture is rebuild-ready:

- PVC manifests are applied idempotently.
- Manifests are re-applied safely.
- Bootstrap wiring is re-runnable.
- Verification scripts validate outcomes.

Full Kubernetes rebuild + verify in one command:

```bash
bash bin/deploy-verify.sh <NODE_IP> [NAMESPACE] [PROFILE]
```

Compose rebuild:

```bash
bash bin/deploy-stack.sh \
  --platform-target compose \
  --namespace media-dev \
  --compose-project-name media-dev
```

Compose rebuild with profile auto-defaults:

```bash
bash bin/deploy-stack.sh --bootstrap-profile-file contracts/media-stack.profile.yaml
```

## Runtime reconciliation

Both platforms use the same persistent controller HTTP API:

| Endpoint | Purpose |
|---|---|
| `POST /actions/reconcile` | On-demand idempotent re-wiring |
| `POST /actions/bootstrap` | Full pipeline; supports `{"retry": N}` for automatic retry |
| `POST /config {"auto_download_content": true}` | Toggle runtime behavior without redeploying |
| `POST /reload` | Hot-reload profile YAML and re-apply env vars |

Platform specifics:

- **Kubernetes** — bootstrap config supplied via ConfigMap from adapter-hooks YAML and profile YAML; optional reconcile CronJobs for periodic re-apply; auth providers available as optional manifests; all linuxserver.io images have `PUID/PGID=1000`; Jellyfin has `securityContext`.
- **Compose** — route strategy supports subdomain, path-prefix, or hybrid; auth provider runtime is pluggable; controller publishes port 9100 for direct dashboard access; Tautulli runs in default profile (no longer requires `--profile plex`).

## Multi-node / remote operator note

Kubernetes mode is StorageClass / PVC-driven, so remote operators can apply manifests from any machine with cluster access.

---

**Project Steward**
Matthew Loschiavo • [matthewloschiavo.com](https://matthewloschiavo.com) • [mploschiavo@gmail.com](mailto:mploschiavo@gmail.com) • [LinkedIn](https://www.linkedin.com/in/matthewloschiavo)
