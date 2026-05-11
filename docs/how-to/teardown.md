# Teardown and cleanup

How to remove the media stack and its data. Choose the section that matches your deployment.

> **Warning**: These commands are destructive and mostly irreversible. Service configurations, API keys, library metadata, download history, and (optionally) media files are deleted. Export anything you want to keep first.

> **Repo paths**: The compose file lives at `deploy/compose/docker-compose.yml`; k8s manifests live under `deploy/k8s/`. Older revisions of this guide referenced `docker/` and `k8s/` — those paths don't exist in this tree.

## Before you start: export keys + config

If the controller is still running, export keys and a full config snapshot first:

```bash
# Full config snapshot (recommended)
curl -s http://localhost:9100/api/backup > media-stack-backup-$(date +%Y%m%d).json

# Per-service API keys
curl -s http://localhost:9100/api/keys > media-stack-keys-backup.json
```

From the dashboard: **Security → Export keys → Copy all**.

---

## Docker Compose

### Use the teardown workflow (recommended)

The repo ships a teardown CLI at
`media_stack.cli.commands.teardown_stack_main`. It handles the
cases the manual recipe gets wrong:

* Preserves the git-tracked `config/defaults/` directory (the
  controller reads bootstrap templates from there on first run; nuking
  it breaks fresh installs).
* Kills stale `kubectl port-forward` processes that bind compose host
  ports — a silent failure when toggling between k8s and compose
  setups on the same host.
* Three scopes; the default is the safest one.

**Required:** the `media-stack-teardown` console-script needs to be
on your PATH. That happens automatically after the [first-time
setup](deployment.md#first-time-setup) (`python -m venv .venv` +
`pip install -e .` + activating the venv) — and works on Windows,
macOS, and Linux equally. The wipe operates on paths configured
in your bootstrap profile, not the current working directory, so
you can run it from anywhere once the venv is active.

```bash
# Default — wipes service config dirs only; keeps data/torrents
# and config/defaults/. `--dry-run` is the default, no harm done.
media-stack-teardown --target compose --scope config --dry-run

# Same plan, actually execute (still preserves media/ and data/):
media-stack-teardown --target compose --scope config --execute --yes

# Also wipe data/ (torrents, usenet, transcode):
media-stack-teardown --target compose --scope data --execute --yes

# Wipe EVERYTHING including media/ (your library!) — destructive:
media-stack-teardown --target compose --scope everything --execute --yes
```

If you haven't done `pip install -e .` and don't want to,
the module form works as a fallback (requires you `cd` to the
repo root so the import path resolves):

```bash
cd /path/to/media-automation-stack
.venv/bin/python -m media_stack.cli.commands.teardown_stack_main \
    --target compose --scope config --execute --yes
```

> **Scope reminder:** `--scope everything` deletes `media/` too —
> your entire library. If you want to redeploy with the same media
> intact, use `--scope config` (or `--scope data` if you also want
> to drop in-flight downloads / transcode cache).

Dry-run previews every action without touching disk, including
per-path size estimates. The 2026-05-09 operator session ran the
workflow successfully on a real cluster — all 19 service config
directories cleaned, ~5 GiB freed, fresh `docker compose up -d` came
up clean afterwards.

After teardown, fresh-bootstrap with:

```bash
docker compose -f deploy/compose/docker-compose.yml up -d
docker compose -f deploy/compose/docker-compose.yml \
    logs -f media-stack-controller
```

### Manual (Linux / macOS)

If you can't run the workflow (Python not installed, or you're
debugging it):

```bash
# 1. Stop all containers and remove the compose network + named volumes
docker compose -f deploy/compose/docker-compose.yml down -v --remove-orphans

# 2. Remove runtime config dirs (KEEP config/defaults/ — it's git-tracked)
find config -mindepth 1 -maxdepth 1 ! -name defaults -exec rm -rf {} +

# 3. Remove download/data dirs (NOT /media — that's the library)
rm -rf data/

# 4. (Optional) Remove the media library
rm -rf media/

# 5. (Optional) Remove the controller + UI images
docker rmi harbor.iomio.io/library/media-stack-controller:latest \
           harbor.iomio.io/library/media-stack-ui:latest 2>/dev/null || true

# 6. (Optional) Prune unused Docker resources
docker system prune -af --volumes
```

If you overrode paths with environment variables:

```bash
# Show your overrides
echo "CONFIG_ROOT: ${CONFIG_ROOT:-./config}"
echo "MEDIA_ROOT:  ${MEDIA_ROOT:-./media}"
echo "DATA_ROOT:   ${DATA_ROOT:-./data}"

# Then remove those paths
rm -rf "${CONFIG_ROOT:-./config}" "${MEDIA_ROOT:-./media}" "${DATA_ROOT:-./data}"
```

### Windows (PowerShell)

```powershell
# 1. Stop all containers and remove volumes
docker compose -f deploy\compose\docker-compose.yml down -v --remove-orphans

# 2. Remove config data (keep defaults\)
Get-ChildItem config -Directory | Where-Object Name -ne 'defaults' | Remove-Item -Recurse -Force

# 3. Remove media and download data
Remove-Item -Recurse -Force media\, data\

# 4. (Optional) Remove the controller + UI images
docker rmi harbor.iomio.io/library/media-stack-controller:latest, harbor.iomio.io/library/media-stack-ui:latest 2>$null

# 5. (Optional) Prune unused Docker resources
docker system prune -af --volumes
```

### Windows (WSL2)

If running inside WSL2, use the Linux commands above. If the data
lives on the Windows filesystem:

```bash
# WSL2 mounts Windows drives at /mnt/c, /mnt/d, etc.
rm -rf /mnt/c/Users/YourName/media-stack/config
rm -rf /mnt/c/Users/YourName/media-stack/media
rm -rf /mnt/c/Users/YourName/media-stack/data
```

---

## Kubernetes

### Single namespace (standard)

```bash
NAMESPACE=media-stack

# 1. Delete every resource in the namespace
kubectl delete namespace "$NAMESPACE"

# 2. Wait for namespace termination
kubectl wait --for=delete namespace/"$NAMESPACE" --timeout=120s 2>/dev/null

# 3. Verify
kubectl get all -n "$NAMESPACE" 2>&1
# Expected: No resources found
```

### With PersistentVolumes (Retain reclaim policy)

`PersistentVolumes` may survive namespace deletion if their reclaim
policy is `Retain`:

```bash
# List PVs that were bound to the media-stack namespace
kubectl get pv | grep media-stack

# Delete them manually
kubectl delete pv <pv-name>

# Or, by label:
kubectl delete pv -l app.kubernetes.io/part-of=media-stack
```

### With kustomize overlays

If you deployed via kustomize, reverse the apply:

```bash
# Standard profile teardown:
kubectl delete -k deploy/k8s/profiles/standard

# Or the "everything" overlay:
kubectl delete -k deploy/k8s/all
```

The `deploy/k8s/overlays/nvidia/` GPU overlay is patch-only — it
doesn't introduce new resources, so there's nothing extra to delete
on teardown. The patches disappear when the underlying Deployment
is deleted.

### MicroK8s

```bash
# Use microk8s' bundled kubectl
microk8s kubectl delete namespace media-stack

# Clean up local storage if you used the host-path provisioner
sudo rm -rf /var/snap/microk8s/common/default-storage/media-stack-*
```

### Workflow CLI on k8s

The same teardown CLI handles k8s too — same console-script, just
`--target k8s` instead of `--target compose`:

```bash
media-stack-teardown --target k8s --scope config --execute --yes

# Production execute requires an explicit namespace confirm token:
media-stack-teardown --target k8s --environment prod --execute \
    --confirm-token "TEARDOWN media-stack"
```

(Same `cd /path/to/media-automation-stack` + `.venv/bin/python -m ...`
fallback applies as in the compose section if you haven't done
`pip install -e .`.)

---

## Verify clean state

### Docker Compose

```bash
# No media-stack containers
docker ps -a --filter 'label=com.docker.compose.project=media-stack' --format '{{.Names}}'

# No media-stack volumes
docker volume ls --filter 'label=com.docker.compose.project=media-stack'

# No media-stack networks
docker network ls --filter 'label=com.docker.compose.project=media-stack'

# Service config dirs gone (only defaults/ remains)
ls -la config/
```

### Kubernetes

```bash
# Namespace gone
kubectl get namespace media-stack 2>&1
# Expected: namespaces "media-stack" not found

# No lingering PVs
kubectl get pv | grep media-stack
# Expected: no output
```

---

## Partial cleanup

### Reset config only (keep media library)

Useful for starting fresh without re-downloading content:

```bash
# Docker Compose
docker compose -f deploy/compose/docker-compose.yml down -v --remove-orphans
find config -mindepth 1 -maxdepth 1 ! -name defaults -exec rm -rf {} +
# Keep media/ and data/ intact
docker compose -f deploy/compose/docker-compose.yml up -d

# Kubernetes
kubectl delete configmap,secret,job -n media-stack --all
kubectl rollout restart deployment -n media-stack
```

### Remove a single service

```bash
# Docker Compose — stop and remove one service
docker compose -f deploy/compose/docker-compose.yml rm -sf sonarr
rm -rf "${CONFIG_ROOT:-./config}/sonarr/"

# Kubernetes — delete one deployment + its PVC
kubectl delete deployment sonarr -n media-stack
kubectl delete pvc media-stack-config-sonarr -n media-stack
```

---

## GPU-specific teardown notes

If you applied the NVIDIA overlay (`deploy/k8s/overlays/nvidia/`):

* **Standard namespace teardown removes everything** including the
  GPU-patched Jellyfin Deployment. The GPU operator's daemonset
  pods (in the `gpu-operator-resources` namespace) survive and
  are unaffected — they're cluster-scoped infrastructure.
* On a redeploy after teardown, you must **re-apply the GPU patch**
  because it lives outside the base manifest:

```bash
kubectl patch -n media-stack deployment/jellyfin \
    --type strategic \
    --patch-file deploy/k8s/overlays/nvidia/jellyfin-gpu-patch.yaml
```

See [ADR-0014](../architecture/adr/0014-gpu-strategy-and-time-slicing.md)
for the GPU strategy and overlay design.

---

## Re-deploying after teardown

After a full teardown, redeploy from scratch:

```bash
# Docker Compose
docker compose -f deploy/compose/docker-compose.yml up -d

# Kubernetes (one of the profiles)
kubectl apply -k deploy/k8s/profiles/standard

# If you have a GPU and the operator installed:
kubectl patch -n media-stack deployment/jellyfin \
    --type strategic \
    --patch-file deploy/k8s/overlays/nvidia/jellyfin-gpu-patch.yaml
```

The controller runs the bootstrap pipeline on first start — discovering
API keys, configuring download clients, setting up indexers, wiring
integrations.

See the [deployment how-to](deployment.md) for the full setup
sequence, including pre-bootstrap profile choice.

---

## Last reviewed

2026-05-10 (third pass) — swapped the canonical invocation to the
`media-stack-teardown` console-script entry point (declared in
`pyproject.toml`'s `[project.scripts]`). After `pip install -e .`
this name is on PATH on Windows, macOS, and Linux equally, and
no `cd` to the repo root is needed because the entry-point resolves
through Python's installed package metadata, not relative imports.
The `cd repo-root + .venv/bin/python -m media_stack.cli.commands.teardown_stack_main`
form is kept as a fallback when the editable-install hasn't been
run. See [First-time setup](deployment.md#first-time-setup) for the
one-time `pip install -e .` step.

2026-05-10 (second pass) — fixed the workflow CLI invocation:
must be run from the repo root with `.venv/bin/python` so the
`media_stack` package resolves. Earlier same-day revision claimed
a `bin/media-stack-teardown` shell wrapper existed; it does not
in this repo. Removed that reference and made the venv-python
prefix explicit on every invocation example.

Prior 2026-05-10 (first pass) — refreshed paths (`deploy/compose/`
+ `deploy/k8s/`), image registry prefix (`harbor.iomio.io/library/`),
added the GPU overlay teardown section. Previous revision
(2026-04-30) had `cd docker/` and `k8s/all/` which no longer exist
in this tree.
