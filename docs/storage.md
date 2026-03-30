# Storage Model

## Default Strategy (Recommended)

The stack is now PVC-first.
Application deployments no longer hardcode host paths for storage.

Source-of-truth storage manifest:
- `k8s/storage-pvc.yaml`

This keeps deployments portable across:
- MicroK8s
- AKS
- other CSI-backed Kubernetes clusters

## Claims Used by the Stack

Config/state claims (per app):
- `media-stack-config-jellyfin`
- `media-stack-config-jellyseerr`
- `media-stack-config-prowlarr`
- `media-stack-config-qbittorrent`
- `media-stack-config-sonarr`
- `media-stack-config-radarr`
- `media-stack-config-lidarr`
- `media-stack-config-readarr`
- `media-stack-config-bazarr`
- `media-stack-config-sabnzbd`
- `media-stack-config-plex`
- `media-stack-config-tautulli`
- `media-stack-config-homepage`
- `media-stack-config-maintainerr`
- `media-stack-config-jellyfin-auto-collections`

Shared content claims:
- `media-stack-data-torrents`
- `media-stack-data-usenet`
- `media-stack-data-transcode`
- `media-stack-media`

## StorageClass Selection

By default, `k8s/storage-pvc.yaml` omits `storageClassName`, so PVCs use the cluster default StorageClass.

Recommended options:
- Set an appropriate default StorageClass in your cluster.
- Pass a deploy-time override (no file edits):
```bash
bash scripts/install.sh --profile full --storage-mode dynamic-pvc --storage-class <STORAGE_CLASS_NAME> --node-ip <NODE_IP>
```
- Or pin claims to a class by editing `k8s/storage-pvc.yaml`.
- Or use `k8s/pvc-storage.example.yaml` as a class-pinned template.
- Or use helper script:
```bash
bash scripts/set-pvc-storage-class.sh <STORAGE_CLASS_NAME>
```

## MicroK8s Custom pvDir (SSD Path)

MicroK8s supports custom hostpath `pvDir` classes.

Use the provided example:
```bash
microk8s kubectl apply -f k8s/storageclass-microk8s.example.yaml
microk8s kubectl get storageclass media-stack-hostpath
```

Then either:
1. Make that class default, or
2. Set `storageClassName: media-stack-hostpath` in `k8s/storage-pvc.yaml`.

## AKS Example

Use Azure Files CSI (RWX-friendly) when you want shared volumes across pods/nodes.

Example class:
```bash
kubectl apply -f k8s/storageclass-aks-azurefile.example.yaml
```

Then either:
1. Make it the default class, or
2. Set `storageClassName: media-stack-azurefile` in `k8s/storage-pvc.yaml`.

Why RWX matters on multi-node clusters:
- bootstrap/reconcile jobs may mount multiple app config PVCs at once
- RWX-backed claims avoid cross-node attach contention common with RWO-only classes

## Core Principle

Download clients write to transient paths:
- `/data/torrents`
- `/data/usenet`

Arr apps import and organize into canonical library paths under `/media`.

## Hardlink-Friendly Behavior

To avoid duplicate storage usage, keep Arr media-management defaults in hardlink-friendly mode when filesystem semantics allow it.

## Legacy HostPath Mode (Optional)

If you intentionally want host directory prep semantics, use:
```bash
bash scripts/install.sh --profile full --storage-mode legacy-hostpath --node-ip <NODE_IP>
```

This enables host folder prep helpers (`prepare-host.sh`, `fix-media-perms.sh`) using `/srv/media-stack` by default.

## Backup/Restore

```bash
bash scripts/backup-stack.sh
bash scripts/restore-stack.sh ./backups/media-stack-backup-YYYYMMDD-HHMMSS.tar.gz
```
