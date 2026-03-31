# Intel Transcoding

## Host requirements
- Intel iGPU
- /dev/dri present
- user in render/video groups

By default, this stack is hostPath-free for portability.
Enable Intel GPU pass-through only when needed:
```bash
NAMESPACE=media-stack bash scripts/toggle-jellyfin-intel-gpu.sh enable
```
Disable it again:
```bash
NAMESPACE=media-stack bash scripts/toggle-jellyfin-intel-gpu.sh disable
```

## Check
```bash
ls -l /dev/dri
groups
docker exec -it jellyfin ls /dev/dri
```

## Jellyfin settings
Enable Intel Quick Sync in Playback / Transcoding.

---

**Project Steward**
Matthew Loschiavo • [matthewloschiavo.com](https://matthewloschiavo.com) • [mploschiavo@gmail.com](mailto:mploschiavo@gmail.com) • [LinkedIn](https://www.linkedin.com/in/matthewloschiavo)
