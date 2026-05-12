# Upgrades

Media Stack ships as a pinned controller image (`harbor.iomio.io/public/media-stack-controller:vX.Y.Z`) plus a separately versioned UI image (`harbor.iomio.io/public/media-stack-ui:vX.Y.Z`). Current versions live in `VERSION` (controller) and `VERSION-UI` (UI) at the repo root. The app images (Jellyfin, Sonarr, etc.) come from upstream registries and follow their own release cadence.

> The CLI invocations below assume `media-stack-*` is on PATH. If you're
> running on a fresh checkout, do the [First-time setup](deployment.md#first-time-setup)
> first (`python -m venv .venv` + `pip install -e .`). The module form
> `.venv/bin/python -m media_stack.cli.commands.X_main` also works.

## Routine upgrade

The controller + UI versions are pinned in:

* `deploy/compose/docker-compose.yml` + `deploy/dist/docker-compose.yml` (compose)
* `deploy/k8s/kustomization.yaml` + per-profile `deploy/k8s/profiles/<name>/kustomization.yaml` (k8s)
* `contracts/api/openapi.yaml` (controller version literal in the OpenAPI metadata)

All four are kept in sync by the version-pin ratchet (`tests/unit/architecture/test_version_pin_consistency.py`) so they can't drift silently.

### Docker Compose

```bash
git pull                                                                  # pick up the new pins
docker compose -f deploy/compose/docker-compose.yml pull                  # fetch new images
docker compose -f deploy/compose/docker-compose.yml up -d                 # recreate changed containers
```

### Kubernetes

Two paths depending on whether you want declarative-from-git (kustomize) or imperative-from-now (kubectl set image). Both are supported.

**Declarative (kustomize, recommended):**

```bash
git pull                                                                  # pick up the new image pins
kubectl apply -k deploy/k8s/profiles/standard                             # or whichever profile you use
kubectl rollout status -n media-stack deploy/media-stack-controller --timeout=120s
kubectl rollout status -n media-stack deploy/media-stack-ui --timeout=120s
```

**Imperative (kubectl set image, fast hot-fix):**

```bash
NEW_CTRL_VERSION="$(cat VERSION)"
NEW_UI_VERSION="$(cat VERSION-UI)"
kubectl set image -n media-stack deployment/media-stack-controller \
    controller=harbor.iomio.io/public/media-stack-controller:v${NEW_CTRL_VERSION}
kubectl set image -n media-stack deployment/media-stack-ui \
    ui=harbor.iomio.io/public/media-stack-ui:v${NEW_UI_VERSION}
kubectl rollout status -n media-stack deploy/media-stack-controller --timeout=120s
```

The imperative form is useful when you want to roll a single image without re-applying the whole kustomize tree (e.g. mid-development, or to avoid touching the GPU overlay's `kubectl patch` state).

The controller's bootstrap re-runs idempotently after any restart, so any new promises shipped in the upgrade will be applied automatically.

## Verifying after upgrade

Cross-platform (Windows / macOS / Linux):

```bash
media-stack-verify          # full promises probe (slower)
```

Linux convenience: `bash bin/test/verify-stack.sh` for a quick edge-only smoke
test (envoy + DNS + TLS handshake — Linux-only because it pokes Docker via
the daemon socket and resolves `/etc/hosts`).

The dashboard's Routes tab is the fastest visual check — green across all rows means the gateway, auth, and per-service routing all survived the upgrade.

## Backups before upgrading

For non-trivial upgrades (controller minor version bumps, app major version bumps, or anything labelled "breaking"):

```bash
# Cross-platform
media-stack-backup
# Linux convenience: bash bin/utils/backup-stack.sh
```

Restore if needed:

```bash
media-stack-restore ./backups/media-stack-backup-<ts>.tar.gz
# Linux convenience: bash bin/utils/restore-stack.sh <path>
```

## Rolling back the controller

If a new controller version misbehaves, pin the previous tag in `VERSION` (and let the version-pin ratchet propagate it):

```bash
echo "1.0.<previous>" > VERSION
# Then bump the same in deploy/compose/docker-compose.yml,
# deploy/k8s/profiles/*/kustomization.yaml, and contracts/api/openapi.yaml
# (the test_version_pin_consistency ratchet enforces these stay in sync).
docker compose -f deploy/compose/docker-compose.yml up -d
```

For k8s, the imperative form is faster for rollback:

```bash
kubectl set image -n media-stack deployment/media-stack-controller \
    controller=harbor.iomio.io/public/media-stack-controller:v1.0.<previous>
```

Bootstrap state is forward / backward compatible across patch versions in the same minor (`v1.0.X`). Major-minor downgrades may require a restore from backup if the controller wrote schema changes to `${CONFIG_ROOT}/.controller/`.

## Upgrade-induced breakage

If a promise fails after upgrade, that's the meta-ratchet doing its job:

1. Check `docker logs media-stack-controller --tail 200` for the failing job.
2. Re-run just that job: `curl -X POST http://localhost:9100/actions/<job-name>`.
3. If it still fails, the upgrade probably needs a profile change — see the changelog entry for that version.

For anything else, [open a bug](../../CONTRIBUTING.md#reporting-a-bug) with the controller version and the failing probe.

---

**Project Steward**
Matthew Loschiavo • [matthewloschiavo.com](https://matthewloschiavo.com) • [mploschiavo@gmail.com](mailto:mploschiavo@gmail.com) • [LinkedIn](https://www.linkedin.com/in/matthewloschiavo)
