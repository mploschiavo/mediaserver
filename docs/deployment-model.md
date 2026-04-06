# Deployment Model

## Core Model

The stack supports two runtime targets:
- Kubernetes namespace deployment (primary path)
- Docker Compose project deployment (alternate path)

![Deployment model](diagrams/deployment-model.png)

Kubernetes flow:
1. Apply profile manifests (Kustomize overlays under `k8s/profiles/`).
2. Generate or reconcile secrets.
3. Wait for workloads (including bootstrap Deployment).
4. Bootstrap service auto-runs on startup (`--serve --auto-run`), configuring all apps via HTTP API.
5. Verify with Playwright E2E tests (ingress + UX smoke + path-prefix).
6. Keep drift low with periodic reconcile CronJobs or on-demand `POST /actions/reconcile`.

Compose flow:
1. Render native compose spec (`docker/docker-compose.yml` + env expansion).
2. Start all services including persistent bootstrap-runner container.
3. Bootstrap-runner auto-runs and configures apps, exposes dashboard on port 9100.
4. Envoy gateway generated automatically from bootstrap profile.
5. Verify with same Playwright E2E tests (identical test suite, different env vars).

Bootstrap deployment profile:
- `contracts/media-stack.profile.yaml` declares target, purpose, stack name, install toggles, exposure intent, route strategy, and auth provider defaults.
- Use `bash bin/validate-bootstrap-profile.sh` to validate profile shape + semantics.

## Profiles

- `minimal`: essential media request/playback path (core services only).
- `standard`: core + optional services (sabnzbd, homepage, maintainerr, tautulli, etc.) + Envoy gateway + bootstrap service.
- `full`: standard + plex, tautulli, and extended automation.
- `public-demo`: demo-safe defaults and reduced downloader automation.
- `power-user`: full with TLS and additional operational guardrails.

Profile manifests live in `k8s/profiles/*` (Kubernetes) and `examples/bootstrap-profiles/` (both platforms).

## Bootstrap Service

The bootstrap runner is a **persistent Deployment** (not a one-shot Job) on both platforms:
- Kubernetes: `media-stack-bootstrap` Deployment with ServiceAccount and RBAC
- Compose: `bootstrap-runner` container with `restart: unless-stopped`

It exposes an HTTP API on port 9100 with an interactive dashboard, action dispatch,
SSE log streaming, webhook notifications, runtime config toggles, and action retry.
See [architecture.md](architecture.md) for the full endpoint reference.

## Storage Modes

- `dynamic-pvc` (required): StorageClass/PVC-driven and portable across clusters.

Example:
```bash
bash bin/install.sh --profile full --storage-mode dynamic-pvc --node-ip <NODE_IP>
```

## Namespace Strategy

Use namespace isolation for environment promotion and safe experimentation.

Example:
```bash
bash bin/install.sh --profile full --namespace media-stack-dev --ingress-domain dev.local --node-ip <NODE_IP>
bash bin/install.sh --profile full --namespace media-stack-prod --ingress-domain prod.local --node-ip <NODE_IP>
```

## Rebuild-First Operations

The expected operating posture is rebuild-ready:
- PVC manifests are applied idempotently
- manifests are re-applied safely
- bootstrap wiring is re-runnable
- verification scripts validate outcomes

One command for full Kubernetes rebuild + verify:
```bash
bash bin/deploy-verify.sh <NODE_IP> [NAMESPACE] [PROFILE]
```

Compose rebuild example:
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

## Runtime Reconciliation

Both platforms use the same persistent bootstrap HTTP API:
- `POST /actions/reconcile` — on-demand idempotent re-wiring
- `POST /actions/bootstrap` — full pipeline with optional `{"retry": N}` for automatic retry
- `POST /config {"auto_download_content": true}` — toggle runtime behavior without re-deploying
- `POST /reload` — hot-reload profile YAML and re-apply env vars

Platform-specific:
- Kubernetes:
  - Bootstrap config supplied via ConfigMap from `contracts/media-stack.config.json`.
  - Optional reconcile CronJobs for periodic re-apply.
  - Auth providers available as optional manifests (`k8s/auth-authelia.yaml`, `k8s/auth-authentik.yaml`).
  - All linuxserver.io images have PUID/PGID=1000; Jellyfin has securityContext.
- Compose:
  - Route strategy supports subdomain, path-prefix, or hybrid patterns.
  - Auth provider runtime is pluggable (`none`, `authelia`, `authentik`).
  - Bootstrap-runner publishes port 9100 for direct dashboard access.
  - Tautulli runs in default profile (no longer requires `--profile plex`).

## Multi-Node / Remote Operator Note

Kubernetes mode is StorageClass/PVC-driven, so remote operators can apply manifests from any machine with cluster access.

---

**Project Steward**
Matthew Loschiavo • [matthewloschiavo.com](https://matthewloschiavo.com) • [mploschiavo@gmail.com](mailto:mploschiavo@gmail.com) • [LinkedIn](https://www.linkedin.com/in/matthewloschiavo)
