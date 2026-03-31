# Screenshots and Runtime Evidence

This directory stores reproducible runtime artifacts captured from a live namespace.

## Folder Structure

- `docs/screenshots/apps/`
  - Playwright-captured UI screenshots for ingress-exposed apps.
  - See `docs/screenshots/apps/README.md`.
- `docs/screenshots/cluster/`
  - Timestamped terminal snapshots (`kubectl` outputs for pods/services/ingress/PVC/events).
  - See `docs/screenshots/cluster/README.md`.

## Capture UI Screenshots

```bash
bash scripts/run-playwright-screenshots.sh <NODE_IP> [NAMESPACE] [OUT_DIR]
```

Example:

```bash
bash scripts/run-playwright-screenshots.sh 192.168.1.60 media-stack
```

This runs `tests/e2e/playwright/tests/screenshot-capture.spec.ts` and writes one PNG per app host.
The capture flow now attempts app login first (using credentials from env/Kubernetes secret),
so screenshots reflect authenticated dashboards rather than pre-login shells.

## Capture Kubernetes Terminal Snapshots

```bash
bash scripts/capture-k8s-snapshots.sh [NAMESPACE] [OUT_DIR]
```

Example:

```bash
bash scripts/capture-k8s-snapshots.sh media-stack
```

This writes timestamped `.txt` evidence files for:
- namespaces, nodes
- pods, services, ingress, PVCs, deployments, jobs
- namespace events
- ingress describe output

## Recommended Baseline Set

- homepage
- jellyfin
- jellyseerr
- sonarr/radarr
- qbittorrent/sabnzbd
- maintainerr
- one full cluster snapshot batch

For architecture visuals, see `docs/diagrams/`.
