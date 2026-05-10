# Hardware-accelerated transcoding (Intel iGPU)

This how-to covers Intel iGPU (Quick Sync / VA-API) passthrough. For
NVIDIA GPU support (Tesla P4 / T4 / A2 / etc.) see the dedicated
section at the bottom and [ADR-0014](../architecture/adr/0014-gpu-strategy-and-time-slicing.md).

## Host requirements

- Intel CPU with iGPU (Haswell or newer; 6th-gen Core or newer for solid Quick Sync)
- `/dev/dri/renderD128` present on the host (`ls -l /dev/dri`)
- The user running docker must be in the `render` and `video` groups (or the container runs as root)

By default this stack is hostPath-free for portability — the Jellyfin
manifests don't mount `/dev/dri`. Enable Intel GPU passthrough only
when needed.

## Compose

The compose file does not ship an Intel-VAAPI profile yet. To enable
Quick Sync on a compose deploy, add this snippet under the `jellyfin`
service in `deploy/compose/docker-compose.yml`:

```yaml
    devices:
      - /dev/dri:/dev/dri
    group_add:
      - "44"   # video
      - "109"  # render — adjust to your distro's render GID
```

Verify the host's render GID with `getent group render | cut -d: -f3`
before pasting (`109` is Ubuntu's; Debian / Fedora differ). Then:

```bash
docker compose -f deploy/compose/docker-compose.yml up -d jellyfin
```

The controller's `JellyfinGpu.check_jellyfin_gpu` detects `/dev/dri`
on the next reconcile tick and writes
`<HardwareAccelerationType>vaapi</HardwareAccelerationType>` plus the
`<VaapiDevice>` setting to `system.xml` automatically. No manual
Jellyfin settings edit needed.

## Kubernetes

On Linux operator hosts, use the toggle script (it patches the Jellyfin
Deployment to add the device mount + render group):

```bash
NAMESPACE=media-stack bash bin/debug/toggle-jellyfin-intel-gpu.sh enable
NAMESPACE=media-stack bash bin/debug/toggle-jellyfin-intel-gpu.sh disable
```

On Windows / macOS (or any environment without bash), apply the same patch directly with `kubectl`:

```bash
kubectl -n media-stack patch deployment jellyfin --type strategic --patch '{
  "spec": {"template": {"spec": {
    "containers": [{
      "name": "jellyfin",
      "resources": {"limits": {"github.com/fuse": 1}},
      "volumeMounts": [{"name": "dri", "mountPath": "/dev/dri"}]
    }],
    "volumes": [{"name": "dri", "hostPath": {"path": "/dev/dri"}}],
    "securityContext": {"supplementalGroups": [44, 109]}
  }}}
}'
```

(Adjust `109` to your node's render GID via `getent group render | cut -d: -f3`.)

This is the Intel equivalent of `deploy/k8s/overlays/nvidia/` — same
basic idea (patch the Jellyfin Deployment to attach GPU resources),
different device + cgroup mechanism.

## Verify

```bash
# Compose
ls -l /dev/dri                           # on the host
docker exec jellyfin ls /dev/dri          # in the container — should match
docker exec jellyfin vainfo               # should list VA-API profiles

# K8s
kubectl -n media-stack exec deploy/jellyfin -- ls /dev/dri
kubectl -n media-stack exec deploy/jellyfin -- vainfo
```

The controller's UI also surfaces this — Operations → GPU transcode
shows `vaapi` as the detected `hw_accel_type` plus a green "Hardware
acceleration available to Jellyfin" badge.

## Jellyfin settings

If you bypass the controller's auto-config (rare), Jellyfin's web UI
exposes the same settings at:

**Dashboard → Playback → Transcoding → Hardware acceleration → Intel QuickSync (VAAPI)**

Set `VA-API device` to `/dev/dri/renderD128` and tick:
- Enable hardware decoding for h264 / hevc / mpeg2 / vc1
- Enable hardware encoding
- Enable tone mapping (HDR → SDR)

## NVIDIA GPU (cross-reference)

If your host has an NVIDIA GPU (Pascal/Turing/Ampere/Ada), the
NVIDIA path is documented separately:

* k8s overlay: `deploy/k8s/overlays/nvidia/` (apply with `kubectl
  patch -n media-stack deployment/jellyfin --type strategic
  --patch-file deploy/k8s/overlays/nvidia/jellyfin-gpu-patch.yaml`).
  Requires the NVIDIA GPU Operator installed in the cluster.
* compose profile: `docker compose --profile nvidia up -d` activates
  the `jellyfin-nvidia` service in `deploy/compose/docker-compose.yml`
  with `runtime: nvidia` + `NVIDIA_DRIVER_CAPABILITIES=compute,video,utility`.
* Strategy + upgrade decisions: [ADR-0014](../architecture/adr/0014-gpu-strategy-and-time-slicing.md).

The controller's `JellyfinGpu.check_jellyfin_gpu` lifecycle method
auto-detects NVIDIA the same way it auto-detects Intel — it sees
`runtimeClassName: nvidia` or `resources.limits."nvidia.com/gpu"` on
the Deployment, sets `<HardwareAccelerationType>nvenc</HardwareAccelerationType>`
in `system.xml`, and bounces Jellyfin via `kubectl rollout restart`.

## Last reviewed

2026-05-10 — added NVIDIA cross-reference + ADR-0014 link, fixed the
`bin/toggle-jellyfin-intel-gpu.sh` path (lives at `bin/debug/`),
expanded compose recipe (the previous revision only documented the
k8s toggle and didn't show how to do it on compose at all), corrected
the render-GID note (was hard-coded to `109` with no callout that it
varies by distro).

---

**Project Steward**
Matthew Loschiavo • [matthewloschiavo.com](https://matthewloschiavo.com) • [mploschiavo@gmail.com](mailto:mploschiavo@gmail.com) • [LinkedIn](https://www.linkedin.com/in/matthewloschiavo)
