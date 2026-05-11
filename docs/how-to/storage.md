# Storage Model

## Default Strategy (Recommended)

The stack is now PVC-first.
Application deployments no longer hardcode host paths for storage.

Source-of-truth storage manifest:
- `deploy/k8s/base/storage/storage-pvc.yaml`

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

By default, `deploy/k8s/base/storage/storage-pvc.yaml` omits `storageClassName`, so PVCs use the cluster default StorageClass.

Recommended options:
- Set an appropriate default StorageClass in your cluster.
- Pass a deploy-time override (no file edits, cross-platform):
```bash
.venv/bin/python -m media_stack.cli.commands.install_main \
    --profile full --storage-mode dynamic-pvc --storage-class <STORAGE_CLASS_NAME> --node-ip <NODE_IP>
```
- Or pin claims to a class by editing `deploy/k8s/base/storage/storage-pvc.yaml`.
- Or use `deploy/k8s/pvc-storage.example.yaml` as a class-pinned template.
- Or run the helper CLI (cross-platform):
```bash
.venv/bin/python -m media_stack.cli.commands.set_pvc_storage_class_main <STORAGE_CLASS_NAME>
```
Linux convenience wrapper: `bash bin/utils/set-pvc-storage-class.sh ...`.

## MicroK8s Custom pvDir (SSD Path)

MicroK8s supports custom hostpath `pvDir` classes.

Use the provided example:
```bash
microk8s kubectl apply -f deploy/k8s/storageclass-microk8s.example.yaml
microk8s kubectl get storageclass media-stack-hostpath
```

Then either:
1. Make that class default, or
2. Set `storageClassName: media-stack-hostpath` in `deploy/k8s/base/storage/storage-pvc.yaml`.

## AKS Example

Use Azure Files CSI (RWX-friendly) when you want shared volumes across pods/nodes.

Example class:
```bash
kubectl apply -f deploy/k8s/storageclass-aks-azurefile.example.yaml
```

Then either:
1. Make it the default class, or
2. Set `storageClassName: media-stack-azurefile` in `deploy/k8s/base/storage/storage-pvc.yaml`.

Why RWX matters on multi-node clusters:
- contracts/reconcile jobs may mount multiple app config PVCs at once
- RWX-backed claims avoid cross-node attach contention common with RWO-only classes

## Core Principle

Download clients write to transient paths:
- `/data/torrents`
- `/data/usenet`

Arr apps import and organize into canonical library paths under `/media`.

## Hardlink-Friendly Behavior

To avoid duplicate storage usage, keep Arr media-management defaults in hardlink-friendly mode when filesystem semantics allow it.

## Storage Mode

This stack now runs in `dynamic-pvc` mode only. Storage behavior is driven by
PVCs + StorageClass and is portable across clusters.

## Backup/Restore

Cross-platform (Windows / macOS / Linux):

```bash
.venv/bin/python -m media_stack.cli.commands.backup_stack_main
.venv/bin/python -m media_stack.cli.commands.restore_stack_main ./backups/media-stack-backup-YYYYMMDD-HHMMSS.tar.gz
```

Linux convenience wrappers: `bash bin/utils/backup-stack.sh`, `bash bin/utils/restore-stack.sh`.

---

**Project Steward**
Matthew Loschiavo • [matthewloschiavo.com](https://matthewloschiavo.com) • [mploschiavo@gmail.com](mailto:mploschiavo@gmail.com) • [LinkedIn](https://www.linkedin.com/in/matthewloschiavo)
