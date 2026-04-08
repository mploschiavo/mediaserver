# K8s Deploy + Bootstrap Pipeline Reference

## Command

```bash
bash bin/deploy-stack.sh --bootstrap-profile-file examples/bootstrap-profiles/media-k8s-standard.yaml
```

## Pipeline Overview

```
deploy-stack.sh
  └─ deploy_stack_main.py
       │
       ├─ Phase 1:  Resolve profile defaults (from YAML)
       ├─ Phase 2:  Validate bootstrap config JSON
       ├─ Phase 3:  Prepare host directories (SKIPPED — K8s uses dynamic PVCs)
       ├─ Phase 4:  Backup existing secret values
       ├─ Phase 5:  Delete namespace (OPTIONAL — DELETE_NAMESPACE=1)
       ├─ Phase 6:  Apply manifests for profile ← kustomize + kubectl apply
       ├─ Phase 7:  Generate secrets
       ├─ Phase 8:  Restore secret values from backup
       ├─ Phase 9:  Patch ingress class
       ├─ Phase 10: Wait for deployments ← kubectl rollout status (all deployments)
       ├─ Phase 11: Apply scale-policy guardrails
       ├─ Phase 12: Run bootstrap pipeline ← bootstrap-all.sh (see below)
       ├─ Phase 13: Run ingress smoke test
       └─ Phase 14: Print final pod status
```

---

## Phase 6: Apply Manifests

**File:** `src/media_stack/core/platforms/kubernetes/services/rebuild_manifest_apply_service.py`

1. Run `kubectl kustomize k8s/profiles/standard/`
2. Pipe output through `rebuild_manifest_overrides_service.py`:
   - Rewrite `namespace: media-stack` → `namespace: <profile.name>`
   - Rewrite `name: media-stack` → `name: <profile.name>`
   - Rewrite `/srv/media-stack` → `<profile.storage.config_root parent>`
   - Rewrite `*.local` → `*.<profile.routing.base_domain>`
   - Inject `storageClassName` if configured
3. Create namespace (idempotent)
4. Delete any existing Jobs (immutable — must be recreated)
5. `kubectl apply -f -` with conflict fallback (replace → create)

**Resources applied** (from `k8s/profiles/standard/kustomization.yaml`):

| File | Resources |
|------|-----------|
| `namespace.yaml` | Namespace |
| `hardening.yaml` | LimitRange |
| `secrets.example.yaml` | Secret (placeholder) |
| `storage-pvc.yaml` | 19 PVCs (config + data + media) |
| `core.yaml` | 9 Deployments + 9 Services (jellyfin, jellyseerr, prowlarr, qbittorrent, sonarr, radarr, lidarr, readarr, bazarr) |
| `optional.yaml` | 7 Deployments + Services (sabnzbd, plex, tautulli, homepage, maintainerr, flaresolverr, jellyfin-auto-collections) + 3 CronJobs |
| `envoy.yaml` | ConfigMap (base template), PVC, envoy-config-init Job, Envoy Deployment + NodePort Service |
| `ingress-traefik.yaml` | Ingress (13 virtual hosts) |
| `unpackerr.yaml` | Deployment (replicas: 0) |
| `scale-policy.yaml` | 9 PodDisruptionBudgets |

**Note:** `controller.yaml` (Deployment + Service + RBAC) IS in the standard kustomization.
The controller service starts idle and is triggered via HTTP after ConfigMaps are created.
The separate `prowlarr-auto-indexers-job.yaml` has been replaced by `POST /actions/auto-indexers`.

---

## Phase 10: Wait for Deployments

**File:** `src/media_stack/core/platforms/kubernetes/services/rebuild_deployments_wait_service.py`

For each Deployment with replicas > 0:
```bash
kubectl -n <namespace> rollout status deploy/<name> --timeout=20m
```

---

## Phase 12: Bootstrap Pipeline

**File:** `src/media_stack/cli/commands/deploy_pipeline_service.py` → `bootstrap-all.sh` → `controller_main.py`

### State file isolation

State file: `.state/bootstrap-all-<namespace>-<platform>.json`

This prevents compose and K8s runs from sharing checkpoint state.

### Phase Plan (from `contracts/adapter-hooks.k8s.yaml`)

```
controller_main.py
  │
  ├─ Phase 1: Ensure torrent client bootstrap access (qbittorrent)
  │    └─ Script: ensure-qbit-credentials.sh
  │    └─ Condition: qbit is selected for arr_clients OR categories
  │
  ├─ Phase 2: Ensure media server bootstrap access (jellyfin)
  │    └─ Script: ensure-jellyfin-bootstrap.sh
  │    └─ Condition: jellyfin bootstrap script exists
  │
  ├─ Phase 3: Ensure usenet client API access (sabnzbd)
  │    └─ Script: ensure-sabnzbd-api-access.sh
  │    └─ Condition: sabnzbd selected for arr_clients
  │
  ├─ Phase 4: Run bootstrap job ← THE MAIN EVENT
  │    └─ Script: run-bootstrap-job.sh → run_bootstrap_job_main.py
  │    └─ See "Bootstrap Job Runner" below
  │
  ├─ Phase 5: Seed request manager local admin (jellyseerr)
  │    └─ Script: seed-jellyseerr-local-admin.sh
  │    └─ Condition: jellyseerr script exists
  │
  ├─ Phase 6: Run indexer auto-discovery (prowlarr)
  │    └─ Script: run-prowlarr-auto-indexers.sh → prowlarr_auto_indexers_runtime.py
  │    └─ See "Auto-Indexer Job" below
  │
  └─ Phase 7: Enable components (unpackerr)
       └─ Action: apply manifest, scale to 1, wait for rollout
       └─ Condition: ENABLE_COMPONENTS flag is true
```

---

## Phase 4 Detail: Bootstrap Job Runner

**File:** `src/media_stack/cli/commands/run_bootstrap_job_main.py`

This orchestrates the main K8s bootstrap Job:

```
run_bootstrap_job_main.py
  │
  ├─ Step 1: Prepare bootstrap job config
  │    └─ Load bootstrap JSON, apply config policy (routing, auth, etc.)
  │    └─ Write to temp file for ConfigMap
  │
  ├─ Step 2: Ensure bootstrap PVC prerequisites
  │    └─ Verify all app config PVCs exist
  │
  ├─ Step 3: Prime Arr API keys into secret
  │    └─ For each arr app: read config.xml → extract ApiKey → patch K8s Secret
  │
  ├─ Step 4: Prime usenet client API key into secret (sabnzbd)
  ├─ Step 5: Prime request manager API key into secret (jellyseerr)
  ├─ Step 6: Prime analytics API key into secret (tautulli, if enabled)
  │
  ├─ Step 7: Update bootstrap ConfigMaps ← CRITICAL
  │    └─ Create ConfigMap: media-stack-controller-config (from prepared JSON)
  │    └─ Create ConfigMap: media-stack-controller-profile (from profile YAML)
  │
  ├─ Step 8: Recreate bootstrap Job
  │    └─ kubectl delete job media-stack-controller --ignore-not-found
  │    └─ kubectl apply -f k8s/controller.yaml (with overrides)
  │
  ├─ Step 9: Wait for bootstrap Job completion
  │    └─ Poll: kubectl get job -o json (every 3s, heartbeat every 15s)
  │    └─ Timeout: configurable (default ~20m)
  │
  ├─ Step 10: Prime media server API key (jellyfin)
  │    └─ Read API key from Job logs or config
  │
  ├─ Step 11: Prime media server user id (jellyfin)
  │
  └─ Step 12: Print bootstrap Job logs
```

### What the Controller Deployment Does

**Manifest:** `k8s/controller.yaml`
**Entrypoint:** `controller_main.py --serve --auto-run`
**Port:** 9100 (HTTP API)

The controller pod starts an HTTP server and auto-triggers the bootstrap:

```
controller_main.py (inside K8s controller pod)
  │
  ├─ Start HTTP API server on :9100
  │    └─ GET  /healthz  — liveness
  │    └─ GET  /readyz   — readiness (true after init)
  │    └─ GET  /status   — telemetry dashboard
  │    └─ POST /run      — trigger bootstrap (or --auto-run)
  │
  ├─ Run preflight handlers (from container_preflight_handlers config):
  │    ├─ jellyfin:    wizard completion, user auth, password rotation, API key
  │    ├─ qbittorrent: wait for reachable (accept 200/403)
  │    ├─ sabnzbd:     host whitelist + local ranges config
  │    ├─ arr_auth:    patch config.xml (AuthMethod=Forms, UrlBase=/app/<svc>)
  │    └─ api_keys:    extract/validate all API keys
  │
  ├─ Run main bootstrap pipeline:
  │    └─ Configure arr download clients (qbittorrent, sabnzbd)
  │    └─ Configure arr root folders, quality profiles
  │    └─ Configure Prowlarr ↔ arr sync
  │    └─ Configure Homepage tiles with correct URLs
  │    └─ Configure Jellyseerr ↔ Jellyfin integration
  │
  └─ Run post-bootstrap handlers:
       ├─ restart_apps: restart all apps to pick up config changes
       └─ unpackerr:    write unpackerr config
```

---

## Phase 6 Detail: Auto-Indexer Job

**File:** `src/media_stack/services/apps/prowlarr/cli/prowlarr_auto_indexers_runtime.py`

```
prowlarr_auto_indexers_runtime.py
  │
  ├─ Step 1: Ensure PVC prerequisites (media-stack-config-prowlarr)
  │
  ├─ Step 2: Create ConfigMap: media-stack-controller-config-auto
  │    └─ Minimal JSON with: technology_bindings, prowlarr_url, arr_apps, exclusions
  │
  ├─ Step 3: Trigger auto-indexers via controller API
  │    └─ Triggered via: POST /actions/auto-indexers via controller API
  │    └─ Image: controller
  │    └─ Command: --auto-prowlarr-indexers
  │    └─ Volume: ConfigMap media-stack-controller-config-auto + prowlarr PVC
  │
  ├─ Step 4: Wait for Job completion (poll every 3s)
  │
  └─ Step 5: Print Job logs
```

---

## ConfigMap Lifecycle

| ConfigMap | Created By | When | Used By |
|-----------|-----------|------|---------|
| `media-stack-controller-config` | run_bootstrap_job_main.py Step 7 | During bootstrap pipeline Phase 4 | bootstrap Job pod, CronJobs |
| `media-stack-controller-profile` | run_bootstrap_job_main.py Step 7 | During bootstrap pipeline Phase 4 | bootstrap Job pod |
| `media-stack-controller-config-auto` | prowlarr_auto_indexers_runtime.py Step 2 | During bootstrap pipeline Phase 6 | auto-indexer Job pod |
| `media-stack-envoy-base-template` | kustomize apply (Phase 6 of deploy) | During manifest apply | envoy-config-init Job |

**Critical ordering:** The envoy-config-init Job and bootstrap Job reference ConfigMaps as volumes.
- `envoy.yaml` marks ConfigMap volumes as `optional: true` → Job starts even if ConfigMap missing
- `controller.yaml` is NOT in kustomization → only applied after ConfigMaps exist (Step 8 above)

---

## Known Issue: Stale State File

**Problem:** `.state/bootstrap-all-media-dev.json` was shared between compose and K8s runs
because both use namespace `media-dev`. Resume mode (default) skips completed phases.

**Fix:** State file now scoped by platform: `bootstrap-all-<namespace>-<platform>.json`

---

## Key Files Reference

### Orchestration
| File | Purpose |
|------|---------|
| `bin/deploy-stack.sh` | Entry point |
| `src/media_stack/cli/commands/deploy_stack_main.py` | Main deploy runner |
| `src/media_stack/cli/commands/deploy_pipeline_service.py` | Pipeline step helpers |
| `src/media_stack/cli/commands/deploy_cli_config_service.py` | Config/profile parsing |

### Bootstrap
| File | Purpose |
|------|---------|
| `src/media_stack/cli/commands/bootstrap_all_main.py` | Phase plan executor |
| `src/media_stack/cli/commands/bootstrap_component_resolver.py` | Phase/component resolution from JSON |
| `src/media_stack/cli/commands/run_bootstrap_job_main.py` | Bootstrap K8s Job orchestrator |
| `src/media_stack/cli/commands/bootstrap_manifest_service.py` | ConfigMap + Job creation |
| `src/media_stack/cli/commands/bootstrap_job_wait_service.py` | Job status polling |
| `src/media_stack/cli/commands/controller_main.py` | Controller HTTP API (runs inside Job pod) |
| `contracts/adapter-hooks.k8s.yaml` | Phase plan config |

### K8s Platform
| File | Purpose |
|------|---------|
| `src/media_stack/core/platforms/kubernetes/plugin.py` | K8s platform plugin |
| `src/media_stack/core/platforms/kubernetes/services/rebuild_manifest_apply_service.py` | Kustomize + apply |
| `src/media_stack/core/platforms/kubernetes/services/rebuild_manifest_overrides_service.py` | Namespace/domain overrides |
| `src/media_stack/core/platforms/kubernetes/services/rebuild_deployments_wait_service.py` | Rollout waiting |

### Auto-Indexer
| File | Purpose |
|------|---------|
| `src/media_stack/services/apps/prowlarr/cli/prowlarr_auto_indexers_runtime.py` | Auto-indexer orchestrator |
| `POST /actions/auto-indexers` | Triggered via controller API (replaces former Job manifest) |

### Manifests
| File | Included in kustomization? |
|------|---------------------------|
| `k8s/namespace.yaml` | Yes |
| `k8s/hardening.yaml` | Yes |
| `k8s/secrets.example.yaml` | Yes |
| `k8s/storage-pvc.yaml` | Yes (19 PVCs) |
| `k8s/core.yaml` | Yes (9 apps) |
| `k8s/optional.yaml` | Yes (7 apps + 3 CronJobs) |
| `k8s/envoy.yaml` | Yes (ConfigMap, PVC, init Job, Deployment, Service) |
| `k8s/ingress-traefik.yaml` | Yes |
| `k8s/unpackerr.yaml` | Yes (replicas: 0) |
| `k8s/scale-policy.yaml` | Yes (9 PDBs) |
| `k8s/controller.yaml` | **No** — applied by bootstrap Phase 4, Step 8 |
| Auto-indexers | **No** — triggered via controller API `POST /actions/auto-indexers` |
