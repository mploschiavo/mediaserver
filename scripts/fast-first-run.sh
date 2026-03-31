#!/usr/bin/env bash
set -euo pipefail

NODE_IP="${1:-}"
NAMESPACE="${NAMESPACE:-media-stack}"

usage() {
  cat <<'EOF'
Usage:
  scripts/fast-first-run.sh <NODE_IP>

Description:
  Prints the fastest first-run wiring flow for media-stack:
  - service URLs
  - recommended setup order
  - one-liner commands to fetch Arr/Prowlarr API keys from Kubernetes

Environment variables:
  NAMESPACE   (default: media-stack)
EOF
}

if [[ -z "$NODE_IP" || "$NODE_IP" == "-h" || "$NODE_IP" == "--help" ]]; then
  usage
  exit 0
fi

if command -v microk8s >/dev/null 2>&1; then
  KUBECTL=(microk8s kubectl)
elif command -v kubectl >/dev/null 2>&1; then
  KUBECTL=(kubectl)
else
  printf '[WARN] Neither microk8s nor kubectl found. API key fetch commands will be shown but not tested.\n' >&2
  KUBECTL=(kubectl)
fi

cat <<EOF
Fast first-run for media-stack
==============================

Use these URLs in your browser:
- Homepage:   http://homepage.local
- Jellyfin:   http://jellyfin.local
- Jellyseerr: http://jellyseerr.local
- Prowlarr:   http://prowlarr.local
- qBittorrent:http://qbittorrent.local

If .local does not resolve on your device, add hosts entry:
$NODE_IP homepage.local jellyfin.local jellyseerr.local sonarr.local radarr.local lidarr.local readarr.local bazarr.local prowlarr.local qbittorrent.local sabnzbd.local tautulli.local

Recommended fastest order (about 15-25 minutes):
1) Full zero-to-usable run (recommended):
   - bash scripts/install.sh --profile full --node-ip $NODE_IP
   - bash scripts/rebuild-and-bootstrap.sh $NODE_IP
2) Run full bootstrap automation (if namespace already exists):
   - bash scripts/set-qbit-secret.sh   # defaults to admin/media-stack-admin
   - bash scripts/ensure-jellyfin-bootstrap.sh   # auto-discovers/updates Jellyfin API key in secret
   - bash scripts/bootstrap-all.sh
   - (this wires Arr + Prowlarr + qBittorrent clients/categories + Jellyseerr Sonarr/Radarr + Unpackerr keys)
3) qBittorrent:
   - verify login with secret credentials
   - categories are auto-managed: tv, movies, music, books
4) Jellyfin:
   - startup wizard/admin are bootstrap-managed from stack secret
   - add/verify libraries under /media/*
5) Prowlarr:
   - add indexers (only trusted/permitted sources)
   - app connections are bootstrap-managed for Sonarr/Radarr/Lidarr/Readarr
6) Sonarr/Radarr/Lidarr/Readarr:
   - root folders are bootstrap-managed (/media/tv, /media/movies, /media/music, /media/books)
   - qBittorrent download client is bootstrap-managed (http://qbittorrent:8080)
7) Jellyseerr:
   - Sonarr + Radarr + Jellyfin are bootstrap-configured
   - local admin account is seeded from STACK_ADMIN credentials
8) Bazarr:
   - set subtitle providers/languages
9) Optional apps:
   - SABnzbd / Plex / Tautulli / FlareSolverr as needed
10) Unpackerr:
   - enable only after Arr API keys are configured

API key helpers (run on this host):
EOF

print_api_key_cmd() {
  local app="$1"
  local port="$2"
  cat <<EOF

$app API key:
  ${KUBECTL[*]} -n $NAMESPACE exec deploy/$app -- sh -c "sed -n 's:.*<ApiKey>\\(.*\\)</ApiKey>.*:\\1:p' /config/config.xml | head -n1"
  URL: http://$app:$port
EOF
}

print_api_key_cmd sonarr 8989
print_api_key_cmd radarr 7878
print_api_key_cmd lidarr 8686
print_api_key_cmd readarr 8787
print_api_key_cmd prowlarr 9696

cat <<'EOF'

Optional sanity checks:
  bash scripts/microk8s-smoke-test.sh <NODE_IP>
  microk8s kubectl -n media-stack get pods
EOF
