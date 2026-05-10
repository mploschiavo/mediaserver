# Deployment

Media Stack supports two runtime targets, both driven by the same per-service contracts:

- **Kubernetes** namespace deployment — the primary path; full bootstrap orchestration, periodic reconcile CronJobs, and KEDA-friendly.
- **Docker Compose** project deployment — the easiest path for a single-host install. Compose runs the same controller container and the same bootstrap.

If you don't already have a preference, start with [Compose](#docker-compose-deployment). It's one command from a fresh host.

![Deployment model](../diagrams/deployment-model.png)

## Profiles

Both platforms share the same set of profiles (manifests live in `deploy/k8s/profiles/*` for Kubernetes and `examples/bootstrap-profiles/` for both):

| Profile | Includes |
|---|---|
| `minimal` | Essential media request / playback path (core services only) |
| `standard` | Core + Sabnzbd, Homepage, Maintainerr, Tautulli + Envoy gateway + controller |
| `full` | Standard + Plex + extended automation |
| `public-demo` | Demo-safe defaults; reduced downloader automation |
| `power-user` | Full + TLS + additional operational guardrails |

The bootstrap profile (`contracts/media-stack.profile.yaml`) declares target, purpose, stack name, install toggles, exposure intent, route strategy, and auth provider defaults. Validate with the cross-platform CLI (Windows / macOS / Linux):

```bash
.venv/bin/python -m media_stack.cli.commands.validate_controller_profile_main
```

Linux convenience wrapper: `bash bin/utils/validate-bootstrap-profile.sh` (6-line wrapper around the Python module above).

> **Cross-platform vs. Linux-only paths.** This guide leads with `docker compose`, `kubectl`, and `python -m media_stack.cli.commands.*` invocations — these work identically on Windows, macOS, and Linux. The `bash bin/<subdir>/*.sh` scripts are thin convenience wrappers (mostly just `exec` the Python module); they're handy on Linux but you don't need them. Where a bash script does something Linux-specific (MicroK8s, `/etc/hosts` mangling, host DNS rendering) the section calls it out explicitly.

## Controller service

The controller is a **persistent Deployment** on both platforms:

- **Kubernetes** — `media-stack-controller` Deployment with ServiceAccount and RBAC.
- **Compose** — `controller` container with `restart: unless-stopped`.

It exposes an HTTP API on port 9100 with an interactive dashboard, action dispatch, SSE log streaming, webhook notifications, runtime config toggles, and action retry. See [Architecture → Controller HTTP API service](../architecture/overview.md#controller-http-api-service) for the full endpoint reference.

---

## Docker Compose deployment

### Scope

Supported in the Compose target:

- Deploy / update services from `deploy/compose/docker-compose.yml`.
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
- Optional: `deploy/compose/.env` for local overrides (process env is used when absent).
- Optional but recommended: `contracts/media-stack.profile.yaml` for deployment / purpose / install / exposure / auth defaults.

### One-command deploy

**Any OS (cross-platform, requires Python 3.11+):**

```bash
python deploy.py compose
python deploy.py compose --delete
```

**Plain Compose (any OS):**

```bash
docker compose -f deploy/compose/docker-compose.yml up -d
# Wait for the controller to be healthy, then trigger:
curl -X POST http://127.0.0.1:9100/actions/bootstrap -H "Content-Type: application/json" -d "{}"
```

### Stack Runner workflow (cross-platform orchestration)

When you want orchestrated deploy + health-check + profile resolution in one
command, use the Python workflow CLI (works on Windows / macOS / Linux):

```bash
.venv/bin/python -m media_stack.cli.commands.deploy_stack_main \
  --platform-target compose \
  --namespace media-dev \
  --compose-project-name media-dev \
  --compose-file deploy/compose/docker-compose.yml \
  --compose-env-file deploy/compose/.env
```

Optional profiles:

```bash
.venv/bin/python -m media_stack.cli.commands.deploy_stack_main \
  --platform-target compose \
  --namespace media-dev \
  --compose-project-name media-dev \
  --compose-profiles optional,plex
```

Linux convenience wrapper: `bash bin/install/deploy-stack.sh ...`.

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
- PVC-backed storage via `deploy/k8s/base/storage/storage-pvc.yaml`.

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
.venv/bin/python -m media_stack.cli.commands.run_unit_tests_main
```

### One-command deploy (any OS)

```bash
python deploy.py k8s
python deploy.py k8s examples/bootstrap-profiles/media-k8s-standard.yaml
python deploy.py k8s --delete
```

Linux convenience wrapper: `./deploy-k8s.sh` (same args, calls `python deploy.py k8s` under the hood).

### Manual kubectl (recommended — works everywhere kubectl works)

```bash
# applies all manifests via kustomize
kubectl apply -k deploy/k8s/profiles/standard

# profile variants
kubectl apply -k deploy/k8s/profiles/{minimal,full,public-demo,power-user}

# the core base (no profile overlays)
kubectl apply -k deploy/k8s/base
```

If `kubectl apply -k ...` errors with `evalsymlink failure`, you're probably inside the `deploy/k8s/` tree — `cd` back to the repo root.

### Full manual deploy

```bash
kubectl create namespace media-dev
kubectl apply -k deploy/k8s/profiles/standard
kubectl -n media-dev create configmap media-stack-controller-config \
  --from-file=adapter-hooks.yaml=contracts/adapter-hooks.k8s.yaml \
  --dry-run=client -o yaml | kubectl apply -f -
kubectl -n media-dev create configmap media-stack-controller-profile \
  --from-file=profile.yaml=examples/bootstrap-profiles/media-k8s-standard.yaml \
  --dry-run=client -o yaml | kubectl apply -f -
kubectl -n media-dev port-forward svc/media-stack-controller 9100:9100 &
curl -X POST http://127.0.0.1:9100/actions/bootstrap -H "Content-Type: application/json" -d "{}"
```

### Workflow CLIs (cross-platform orchestration)

When you want orchestration beyond a single `kubectl apply -k`, the Python workflow
CLIs run on Windows / macOS / Linux:

```bash
# installer wizard with profile selection
.venv/bin/python -m media_stack.cli.commands.install_main --profile full --node-ip <NODE_IP>
.venv/bin/python -m media_stack.cli.commands.install_main --profile full --storage-mode dynamic-pvc --node-ip <NODE_IP>

# deterministic rebuild + verification (recommended for DR confidence)
.venv/bin/python -m media_stack.cli.commands.deploy_verify_main <NODE_IP> [NAMESPACE] [PROFILE]

# fully automatic rebuild + bootstrap + smoke test
.venv/bin/python -m media_stack.cli.commands.deploy_stack_main <NODE_IP>
PROFILE=power-user .venv/bin/python -m media_stack.cli.commands.deploy_stack_main <NODE_IP>
```

Linux convenience wrappers: `bash bin/install/install.sh`, `bash bin/test/deploy-verify.sh`, `bash bin/install/deploy-stack.sh`.

`public-demo` intentionally skips bootstrap in `deploy_stack_main` and scales downloader automation down.

Equivalent manual apply when you don't want kustomize:

```bash
kubectl apply -f deploy/k8s/base/namespace.yaml
kubectl apply -f deploy/k8s/base/hardening.yaml
kubectl apply -f deploy/k8s/base/secrets.example.yaml
kubectl apply -f deploy/k8s/base/storage/storage-pvc.yaml
kubectl apply -f deploy/k8s/base/apps/core.yaml
kubectl apply -f deploy/k8s/base/edge/ingress-traefik.yaml
kubectl apply -f deploy/k8s/base/scale-policy.yaml
```

Apply optional apps after core is healthy:

```bash
kubectl apply -f deploy/k8s/base/apps/optional.yaml
```

Apply Unpackerr after Arr API keys are set:

```bash
kubectl apply -f deploy/k8s/base/apps/unpackerr.yaml
kubectl -n media-stack scale deploy/unpackerr --replicas=1
```

### Configuration-as-code bootstrap

Build / push the controller image (cross-platform — uses the Docker CLI under the hood):

```bash
.venv/bin/python -m media_stack.cli.commands.build_controller_image_main
.venv/bin/python -m media_stack.cli.commands.build_ui_image_main
```

Linux convenience wrappers: `bash bin/build/build-controller-image.sh`, `bash bin/build/build-ui-image.sh`.

Run idempotent post-deploy wiring. **The controller does this automatically on startup** — the manual hooks below are for re-running or debugging individual phases:

```bash
# one-command pipeline (cross-platform):
curl -X POST http://localhost:9100/actions/bootstrap

# full one-command flow from fresh namespace:
.venv/bin/python -m media_stack.cli.commands.deploy_stack_main <NODE_IP>
```

Linux-only debug helpers (under `bin/debug/`, used when an `ensure-*` job is misbehaving and you want to reconcile a single service from a shell):

```bash
bash bin/debug/set-qbit-secret.sh
bash bin/debug/ensure-qbit-credentials.sh
bash bin/debug/sync-unpackerr-keys.sh
bash bin/debug/run-prowlarr-auto-indexers.sh
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

Override the runner image without editing manifests by setting the env var on the
controller Deployment (`BOOTSTRAP_RUNNER_IMAGE=<registry>/<repo>/media-stack-controller:<tag>`)
and bouncing the pod.

Set stack admin credentials in `deploy/k8s/base/secrets.example.yaml` for fully automated download-client wiring. Defaults are `admin` / `<namespace>`, and qBittorrent uses those same values by default. `JELLYFIN_API_KEY` is optional; bootstrap can auto-discover or recover it from the Jellyfin DB and persist it in the secret.

Cross-platform: edit the secret YAML directly. Linux-only convenience helpers for
ad-hoc credential resets (all live under `bin/debug/` or `bin/utils/`):

```bash
bash bin/utils/generate-secrets.sh
bash bin/debug/set-qbit-secret.sh [USERNAME] [PASSWORD]
bash bin/debug/ensure-qbit-credentials.sh
bash bin/debug/set-jellyfin-api-key.sh <JELLYFIN_API_KEY>
```

### Multi-namespace and remote DNS

Multi-namespace install (cross-platform):

```bash
.venv/bin/python -m media_stack.cli.commands.install_main \
    --profile full --namespace media-stack-dev --ingress-domain dev.local --node-ip <NODE_IP>
.venv/bin/python -m media_stack.cli.commands.install_main \
    --profile full --namespace media-stack-e2e --ingress-domain e2e.local --node-ip <NODE_IP>
```

Render host entries and dnsmasq/AdGuard snippets for a specific namespace (Linux-only — these helpers depend on `/etc/hosts` + dnsmasq path conventions):

```bash
bash bin/utils/render-hosts-example.sh <NODE_IP> media-stack-dev
bash bin/utils/render-dnsmasq-snippet.sh <NODE_IP> media-stack-dev
```

Clean up old test namespaces (cross-platform):

```bash
kubectl get ns -o name | grep '^namespace/media-stack-' | grep -v '^namespace/media-stack$' | xargs -r kubectl delete --wait=false
```

### TLS and DNS

Cross-platform:

```bash
.venv/bin/python -m media_stack.cli.commands.setup_lan_tls_main
```

Linux convenience: `bash bin/utils/setup-lan-tls.sh`. The dnsmasq/hosts renderers are Linux-only (see Multi-namespace section above).

### Backup and restore

Cross-platform (Windows / macOS / Linux):

```bash
.venv/bin/python -m media_stack.cli.commands.backup_stack_main
.venv/bin/python -m media_stack.cli.commands.restore_stack_main ./backups/media-stack-backup-YYYYMMDD-HHMMSS.tar.gz
```

Linux convenience wrappers: `bash bin/utils/backup-stack.sh`, `bash bin/utils/restore-stack.sh`.

### Scale policy

Cross-platform:

```bash
.venv/bin/python -m media_stack.cli.commands.apply_scale_policy_main
SCALE_TO_ZERO=1 .venv/bin/python -m media_stack.cli.commands.apply_scale_policy_main
```

Linux convenience: `bash bin/utils/apply-scale-policy.sh`.

KEDA background-component examples:

```bash
kubectl apply -f deploy/k8s/keda-workers.example.yaml
```

### StorageClass profiles

Deployments are PVC-based by default. Choose storage behavior without editing app Deployment YAML:

1. **Default** — rely on the cluster default StorageClass (`deploy/k8s/base/storage/storage-pvc.yaml` has no `storageClassName` by default).
2. **Inject one class at deploy time:**
   ```bash
   .venv/bin/python -m media_stack.cli.commands.install_main \
       --profile full --storage-mode dynamic-pvc --storage-class <NAME> --node-ip <NODE_IP>
   ```
3. **Pin all claims to a class** — use `deploy/k8s/pvc-storage.example.yaml` as your template, or patch in place:
   ```bash
   .venv/bin/python -m media_stack.cli.commands.set_pvc_storage_class_main <NAME>
   ```
4. **MicroK8s custom pvDir class:**
   ```bash
   microk8s kubectl apply -f deploy/k8s/storageclass-microk8s.example.yaml
   ```
5. **AKS Azure Files (RWX-friendly):**
   ```bash
   kubectl apply -f deploy/k8s/storageclass-aks-azurefile.example.yaml
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

### MicroK8s helpers (Linux-only — MicroK8s itself is Linux-only)

```bash
# only needed if your ingress class is not "public"
bash bin/k8s/microk8s-patch-ingress-class.sh nginx
bash bin/test/microk8s-smoke-test.sh <NODE_IP>
bash bin/k8s/microk8s-reconcile.sh --include-optional
```

The smoke-test skips ingress hosts when the backend service isn't installed (useful for core-only deployments). The reconcile and smoke-test scripts are Python under the hood (`microk8s_reconcile_main`, `microk8s_smoke_test_main`) — you can call them as `.venv/bin/python -m media_stack.cli.commands.microk8s_reconcile_main ...` from any OS, but the cluster they target will still need to be MicroK8s.

### Common recovery

If logs show `s6-applyuidgid` permission errors, or Deployments are stuck between old/new ReplicaSets:

```bash
.venv/bin/python -m media_stack.cli.commands.microk8s_reconcile_main --include-optional
```

If Arr apps fail to add root folders with `Folder '/media/' is not writable by user 'abc'`:

```bash
kubectl -n media-stack rollout restart \
  deploy/sonarr deploy/radarr deploy/lidarr deploy/readarr \
  deploy/bazarr deploy/prowlarr deploy/qbittorrent
```

For unclear bootstrap status, collect focused diagnostics by re-running the bootstrap action with DEBUG logging on the controller pod:

```bash
kubectl -n media-stack set env deploy/media-stack-controller MEDIA_STACK_LOG_LEVEL=DEBUG
kubectl -n media-stack rollout restart deploy/media-stack-controller
curl -X POST http://localhost:9100/actions/bootstrap -H "Content-Type: application/json" -d '{"resume": false}'
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
.venv/bin/python -m media_stack.cli.commands.install_main --profile full --storage-mode dynamic-pvc --node-ip <NODE_IP>
```

## Namespace strategy

Use namespace isolation for environment promotion and safe experimentation:

```bash
.venv/bin/python -m media_stack.cli.commands.install_main \
    --profile full --namespace media-stack-dev  --ingress-domain dev.local  --node-ip <NODE_IP>
.venv/bin/python -m media_stack.cli.commands.install_main \
    --profile full --namespace media-stack-prod --ingress-domain prod.local --node-ip <NODE_IP>
```

## Rebuild-first operations

The expected operating posture is rebuild-ready:

- PVC manifests are applied idempotently.
- Manifests are re-applied safely.
- Bootstrap wiring is re-runnable.
- Verification scripts validate outcomes.

Full Kubernetes rebuild + verify in one command (cross-platform):

```bash
.venv/bin/python -m media_stack.cli.commands.deploy_verify_main <NODE_IP> [NAMESPACE] [PROFILE]
```

Compose rebuild:

```bash
.venv/bin/python -m media_stack.cli.commands.deploy_stack_main \
  --platform-target compose \
  --namespace media-dev \
  --compose-project-name media-dev
```

Compose rebuild with profile auto-defaults:

```bash
.venv/bin/python -m media_stack.cli.commands.deploy_stack_main --bootstrap-profile-file contracts/media-stack.profile.yaml
```

Linux convenience wrappers: `bash bin/test/deploy-verify.sh`, `bash bin/install/deploy-stack.sh`.

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

## Last reviewed

2026-05-10 — refreshed every `bin/*.sh` path to its new `bin/<subdir>/`
location and demoted Linux-only bash invocations in favour of the
cross-platform `python -m media_stack.cli.commands.<X>_main` form.
The bash scripts under `bin/install/`, `bin/test/`, `bin/build/`,
`bin/utils/`, `bin/k8s/` are 6-line `exec` wrappers around those
Python modules; the Linux convenience callouts stay so Linux users
can keep using them, but Windows and macOS operators don't need
them at all. Also fixed: `docker/docker-compose.yml` →
`deploy/compose/docker-compose.yml`, `k8s/profiles/*` →
`deploy/k8s/profiles/*`, `k8s/storage-pvc.yaml` →
`deploy/k8s/base/storage/storage-pvc.yaml`, and the rest of the
post-ADR-0012 directory reorganisation.

---

**Project Steward**
Matthew Loschiavo • [matthewloschiavo.com](https://matthewloschiavo.com) • [mploschiavo@gmail.com](mailto:mploschiavo@gmail.com) • [LinkedIn](https://www.linkedin.com/in/matthewloschiavo)
