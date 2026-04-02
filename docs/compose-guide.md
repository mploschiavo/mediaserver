# Compose Guide

Docker Compose is supported as an alternate runtime target for rebuild deployment flows.

Kubernetes remains the primary runtime path for full bootstrap job orchestration and periodic reconcile jobs.

## Compose Runtime Scope

Supported in Compose target:
- deploy/update selected services from `docker/docker-compose.yml`
- wait for running/healthy containers
- smoke-check running container count and return a node IP hint
- print final container status summary
- apply route/auth edge labels declaratively from bootstrap profile/runtime flags

Not currently part of Compose target:
- Kubernetes bootstrap job/CronJob pipeline
- Kubernetes Secret-based credential preservation/generation phases
- ingress-class patching phase (Compose routing labels are applied during container create/update)

## Prerequisites

- Docker Engine running and reachable by Docker SDK (`docker-py`)
- Optional: `docker/.env` for local overrides (defaults from process env when omitted)
- Optional but recommended: `bootstrap/media-stack.bootstrap.yaml` for deployment/purpose/install/exposure/auth defaults

## Deploy with Rebuild Runner (Compose Target)

```bash
bash scripts/rebuild-and-bootstrap.sh \
  --platform-target compose \
  --namespace media-dev \
  --compose-project-name media-dev \
  --compose-file docker/docker-compose.yml \
  --compose-env-file docker/.env
```

Optional profiles:

```bash
bash scripts/rebuild-and-bootstrap.sh \
  --platform-target compose \
  --namespace media-dev \
  --compose-project-name media-dev \
  --compose-profiles optional,plex
```

Notes:
- Services with `profiles:` are skipped unless selected via `--compose-profiles` / `COMPOSE_PROFILES`.
- `install` toggles from bootstrap profile map to `selected_apps` filtering for Compose deployment.
- Path-prefix and hybrid route strategies can publish browser apps under one gateway host (for example `/app/sonarr`) while keeping Jellyfin direct-host routing for TV/mobile clients.
- `AUTH_PROVIDER` supports `none`, `authelia`, and `authentik` middleware wiring stubs in Compose labels.
- `run_bootstrap` is forced off for non-Kubernetes targets.

See also:
- [Bootstrap Profile](bootstrap-profile.md)
- [Deployment Model](deployment-model.md)
- [Kubernetes Guide](k8s-guide.md)
- [Operations](operations.md)

---

**Project Steward**
Matthew Loschiavo • [matthewloschiavo.com](https://matthewloschiavo.com) • [mploschiavo@gmail.com](mailto:mploschiavo@gmail.com) • [LinkedIn](https://www.linkedin.com/in/matthewloschiavo)
