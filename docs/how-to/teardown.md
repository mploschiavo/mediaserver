# Teardown and Cleanup

How to completely remove the media stack and all its data. Choose the section that matches your deployment.

> **Warning**: These commands are destructive and irreversible. All service configurations, API keys, library metadata, download history, and media files will be permanently deleted. Export anything you need first.

## Before You Start

### Export Keys and Config (Optional)

Save your API keys and credentials before tearing down:

```bash
# From the controller API (if still running)
curl -s http://localhost:9100/api/keys > media-stack-keys-backup.json

# Or from the dashboard: Security > Export Keys > Copy All

# Download a full config backup
curl -s http://localhost:9100/api/backup > media-stack-backup.json
```

---

## Docker Compose

### Use the teardown script (recommended)

The repo ships a teardown helper at [`bin/ops/teardown-compose.sh`](../../bin/ops/teardown-compose.sh). It handles the cases the manual recipe gets wrong:

* Preserves git-tracked `config/defaults/` (the controller reads bootstrap templates from it on first run — nuking it breaks fresh installs).
* Kills stale `kubectl port-forward` processes that bind compose host ports — a common silent failure when toggling between k8s and compose.
* Three scopes, the safest one is the default:

```bash
# Default — wipes runtime config dirs only, keeps data/torrents and config/defaults/
bin/ops/teardown-compose.sh

# Also wipe data/ (torrents, usenet, transcode)
bin/ops/teardown-compose.sh --with-data

# Wipe everything including media/ (asks for confirmation per dir)
bin/ops/teardown-compose.sh --everything

# Show what would happen, take no action
bin/ops/teardown-compose.sh --dry-run
```

After teardown, fresh-bootstrap with:

```bash
docker compose -f deploy/compose/docker-compose.yml up -d
docker compose -f deploy/compose/docker-compose.yml logs -f media-stack-controller
```

### Manual (Linux / macOS)

```bash
cd docker/

# 1. Stop all containers and remove volumes
docker compose down -v --remove-orphans

# 2. Remove runtime config dirs (KEEP config/defaults/ — it's git-tracked)
find config -mindepth 1 -maxdepth 1 -not -name defaults -exec rm -rf {} +

# 3. Remove download data (NOT /media — that's the library)
rm -rf data/

# 4. (Optional) Remove media library
rm -rf media/

# 5. (Optional) Remove the controller image
docker rmi media-stack-controller:latest 2>/dev/null

# 6. (Optional) Prune unused Docker resources
docker system prune -af --volumes
```

If you overrode paths with environment variables:

```bash
# Check your overrides
echo "CONFIG_ROOT: ${CONFIG_ROOT:-./config}"
echo "MEDIA_ROOT:  ${MEDIA_ROOT:-./media}"
echo "DATA_ROOT:   ${DATA_ROOT:-./data}"

# Then remove those paths
rm -rf "${CONFIG_ROOT:-./config}" "${MEDIA_ROOT:-./media}" "${DATA_ROOT:-./data}"
```

### Windows (PowerShell)

```powershell
cd docker\

# 1. Stop all containers and remove volumes
docker compose down -v --remove-orphans

# 2. Remove config data
Remove-Item -Recurse -Force config\

# 3. Remove media and download data
Remove-Item -Recurse -Force media\, data\

# 4. (Optional) Remove the controller image
docker rmi media-stack-controller:latest 2>$null

# 5. (Optional) Prune unused Docker resources
docker system prune -af --volumes
```

### Windows (WSL2)

If running inside WSL2, use the Linux commands above. If the data is on the Windows filesystem:

```bash
# WSL2 mounts Windows drives at /mnt/c, /mnt/d, etc.
rm -rf /mnt/c/Users/YourName/media-stack/config
rm -rf /mnt/c/Users/YourName/media-stack/media
rm -rf /mnt/c/Users/YourName/media-stack/data
```

---

## Kubernetes

### Single Namespace (Standard)

```bash
NAMESPACE=media-stack

# 1. Delete all resources in the namespace
kubectl delete namespace "$NAMESPACE"

# 2. Wait for namespace termination
kubectl wait --for=delete namespace/"$NAMESPACE" --timeout=120s 2>/dev/null

# 3. Verify clean
kubectl get all -n "$NAMESPACE" 2>&1
# Should show: No resources found
```

### With Persistent Volumes

PersistentVolumes may survive namespace deletion if their reclaim policy is `Retain`:

```bash
# List PVs that were bound to the media-stack namespace
kubectl get pv | grep media-stack

# Delete them manually
kubectl delete pv <pv-name>

# Or delete all PVs with the media-stack label
kubectl delete pv -l app.kubernetes.io/part-of=media-stack
```

### With Kustomize Overlays

```bash
# If you deployed with kustomize, reverse it:
kubectl delete -k k8s/all/
# Or for a specific overlay:
kubectl delete -k k8s/all/
```

### MicroK8s Specific

```bash
# If using MicroK8s storage
microk8s kubectl delete namespace media-stack

# Clean up local storage
sudo rm -rf /var/snap/microk8s/common/default-storage/media-stack-*
```

---

## Verify Clean State

### Docker Compose

```bash
# No media-stack containers
docker ps -a --filter "label=com.docker.compose.project=media-stack" --format '{{.Names}}'

# No media-stack volumes
docker volume ls --filter "label=com.docker.compose.project=media-stack"

# No media-stack networks
docker network ls --filter "label=com.docker.compose.project=media-stack"

# Config directory gone
ls -la config/ 2>&1  # Should show "No such file or directory"
```

### Kubernetes

```bash
# Namespace gone
kubectl get namespace media-stack 2>&1
# Should show: namespaces "media-stack" not found

# No lingering PVs
kubectl get pv | grep media-stack
# Should show nothing
```

---

## Partial Cleanup

### Reset Config Only (Keep Media)

Useful for starting fresh without re-downloading content:

```bash
# Docker Compose
cd docker/
docker compose down -v --remove-orphans
rm -rf config/
# Keep media/ and data/ intact
docker compose up -d

# Kubernetes
kubectl delete configmap,secret,job -n media-stack --all
kubectl rollout restart deployment -n media-stack
```

### Remove a Single Service

```bash
# Docker Compose — stop and remove one service
docker compose rm -sf sonarr
rm -rf config/sonarr/

# Kubernetes — delete one deployment
kubectl delete deployment sonarr -n media-stack
kubectl delete pvc sonarr-config -n media-stack
```

---

## Re-deploying After Teardown

After a full teardown, redeploy from scratch:

```bash
# Docker Compose
cd docker/
docker compose --profile standard up -d

# Kubernetes
kubectl apply -k k8s/all/
```

The controller will run the full bootstrap pipeline automatically on first start — discovering API keys, configuring download clients, setting up indexers, and wiring all integrations.

See [GETTING-STARTED.md](../GETTING-STARTED.md) for the full setup guide.
