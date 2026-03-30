#!/usr/bin/env bash
set -euo pipefail

NAMESPACE="${NAMESPACE:-media-stack}"
DRY_RUN=0
SCALE_WORKERS_TO_ZERO="${SCALE_WORKERS_TO_ZERO:-0}"

usage() {
  cat <<'EOF'
Usage:
  scripts/apply-scale-policy.sh [--dry-run]

Description:
  Enforces scale policy:
  - Core interactive apps are kept at replicas >= 1
  - Optional worker-like apps can be scaled to 0 when SCALE_WORKERS_TO_ZERO=1

Environment variables:
  NAMESPACE             (default: media-stack)
  SCALE_WORKERS_TO_ZERO (default: 0)
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run) DRY_RUN=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "[ERR] Unknown arg: $1" >&2; exit 1 ;;
  esac
done

if command -v microk8s >/dev/null 2>&1; then
  KUBECTL=(microk8s kubectl)
elif command -v kubectl >/dev/null 2>&1; then
  KUBECTL=(kubectl)
else
  echo "[ERR] Neither microk8s nor kubectl is available in PATH." >&2
  exit 1
fi

core_apps=(jellyfin jellyseerr prowlarr qbittorrent sonarr radarr lidarr readarr bazarr)
worker_apps=(unpackerr flaresolverr)

scale_deploy() {
  local deploy="$1"
  local replicas="$2"
  if ! "${KUBECTL[@]}" -n "$NAMESPACE" get deploy "$deploy" >/dev/null 2>&1; then
    return 0
  fi
  if [[ "$DRY_RUN" == "1" ]]; then
    echo "[DRY] scale deploy/$deploy -> $replicas"
  else
    "${KUBECTL[@]}" -n "$NAMESPACE" scale deploy "$deploy" --replicas="$replicas" >/dev/null
    echo "[OK] scale deploy/$deploy -> $replicas"
  fi
}

for app in "${core_apps[@]}"; do
  if "${KUBECTL[@]}" -n "$NAMESPACE" get deploy "$app" >/dev/null 2>&1; then
    replicas="$("${KUBECTL[@]}" -n "$NAMESPACE" get deploy "$app" -o jsonpath='{.spec.replicas}' 2>/dev/null || echo 1)"
    if [[ -z "$replicas" || "$replicas" == "0" ]]; then
      scale_deploy "$app" 1
    fi
  fi
done

if [[ "$SCALE_WORKERS_TO_ZERO" == "1" ]]; then
  for app in "${worker_apps[@]}"; do
    scale_deploy "$app" 0
  done
fi

echo "[OK] Scale policy applied."
