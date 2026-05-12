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

The bootstrap profile (`contracts/media-stack.profile.yaml`) declares target, purpose, stack name, install toggles, exposure intent, route strategy, and auth provider defaults. Validate with the cross-platform CLI:

```bash
# After 'pip install -e .' — see First-time setup below:
media-stack-validate-profile

# Equivalent without the console-script:
.venv/bin/python -m media_stack.cli.commands.validate_controller_profile_main

# Linux convenience wrapper:
bash bin/utils/validate-bootstrap-profile.sh
```

## First-time setup

`git clone` doesn't create `.venv/` or install Python deps — you have to
do that once per machine. After this step, all three forms shown
throughout this guide (`media-stack-*` console-scripts,
`.venv/bin/python -m media_stack.cli.commands.*`, and the Linux `bash bin/...`
wrappers) all work.

**Linux / macOS:**

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"          # add ,docs] if you also want the mkdocs deps
```

**Windows (PowerShell):**

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
```

`pip install -e .` is editable install — it puts `media_stack` on
`sys.path` pointing at the cloned tree and registers every console-script
declared in `pyproject.toml` (`media-stack-deploy`, `media-stack-backup`,
`media-stack-teardown`, etc.) under `.venv/bin/` (or `.venv/Scripts\` on
Windows). Activating the venv puts those on your PATH.

Verify:

```bash
media-stack-validate-profile --help        # console-script, after activate
.venv/bin/python -m media_stack.version    # module form
```

> **Cross-platform vs. Linux-only paths.** This guide shows three
> forms for each CLI: the `media-stack-*` console-script (cleanest,
> works on Windows / macOS / Linux), the equivalent
> `.venv/bin/python -m media_stack.cli.commands.<X>_main` module
> invocation (same thing under the hood), and the `bash bin/<subdir>/*.sh`
> Linux convenience wrapper. Pick whichever you prefer. The Docker /
> kubectl recipes work everywhere with no setup. Anything genuinely
> Linux-only (MicroK8s, `/etc/hosts` mangling, host DNS rendering) is
> called out explicitly.

### Console-scripts cheatsheet

After `pip install -e .` these names are on PATH and replace the longer
`.venv/bin/python -m media_stack.cli.commands.<X>_main` invocation:

| Console-script | Module form |
|---|---|
| `media-stack-deploy` | `deploy_stack_main` |
| `media-stack-deploy-verify` | `deploy_verify_main` |
| `media-stack-backup` | `backup_stack_main` |
| `media-stack-restore` | `restore_stack_main` |
| `media-stack-teardown` | `teardown_stack_main` |
| `media-stack-verify` | `verify_fresh_install` |
| `media-stack-build-controller` | `build_controller_image_main` |
| `media-stack-build-ui` | `build_ui_image_main` |
| `media-stack-install` | `services.apps.stack.cli.install_main` |
| `media-stack-bootstrap-all` | `controller_all_main` |
| `media-stack-watch-install` | `watch_install_main` |
| `media-stack-microk8s-reconcile` | `microk8s_reconcile_main` |
| `media-stack-microk8s-smoke` | `microk8s_smoke_test_main` |
| `media-stack-reset-admin` | `reset_admin_main` |
| `media-stack-setup-lan-tls` | `setup_lan_tls_main` |
| `media-stack-set-pvc-storage-class` | `set_pvc_storage_class_main` |
| `media-stack-validate-config` | `validate_controller_config_main` |
| `media-stack-validate-profile` | `validate_controller_profile_main` |
| `media-stack-apply-scale-policy` | `apply_scale_policy_main` |
| `media-stack-run-unit-tests` | `run_unit_tests_main` |
| `media-stack-release` | `release_pipeline_main` |

A few modules don't have console-script entry points yet (e.g.
`generate_secrets_main`, `verify_flow_main`, `run_playwright_screenshots_main`,
`microk8s_reconcile_main`'s alias) — use the module form for those.

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

### Two ways to deploy compose

There are two valid invocation styles. Use the **Workflow CLI**
for the canonical "first-time deploy on a fresh box" experience;
use **plain `docker compose`** for every subsequent restart / log
tail / single-service operation. The table below summarises the
difference; the two subsections that follow give the actual
commands.

| Concern | Plain `docker compose` | `media-stack-deploy` (workflow CLI) |
|---|---|---|
| First-time deploy on a fresh clone | ⚠️  Insecure defaults (see below) | ✅  Canonical path |
| Re-up / restart / logs / ps | ✅  Familiar, fast | overkill |
| Renders `deploy/compose/.env` from `secrets.generated.env` + profile | ❌  no — falls back to image defaults (`STACK_ADMIN_PASSWORD=admin`) | ✅  rendered before `up -d` |
| Runs `compose_preflight_handler` entries (Authelia config seed, Bazarr URL-base, Sabnzbd API access, Jellyfin bootstrap auth) | ❌  bypassed | ✅  every contract that declares one |
| Applies `apps.<service>: false` profile toggles | ❌  every container in the YAML starts | ✅  profile-driven service selection |
| Selects compose profiles (e.g. `optional`, `plex`) | manual `COMPOSE_PROFILES=…` env | profile-aware |
| qBit / Jellyfin / *arr password + URL-base sync at boot | ✅  (moved into the orchestrator promise loop in v1.0.367 — fires on every controller boot regardless of how compose came up) | ✅ |
| Audit chain verifier / log-feed wiring | ✅  controller does this itself | ✅ |

The orchestrator post-boot reconciler closes most of the gap on
re-ups, but the **`.env` rendering** and **compose_preflight
handler** items only happen via the Workflow CLI, and they ARE
load-bearing on a fresh box — otherwise the stack comes up with
`STACK_ADMIN_PASSWORD=admin` (the controller's blocklist guard
fires a WARN but doesn't refuse to boot unless
`internet_exposed=true` in the profile).

#### Path A — Workflow CLI (first-time deploy, profile changes)

All deployment settings come from the bootstrap profile YAML, not
from CLI flags. The deploy CLI accepts an optional `NODE_IP`
positional (k8s only) and `--bootstrap-profile-file FILE` — that's
it.

Three invocation forms, matching the [teardown how-to](teardown.md):

**Console-script** (after `pip install -e .` — recommended for
repeated use; works on Windows / macOS / Linux equally because
the entry point resolves through Python's installed-package
metadata, not relative imports):

```bash
media-stack-deploy \
    --bootstrap-profile-file deploy/examples/bootstrap-profiles/media-compose-standard.yaml
```

**`.venv` module form** (fallback if the console script isn't on
PATH, e.g. you have a virtualenv but skipped the editable install):

```bash
cd /path/to/media-automation-stack
.venv/bin/python -m media_stack.cli.commands.deploy_stack_main \
    --bootstrap-profile-file deploy/examples/bootstrap-profiles/media-compose-standard.yaml
```

**No-venv form** (any host with `python3` + PyYAML — the form CI
uses, and the right form for fresh clones / remote-host
troubleshooting where you can't easily install into a venv):

```bash
cd /path/to/media-automation-stack
PYTHONPATH=src python3 -m media_stack.cli.commands.deploy_stack_main \
    --bootstrap-profile-file deploy/examples/bootstrap-profiles/media-compose-standard.yaml
```

What this actually does, in order:

1. Resolves the bootstrap profile YAML (`--bootstrap-profile-file`
   flag → `BOOTSTRAP_PROFILE_FILE` env → `contracts/media-stack.profile.yaml`).
2. Reads `metadata.platform` from the profile to dispatch to compose
   vs k8s.
3. Writes `deploy/compose/.env` from `secrets.generated.env` +
   profile defaults (`STACK_ADMIN_USERNAME` / `STACK_ADMIN_PASSWORD`,
   `STACK_ADMIN_EMAIL`, `CONFIG_ROOT` / `DATA_ROOT` / `MEDIA_ROOT`,
   `APP_PATH_PREFIX`, etc.).
4. Runs every `compose_preflight_handler` declared in
   `contracts/services/*.yaml` against the resolved config:
   - `authelia` — copies `users_database.yml` defaults into the
     auth provider config dir
   - `bazarr` — sets the URL-base for reverse-proxy access
   - `jellyfin` — runs `cli_ensure_controller_main` to seed the
     wizard with the stack-admin password and discover the API key
   - `qbittorrent` — rotates the temp WebUI password to
     `STACK_ADMIN_PASSWORD` and applies the auth-bypass whitelist
     (orchestrator also runs this at controller boot now, but the
     preflight runs it before `up -d` so qBit comes up healthy on
     the first start)
   - `sabnzbd` — seeds the API key into the controller secret if
     it's already on disk
   - `<arr>` — patches each Servarr config.xml to set
     `AuthenticationMethod=External` + `UrlBase=/app/<arr>`
5. Calls `docker compose up -d` against
   `deploy/compose/docker-compose.yml` (with the
   `apps.<service>: false` profile toggles applied as the
   `selected_apps` filter).
6. Optionally triggers the controller's `bootstrap` action if the
   profile sets `bootstrap.run_bootstrap: true` (default for
   compose is true; k8s has its own conditional logic).

The bootstrap profile decides every behaviour the CLI used to
accept as flags:

| Behaviour | Profile key |
|---|---|
| compose vs k8s | `metadata.platform` (`compose` or `k8s`) |
| namespace / compose project name | `metadata.name` |
| optional services | `apps.<service>: true/false` + `install_profile` |
| compose profiles selection | env `COMPOSE_PROFILES` (set explicitly) |
| compose file / env file paths | profile defaults; env `COMPOSE_FILE` / `COMPOSE_ENV_FILE` override |
| run bootstrap action | `bootstrap.run_bootstrap` |

Use the right profile for your target — e.g.
`deploy/examples/bootstrap-profiles/media-compose-standard.yaml`
(compose) or `media-k8s-standard.yaml` (k8s). Operators
typically copy one of those into
`contracts/media-stack.profile.yaml` once at install time and the
deploy CLI picks it up by default (the
`--bootstrap-profile-file` flag overrides that for ad-hoc runs).

Linux convenience wrapper: `bash bin/install/deploy-stack.sh
[NODE_IP] [--bootstrap-profile-file FILE]` — passes through to
the same `deploy_stack_main` module.

#### Path B — Plain `docker compose` (re-ups, day-to-day)

Once the stack has been deployed once with the Workflow CLI (so
`deploy/compose/.env` exists with the strong stack-admin password
+ every compose_preflight has run at least once), plain
`docker compose` is the right tool for everyday operations:

```bash
# From the repo root (most familiar — works thanks to the
# include: stub in ./compose.yaml):
docker compose up -d
docker compose ps
docker compose logs -f media-stack-controller
docker compose restart sonarr

# From anywhere with the long-form -f (equivalent):
docker compose -f deploy/compose/docker-compose.yml up -d
```

Both forms address the same project (``name: media-stack``) so
they don't conflict — invoke from whichever path is convenient.

Plain `docker compose up -d` runs the controller, which on every
boot reconciles the things that used to require the deploy CLI:

* qBit auth-bypass whitelist + password rotation
  (`qbittorrent:ensure-credentials` job, `pre_bootstrap` phase,
  priority 5)
* Jellyfin admin password sync (`jellyfin:ensure-credentials`
  job, `pre_bootstrap` phase, priority 6)
* *arr `UrlBase` reconcile (five `<arr>:ensure-url-base` jobs,
  one per Servarr fork)
* Audit chain verifier
* Authelia post-up config seed (separate path from the
  compose_preflight seed; same end state)

What plain `docker compose up -d` does **not** do (and why a
fresh-box first deploy still needs the Workflow CLI):

* **Does not render `deploy/compose/.env`** — the compose YAML
  reads env vars from there, and if the file is missing the
  containers fall back to image defaults
  (`STACK_ADMIN_PASSWORD=admin`). The controller's blocklist guard
  fires a WARN but doesn't refuse to boot unless
  `internet_exposed=true` in the profile.
* **Does not run compose_preflight handlers** that are NOT
  represented in the orchestrator promise loop — Bazarr URL-base,
  Sabnzbd API access seed, Jellyfin first-run wizard. These mostly
  recover on their own via the orchestrator's reconcile path after
  ~one tick, but the first dashboard hit during that window can
  show partial state.
* **Does not apply `apps.<service>: false` profile toggles** — every
  container in `docker-compose.yml` starts regardless. To filter,
  set `COMPOSE_PROFILES=…` explicitly.

### Compose runtime notes

- Services with `profiles:` are skipped unless selected via the `COMPOSE_PROFILES` env var (e.g. `COMPOSE_PROFILES=optional,plex media-stack-deploy --bootstrap-profile-file …`).
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
media-stack-run-unit-tests
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
media-stack-install --profile full --node-ip <NODE_IP>
media-stack-install --profile full --storage-mode dynamic-pvc --node-ip <NODE_IP>

# deterministic rebuild + verification (recommended for DR confidence)
media-stack-deploy-verify <NODE_IP> [NAMESPACE] [PROFILE]

# fully automatic rebuild + bootstrap + smoke test
media-stack-deploy <NODE_IP>
PROFILE=power-user media-stack-deploy <NODE_IP>
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
media-stack-build-controller
media-stack-build-ui
```

Linux convenience wrappers: `bash bin/build/build-controller-image.sh`, `bash bin/build/build-ui-image.sh`.

Run idempotent post-deploy wiring. **The controller does this automatically on startup** — the manual hooks below are for re-running or debugging individual phases:

```bash
# one-command pipeline (cross-platform):
curl -X POST http://localhost:9100/actions/bootstrap

# full one-command flow from fresh namespace:
media-stack-deploy <NODE_IP>
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
media-stack-install \
    --profile full --namespace media-stack-dev --ingress-domain dev.local --node-ip <NODE_IP>
media-stack-install \
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
media-stack-setup-lan-tls
```

Linux convenience: `bash bin/utils/setup-lan-tls.sh`. The dnsmasq/hosts renderers are Linux-only (see Multi-namespace section above).

### Backup and restore

Cross-platform (Windows / macOS / Linux):

```bash
media-stack-backup
media-stack-restore ./backups/media-stack-backup-YYYYMMDD-HHMMSS.tar.gz
```

Linux convenience wrappers: `bash bin/utils/backup-stack.sh`, `bash bin/utils/restore-stack.sh`.

### Scale policy

Cross-platform:

```bash
media-stack-apply-scale-policy
SCALE_TO_ZERO=1 media-stack-apply-scale-policy
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
   media-stack-install \
       --profile full --storage-mode dynamic-pvc --storage-class <NAME> --node-ip <NODE_IP>
   ```
3. **Pin all claims to a class** — use `deploy/k8s/pvc-storage.example.yaml` as your template, or patch in place:
   ```bash
   media-stack-set-pvc-storage-class <NAME>
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

The smoke-test skips ingress hosts when the backend service isn't installed (useful for core-only deployments). The reconcile and smoke-test scripts are Python under the hood (`microk8s_reconcile_main`, `microk8s_smoke_test_main`) — you can call them as `media-stack-microk8s-reconcile ...` from any OS, but the cluster they target will still need to be MicroK8s.

### Common recovery

If logs show `s6-applyuidgid` permission errors, or Deployments are stuck between old/new ReplicaSets:

```bash
media-stack-microk8s-reconcile --include-optional
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
media-stack-install --profile full --storage-mode dynamic-pvc --node-ip <NODE_IP>
```

## Namespace strategy

Use namespace isolation for environment promotion and safe experimentation:

```bash
media-stack-install \
    --profile full --namespace media-stack-dev  --ingress-domain dev.local  --node-ip <NODE_IP>
media-stack-install \
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
media-stack-deploy-verify <NODE_IP> [NAMESPACE] [PROFILE]
```

Compose rebuild:

```bash
media-stack-deploy \
  --platform-target compose \
  --namespace media-dev \
  --compose-project-name media-dev
```

Compose rebuild with profile auto-defaults:

```bash
media-stack-deploy --bootstrap-profile-file contracts/media-stack.profile.yaml
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

2026-05-12 — rewrote the compose section as "Two ways to deploy
compose" with explicit Path A (Workflow CLI for first-time
deploys) / Path B (plain `docker compose` for day-to-day) split.
Spells out exactly what each path does and does not do, with a
table of differences up front. Documents the new root-level
`compose.yaml` stub that makes `docker compose up -d` work from
the repo root (via compose v2.20+ ``include:`` of the real file
at `deploy/compose/docker-compose.yml`). Stale CLI flag examples
removed — the deploy CLI only takes `[NODE_IP]` and
`--bootstrap-profile-file FILE`; everything else comes from the
profile YAML.

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
